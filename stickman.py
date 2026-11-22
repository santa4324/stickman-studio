#!/usr/bin/env python3
"""Turn a pasted script into a finished stickman explainer.

This is the engine behind studio.py. It takes plain sentences, narrates them
locally with Piper, picks a scene for each sentence from what the words are
about, writes the B1 project JSON, and renders it through the installed
directing-stickman-videos runtime.

    python3 stickman.py --script "Your AI wrote the app. It also wrote the holes." --out out/run
"""

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import stickman_art

ROOT = Path(__file__).resolve().parent
SKILL = ROOT / ".agents" / "skills" / "directing-stickman-videos"
AUTOPILOT = SKILL / "scripts" / "autopilot.mjs"
VOICE = ROOT / "data" / "voices" / "en_US-ryan-high.onnx"
ART_SRC = ROOT / "data" / "stickman" / "art"
BED = ROOT / "data" / "stickman" / "bed"

LEAD, GAP, TAIL = 0.35, 0.22, 0.95
MAX_CUE = 28          # characters that fit one portrait caption line at phone size
WIDTH, HEIGHT = 720, 1280


# ---------------------------------------------------------------- script text

def sentences(text):
    """Split pasted text into speakable sentences, dropping markdown noise."""
    text = re.sub(r"[#*_>`]", " ", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = " ".join(text.split())
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = []
    for part in parts:
        part = part.strip(" -")
        if not part:
            continue
        if part[-1] not in ".!?":
            part += "."
        out.append(part)
    return out


def cues_for(sentence, start, end):
    """Break one sentence into balanced caption cues, never one dangling word."""
    words = sentence.split()
    if len(sentence) <= MAX_CUE:
        chunks = [sentence]
    else:
        count = max(2, -(-len(sentence) // MAX_CUE))
        target = len(words) / count
        chunks, current = [], []
        for index, word in enumerate(words):
            current.append(word)
            remaining = len(words) - index - 1
            at_comma = word.endswith((",", ";", ":"))
            if (len(current) >= target and not at_comma) or \
               len(" ".join(current)) >= MAX_CUE or \
               (at_comma and len(current) >= target * 0.6):
                if remaining:
                    chunks.append(" ".join(current))
                    current = []
        if current:
            chunks.append(" ".join(current))
        # A last chunk of one word reads better folded into the one before it.
        if len(chunks) > 1 and len(chunks[-1].split()) == 1:
            chunks[-2] = f"{chunks[-2]} {chunks.pop()}"
    if not chunks:
        return []

    span = max(0.4, end - start)
    weights = [max(4, len(c)) + 2 for c in chunks]
    total = sum(weights)
    cues, cursor = [], start
    for chunk, weight in zip(chunks, weights):
        length = span * weight / total
        cues.append({"start": round(cursor, 2), "end": round(cursor + length, 2), "text": chunk})
        cursor += length
    cues[-1]["end"] = round(end, 2)
    return cues


# ---------------------------------------------------------------- narration

def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)], check=True)


def _duration(path):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip())


def ensure_beds():
    """A quiet room bed and a soft pad, made once and reused by every film."""
    BED.mkdir(parents=True, exist_ok=True)
    amb, music = BED / "ambience.wav", BED / "music-bed.wav"
    if not amb.exists():
        _ff("-f", "lavfi", "-i", "anoisesrc=color=brown:amplitude=0.35:duration=60",
            "-af", "lowpass=f=700,volume=0.25", "-ar", "48000", "-ac", "1", amb)
    if not music.exists():
        _ff("-f", "lavfi", "-i", "sine=frequency=196:duration=60",
            "-f", "lavfi", "-i", "sine=frequency=293.66:duration=60",
            "-f", "lavfi", "-i", "sine=frequency=246.94:duration=60",
            "-filter_complex",
            "[0][1][2]amix=inputs=3:normalize=1,tremolo=f=0.25:d=0.35,"
            "lowpass=f=1600,volume=0.5", "-ar", "48000", "-ac", "1", music)


def narrate(lines, outdir):
    """One wav per sentence, joined with silence so every word has room."""
    audio = outdir / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    for old in audio.glob("*.wav"):
        old.unlink()

    def silence(path, seconds):
        _ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{seconds:.3f}",
            "-ar", "48000", "-ac", "1", path)

    silence(audio / "lead.wav", LEAD)
    silence(audio / "gap.wav", GAP)
    silence(audio / "tail.wav", TAIL)

    parts, captions, cursor = [], [], LEAD
    for index, line in enumerate(lines):
        raw = audio / f"{index:02d}-raw.wav"
        part = audio / f"{index:02d}.wav"
        subprocess.run(
            ["piper", "--model", str(VOICE), "--output_file", str(raw)],
            input=line, text=True, check=True,
        )
        # One sample rate for every part, or the join collapses the timestamps.
        _ff("-i", raw, "-ar", "48000", "-ac", "1", part)
        raw.unlink()
        length = _duration(part)
        captions.extend(cues_for(line, cursor, cursor + length))
        parts.append(part)
        cursor += length + GAP

    sequence = [audio / "lead.wav"]
    for part in parts:
        sequence += [part, audio / "gap.wav"]
    sequence[-1] = audio / "tail.wav"
    listing = audio / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in sequence))
    _ff("-f", "concat", "-safe", "0", "-i", listing, "-ar", "48000", "-ac", "1",
        outdir / "narration.wav")
    return captions, _duration(outdir / "narration.wav")


# ---------------------------------------------------------------- scene choice

SCENES = [
    ("cta", ("free", "scan", ".org", "http", "link", "sign up", "try it", "start")),
    ("repo", ("github", "repo", "commit", "key", "secret", "token", ".env", "password")),
    ("tables", ("table", "supabase", "database", "postgres", "row", "rls", "lock", "policy")),
    ("stat", ("%", "nine", "eight", "seven", "most", "half", "in ten", "average", "majority")),
    ("fix", ("fix", "patch", "solve", "approve", "decide", "close", "repair", "checklist")),
    ("warning", ("hole", "bug", "leak", "expose", "risk", "warning", "fail", "crash", "broken")),
]


def _hit(low, word):
    """Whole-word match, so "whole" does not count as "hole"."""
    if word == "%":
        return "%" in low
    return re.search(rf"\b{re.escape(word)}(s|es)?\b", low) is not None


def pick_scene(line):
    low = line.lower()
    for name, words in SCENES:
        if any(_hit(low, word) for word in words):
            return name
    return "app"


def heading_for(line):
    """Short in-scene title: one wrapped line, whole words, no dangling joiners."""
    words = re.sub(r"[^A-Za-z0-9%' ]", " ", line).upper().split()
    kept = []
    for word in words:
        if len(" ".join(kept + [word])) > 30:
            break
        kept.append(word)
    stop = {"A", "AN", "THE", "OF", "IN", "ON", "AND", "TO", "WITH", "FOR"}
    while len(kept) > 1 and kept[-1] in stop:
        kept.pop()
    return " ".join(kept) or "WATCH THIS"


# ---------------------------------------------------------------- composition

def region(rid, role, ids, x, y, w, h, intent):
    return {"id": rid, "role": role, "layer_ids": ids,
            "region": {"x": x, "y": y, "w": w, "h": h}, "intent": intent}


def scene_layers(kind, head, start):
    """Layer list, regions and intent for one scene kind."""
    # Titles are drawn on one line, so longer ones step down in size to fit.
    size = 60 if len(head) > 22 else (74 if len(head) > 16 else 86)
    h = {"type": "text", "id": "head", "text": head, "x": 110, "y": 260,
         "size": size, "bold": True}
    head_region = region("r-head", "information", ["head"], 100, 220, 960, 260,
                         "Heading sits at the top of the portrait stage.")

    if kind == "stat":
        layers = [
            h,
            {"type": "image", "id": "counter", "path": "art/counter.png", "x": 150,
             "y": 620, "w": 940,
             "motion": {"role": "information", "reason": "The boxes arrive with the number"},
             "keys": [{"t": 0, "alpha": 0}, {"t": 0.7, "alpha": 1}]},
            {"type": "image", "id": "char", "path": "art/char-point.png", "x": 600,
             "y": 970, "w": 420,
             "motion": {"role": "actor", "reason": "The figure points at the filled boxes"},
             "keys": [{"t": 0, "x": 600}, {"t": 1.3, "x": 560}]},
        ]
        regions = [head_region,
                   region("r-counter", "information", ["counter"], 140, 590, 950, 380,
                          "Holds all ten boxes and their reveal."),
                   region("r-char", "actor", ["char"], 520, 940, 500, 700,
                          "Pointing arm and figure stay inside the box.")]
        return layers, regions, "Numbers own the frame; the figure points at them."

    if kind == "warning":
        layers = [
            h,
            {"type": "image", "id": "char", "path": "art/char-shock.png", "x": 300,
             "y": 600, "w": 500,
             "motion": {"role": "actor", "reason": "The figure throws its arms up at the flaw"},
             "keys": [{"t": 0, "w": 500}, {"t": 0.45, "w": 525}, {"t": 1.4, "w": 500}]},
            {"type": "image", "id": "warning", "path": "art/warning.png", "x": 680,
             "y": 900, "w": 290,
             "motion": {"role": "prop", "reason": "The warning sign drops into view"},
             "keys": [{"t": 0, "y": 790, "alpha": 0}, {"t": 0.55, "y": 900, "alpha": 1}]},
        ]
        regions = [head_region,
                   region("r-char", "actor", ["char"], 250, 560, 620, 900,
                          "Whole figure stays visible, raised arms inside the box."),
                   region("r-warning", "prop", ["warning"], 660, 860, 350, 360,
                          "Contains the sign's drop and resting position.")]
        return layers, regions, "Reaction left, warning right: cause and effect."

    if kind == "tables":
        layers = [
            h,
            {"type": "image", "id": "db", "path": "art/db.png", "x": 420, "y": 600,
             "w": 430,
             "motion": {"role": "prop", "reason": "The table stack settles as it is read"},
             "keys": [{"t": 0, "y": 560}, {"t": 0.7, "y": 600}]},
            {"type": "image", "id": "lock", "path": "art/lock-open.png", "x": 700,
             "y": 1030, "w": 250,
             "motion": {"role": "prop", "reason": "The open padlock turns up beside the tables"},
             "keys": [{"t": 0, "alpha": 0, "x": 740}, {"t": 0.8, "alpha": 1, "x": 700}]},
            {"type": "image", "id": "char", "path": "art/char-hold.png", "x": 120,
             "y": 790, "w": 470},
        ]
        regions = [head_region,
                   region("r-db", "prop", ["db"], 400, 560, 470, 600,
                          "Complete table stack including its settle move."),
                   region("r-lock", "prop", ["lock"], 650, 1000, 300, 340,
                          "Open padlock and its slide in."),
                   region("r-char", "actor", ["char"], 100, 750, 500, 800,
                          "Watching figure, feet inside the box.")]
        return layers, regions, "Tables on the right, watcher on the left."

    if kind == "repo":
        layers = [
            h,
            {"type": "image", "id": "repo", "path": "art/repo.png", "x": 300, "y": 590,
             "w": 660,
             "motion": {"role": "prop", "reason": "The repo card is the surface being swept"},
             "keys": [{"t": 0, "x": 300}, {"t": 0.9, "x": 318}]},
            {"type": "image", "id": "key", "path": "art/key.png", "x": 120, "y": 1290,
             "w": 360,
             "motion": {"role": "prop", "reason": "The key slides out of the repo"},
             "keys": [{"t": 0, "alpha": 0, "x": 120}, {"t": 1.0, "alpha": 1, "x": 260}]},
            {"type": "image", "id": "char", "path": "art/char-slump.png", "x": 700,
             "y": 980, "w": 390},
        ]
        regions = [head_region,
                   region("r-repo", "prop", ["repo"], 280, 570, 700, 600,
                          "Whole repo card, including its drift."),
                   region("r-key", "prop", ["key"], 100, 1250, 540, 260,
                          "Full travel of the key out of the card."),
                   region("r-char", "actor", ["char"], 680, 950, 410, 640,
                          "Dismayed builder, feet inside the box.")]
        return layers, regions, "Card first, then the key escaping along the bottom."

    if kind == "fix":
        layers = [
            h,
            {"type": "image", "id": "check", "path": "art/check.png", "x": 400,
             "y": 600, "w": 400,
             "motion": {"role": "prop", "reason": "The tick lands as the fix arrives"},
             "keys": [{"t": 0, "w": 350, "alpha": 0}, {"t": 0.6, "w": 400, "alpha": 1}]},
            {"type": "image", "id": "char", "path": "art/char-approve.png", "x": 390,
             "y": 1020, "w": 400,
             "motion": {"role": "actor", "reason": "The figure reaches toward the tick"},
             "keys": [{"t": 0, "y": 1020}, {"t": 1.0, "y": 998}]},
        ]
        regions = [head_region,
                   region("r-check", "prop", ["check"], 370, 570, 460, 460,
                          "The tick at its largest scale."),
                   region("r-char", "actor", ["char"], 360, 980, 460, 660,
                          "Approving figure and raised hand.")]
        return layers, regions, "Tick dominant, figure below it."

    if kind == "cta":
        layers = [
            {"type": "text", "id": "head", "text": head, "x": 110, "y": 250,
             "size": 56 if len(head) > 22 else 78, "bold": True},
            {"type": "text", "id": "url", "text": "stackshield.org", "x": 110,
             "y": 500, "size": 68},
            {"type": "image", "id": "char", "path": "art/char-point.png", "x": 340,
             "y": 660, "w": 520,
             "motion": {"role": "actor", "reason": "The figure points at the address"},
             "keys": [{"t": 0, "x": 340}, {"t": 1.6, "x": 310}]},
            {"type": "image", "id": "check", "path": "art/check.png", "x": 850,
             "y": 1180, "w": 220},
        ]
        regions = [region("r-head", "information", ["head"], 100, 210, 960, 320,
                          "Largest word of the film, near the top."),
                   region("r-url", "information", ["url"], 100, 490, 900, 120,
                          "The address, inside the safe width."),
                   region("r-char", "actor", ["char"], 300, 640, 620, 900,
                          "Full pointing figure."),
                   region("r-check", "information", ["check"], 820, 1140, 270, 300,
                          "Tick as the closing mark.")]
        return layers, regions, "Address readable at phone size, figure under it."

    # default: the app, and the laptop it was written on
    layers = [
        h,
        {"type": "image", "id": "char", "path": "art/char-typing.png", "x": 210,
         "y": 590, "w": 600,
         "motion": {"role": "actor", "reason": "The builder leans in and types"},
         "keys": [{"t": 0, "x": 210, "y": 590}, {"t": 1.3, "x": 222, "y": 582}]},
        {"type": "image", "id": "laptop", "path": "art/laptop.png", "x": 560,
         "y": 1010, "w": 440,
         "motion": {"role": "prop", "reason": "The fault mark on the screen appears"},
         "keys": [{"t": 0, "alpha": 0.3}, {"t": 0.8, "alpha": 1}]},
    ]
    regions = [head_region,
               region("r-char", "actor", ["char"], 180, 560, 680, 720,
                      "Leaning builder with both hands on the keys."),
               region("r-laptop", "prop", ["laptop"], 540, 980, 500, 380,
                      "Holds the screen where the fault mark appears.")]
    return layers, regions, "Builder and laptop, one move on the screen."


def compose(lines, captions, duration, outdir):
    """Shot boundaries follow the spoken sentences; captions may split inside."""
    art = outdir / "art"
    art.mkdir(exist_ok=True)
    for png in ART_SRC.glob("*.png"):
        shutil.copy2(png, art / png.name)

    # One shot per sentence: a sentence begins at its first cue's first word.
    sentence_starts = []
    cue_index = 0
    for line in lines:
        sentence_starts.append(captions[cue_index]["start"] if cue_index < len(captions) else 0)
        chunk = cues_for(line, 0, 1)  # count only
        cue_index += max(1, len(chunk))

    shots, sfx = [], []
    for index, line in enumerate(lines):
        start = 0.0 if index == 0 else round(sentence_starts[index] - 0.12, 2)
        end = duration if index == len(lines) - 1 else round(sentence_starts[index + 1] - 0.12, 2)
        if end <= start:
            end = round(start + 0.8, 2)
        kind = pick_scene(line)
        layers, regions, intent = scene_layers(kind, heading_for(line), start)
        # Layer and region ids must be unique across the whole project, not just
        # within a shot, or the runtime's motion diagnostics reject the film.
        tag = f"s{index}-"
        for layer in layers:
            layer["id"] = tag + layer["id"]
        for reg in regions:
            reg["id"] = tag + reg["id"]
            reg["layer_ids"] = [tag + lid for lid in reg["layer_ids"]]
        shots.append({
            "start": start, "end": end, "layers": layers,
            "portrait": {"intent": intent, "background_ids": [], "regions": regions},
        })
        sfx.append({"time": round(start + 0.2, 2),
                    "kind": "pop" if kind in ("warning", "stat") else "tap", "gain": 0.018})

    project = {
        "version": 1, "style": "B1", "width": WIDTH, "height": HEIGHT, "fps": 30,
        "voice": {"path": "narration.wav", "lead": 0.0, "tail": 0.0},
        "captions": captions, "shots": shots, "sfx": sfx,
        "ambience": [{"path": "ambience.wav", "start": 0, "end": duration, "gain": 0.04}],
        "music": {"path": "music-bed.wav", "gain": 0.045},
        "portrait": {"safe_area": {"left": 0.06, "right": 0.12, "top": 0.08, "bottom": 0.18}},
        "reference_review": {"author_id": "studio", "capability": {
            "mode": "limited",
            "environment": "macOS, local B1 runtime, Piper narration",
            "limitations": ["Local Piper voice", "Code-drawn artwork"],
        }},
    }
    (outdir / "project.json").write_text(json.dumps(project, indent=2))
    return project


def build(script, outdir, on_progress=None):
    """Full pipeline: text -> voice -> scenes -> rendered MP4."""
    def step(message):
        if on_progress:
            on_progress(message)

    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    lines = sentences(script)
    if not lines:
        raise ValueError("No sentences found in the script.")

    step(f"drawing the cast ({len(lines)} scenes)")
    stickman_art.main(outdir / "art")
    ensure_beds()
    shutil.copy2(BED / "ambience.wav", outdir / "ambience.wav")
    shutil.copy2(BED / "music-bed.wav", outdir / "music-bed.wav")

    step("narrating")
    captions, duration = narrate(lines, outdir)
    step("composing scenes")
    compose(lines, captions, duration, outdir)

    step("rendering (this is the slow part)")
    result = subprocess.run(
        ["node", str(AUTOPILOT), "preview", str(outdir / "project.json"),
         "--seconds", f"{duration - 0.05:.2f}", "--replace"],
        cwd=str(SKILL), capture_output=True, text=True, timeout=900,
    )
    film = outdir / "output" / "preview.mp4"
    if result.returncode != 0 or not film.exists():
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-6:]
        raise RuntimeError("Render failed: " + " | ".join(tail))

    final = outdir / "film.mp4"
    shutil.copy2(film, final)
    srt = outdir / "output" / "preview.srt"
    if srt.exists():
        shutil.copy2(srt, outdir / "film.srt")
    return {
        "film": str(final), "duration": round(duration, 2),
        "lines": len(lines), "captions": len(captions),
        "scenes": [pick_scene(line) for line in lines],
    }


def main():
    parser = argparse.ArgumentParser(description="Script to stickman explainer.")
    parser.add_argument("--script", help="script text")
    parser.add_argument("--file", help="read the script from a file")
    parser.add_argument("--out", help="output directory")
    args = parser.parse_args()

    text = args.script or (Path(args.file).read_text() if args.file else None)
    if not text:
        raise SystemExit("Pass --script \"...\" or --file script.txt")
    outdir = Path(args.out) or ROOT / "out" / "stickman" / datetime.now().strftime("%Y%m%d-%H%M%S")
    print(json.dumps(build(text, outdir), indent=2))


if __name__ == "__main__":
    main()
