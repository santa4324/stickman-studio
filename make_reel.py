#!/usr/bin/env python3
"""Render a silent, text-overlay Reel locally -- no account, no credits.

The format that wins in data/report.md is 9:16, silent, one or two lines of
text, nothing else. This renders that in a couple of seconds from ffmpeg plus
Pillow: no video model, no GPU, no multi-gigabyte weights, so it is safe on an
8 GB M1. Text is drawn to a transparent PNG and composited with the overlay
filter, because this ffmpeg build has no drawtext (no freetype).

Every render is appended to data/renders.csv, so when report.py starts showing
numbers for a Reel you can trace it back to the hook that earned them. Next to
each .mp4 sits a .txt pack from caption.py -- the on-screen ask, caption, first
comment and hashtags -- because a hook that holds attention still needs an ask
before anyone comments, saves or sends it.

    python3 make_reel.py --text "me checking for leaked secrets before launch"
    python3 make_reel.py --variants hooks.txt --clip random
    python3 make_reel.py --text "..." --clip data/clips/golf-1234.mp4
    python3 make_reel.py --text "..." --clip golf.mp4 --seconds 8 --mute --list-fonts

The clip's own audio is kept by default: in a reaction meme the sound is half
of the joke. Pass --mute for silent footage and add trending audio in Instagram.
"""

import argparse
import csv
import random
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import caption

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "out"
RENDER_LOG = ROOT / "data" / "renders.csv"

WIDTH, HEIGHT, FPS = 1080, 1920, 30
# The reference Reels in examples/ use plain, small type: ~34px on a 1080-wide
# frame, regular weight, a narrow column near the top, and a soft shadow instead
# of a stroke. Big bold outlined text reads as an ad; small text over real
# footage reads as a meme caption, which is what gets watched.
MAX_TEXT_WIDTH = int(WIDTH * 0.70)
MAX_TEXT_HEIGHT = int(HEIGHT * 0.42)
TOP_MARGIN = int(HEIGHT * 0.11)
LINE_SPACING = 0.42          # as a fraction of the font size
MAX_FONT_SIZE = 40
MIN_FONT_SIZE = 28
MAX_LINES = 5

# The ask sits low in frame and stays smaller than the hook: it should read as
# a caption, not compete with the line that earns the first three seconds.
CTA_MAX_FONT_SIZE = 32
CTA_MIN_FONT_SIZE = 24
CTA_MAX_LINES = 2
CTA_BOTTOM_MARGIN = int(HEIGHT * 0.10)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

BACKGROUND = "gradients=s={w}x{h}:c0=0x0E1116:c1=0x1B2430:n=2:d={seconds}:speed=0.012:r={fps}"


def find_font(explicit=None):
    candidates = [explicit] if explicit else FONT_CANDIDATES
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit(
        "No usable font found. Pass one with --font /path/to/Font Bold.ttf\n"
        "On macOS these usually work:\n  " + "\n  ".join(FONT_CANDIDATES)
    )


def wrap_text(text, font, max_width):
    """Greedy word wrap measured against the real font, not a character count."""
    lines = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if current and font.getlength(candidate) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines


def fit_text(text, font_path):
    """Largest font size whose wrapped block fits the area in three lines or fewer.

    A long hook shrinks instead of stacking into five lines, which is what the
    high-skip Reels in data/report.md look like.
    """
    from PIL import ImageFont

    fallback = None
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -1):
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text(text, font, MAX_TEXT_WIDTH)
        if not lines:
            raise SystemExit("Nothing to render: the text is empty.")
        ascent, descent = font.getmetrics()
        spacing = int(size * LINE_SPACING)
        block_height = len(lines) * (ascent + descent) + (len(lines) - 1) * spacing
        widest = max(font.getlength(line) for line in lines)
        if block_height > MAX_TEXT_HEIGHT or widest > MAX_TEXT_WIDTH:
            continue
        candidate = (font, lines, ascent, descent, spacing)
        if fallback is None:
            fallback = candidate
        if len(lines) <= MAX_LINES:
            return candidate

    if fallback is not None:
        return fallback

    font = ImageFont.truetype(font_path, MIN_FONT_SIZE)
    lines = wrap_text(text, font, MAX_TEXT_WIDTH) or [text.strip()]
    ascent, descent = font.getmetrics()
    return font, lines, ascent, descent, int(MIN_FONT_SIZE * LINE_SPACING)


def fit_cta(text, font_path):
    """One short ask, shrunk until it fits two lines at the bottom."""
    from PIL import ImageFont

    for size in range(CTA_MAX_FONT_SIZE, CTA_MIN_FONT_SIZE - 1, -1):
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text(text, font, MAX_TEXT_WIDTH)
        ascent, descent = font.getmetrics()
        spacing = int(size * LINE_SPACING)
        block_height = len(lines) * (ascent + descent) + (len(lines) - 1) * spacing
        if len(lines) <= CTA_MAX_LINES and block_height <= HEIGHT * 0.16:
            return font, lines, ascent, descent, spacing

    font = ImageFont.truetype(font_path, CTA_MIN_FONT_SIZE)
    lines = wrap_text(text, font, MAX_TEXT_WIDTH)[:CTA_MAX_LINES]
    ascent, descent = font.getmetrics()
    return font, lines, ascent, descent, int(CTA_MIN_FONT_SIZE * LINE_SPACING)


def draw_soft_text(draw, xy, line, font, shadow_only=False):
    """White text with a blurred drop shadow -- no outline.

    A stroke is what makes meme text look like a Canva template. A shadow keeps
    the line legible over bright sky and skin without boxing it in.
    """
    x, y = xy
    if shadow_only:
        draw.text((x, y), line, font=font, fill=(0, 0, 0, 235))
    else:
        # A hairline edge plus the shadow: white type survives a bright sky
        # without the thick outline that makes a Reel look like a Canva deck.
        draw.text(
            (x, y), line, font=font, fill=(255, 255, 255, 255),
            stroke_width=max(1, font.size // 18), stroke_fill=(0, 0, 0, 130),
        )


def render_text_png(text, font_path, destination, cta=None):
    """Small white caption near the top of a transparent 1080x1920 canvas."""
    from PIL import Image, ImageDraw, ImageFilter

    font, lines, ascent, descent, spacing = fit_text(text, font_path)
    shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    y = TOP_MARGIN
    for line in lines:
        x = int((WIDTH - font.getlength(line)) / 2)
        draw_soft_text(sdraw, (x, y), line, font, shadow_only=True)
        draw_soft_text(draw, (x, y), line, font)
        y += ascent + descent + spacing

    if cta:
        cta_font, cta_lines, c_ascent, c_descent, c_spacing = fit_cta(cta, font_path)
        cta_height = (
            len(cta_lines) * (c_ascent + c_descent)
            + (len(cta_lines) - 1) * c_spacing
        )
        cta_y = HEIGHT - CTA_BOTTOM_MARGIN - cta_height
        for line in cta_lines:
            x = int((WIDTH - cta_font.getlength(line)) / 2)
            draw_soft_text(sdraw, (x, cta_y), line, cta_font, shadow_only=True)
            draw_soft_text(draw, (x, cta_y), line, cta_font)
            cta_y += c_ascent + c_descent + c_spacing

    # Offset the shadow down a touch, blur it, then lay the type on top.
    blurred = shadow.filter(ImageFilter.GaussianBlur(radius=max(2, font.size // 16)))
    offset = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    offset.alpha_composite(blurred, (0, max(2, font.size // 12)))
    canvas = Image.alpha_composite(offset, canvas)
    canvas.save(destination)
    return font.size, len(lines)


def slugify(text, limit=6):
    words = re.sub(r"[^a-z0-9\s-]", "", text.lower()).split()
    stem = "-".join(words[:limit]) or "reel"
    return stem[:60]


def resolve_clips(value, count):
    """One clip per hook: a file, a folder, or 'random' (from data/clips).

    A batch on a folder deals a different clip to every hook, so no two Reels
    share footage and nothing looks duplicated in the feed. A single named file
    is used for all of them -- that's a deliberate A/B on one background.
    """
    suffixes = {".mp4", ".mov", ".webm", ".m4v"}
    if not value:
        return [None] * count
    if value.strip().lower() == "random":
        value = ROOT / "data" / "clips"
        if not value.exists():
            raise SystemExit(
                'Nothing in data/clips yet. Fetch some:\n'
                '  python3 clips.py --query "person reacting" --limit 5'
            )
    path = Path(value)
    if path.is_dir():
        pool = sorted(p for p in path.iterdir() if p.suffix.lower() in suffixes)
        if not pool:
            raise SystemExit(f"No video files in {path}")
        picks = []
        while len(picks) < count:
            shuffled = pool[:]
            random.shuffle(shuffled)
            picks.extend(shuffled)
        return picks[:count]
    if not path.exists():
        raise SystemExit(f"No such clip: {value}")
    return [path] * count


def has_audio_signal(path):
    """Does this clip's audio track carry any sound?

    Stock clips often ship a silent AAC track. Keeping it means the Reel has an
    audio stream that plays nothing, which reads as a broken upload; dropping it
    leaves an honest silent file that a track can be added to in the app.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-map", "0:a:0?", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    match = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", result.stderr)
    if not match or "inf" in match.group(1):
        return False
    return float(match.group(1)) > -50


def build_command(clip, text_png, output, seconds, encoder, transparent_bg, keep_audio, focus, audio=None):
    if clip:
        source = ["-stream_loop", "-1", "-i", str(clip)]
        # Landscape sources lose their edges when cropped to 9:16; focus picks
        # which horizontal slice survives (0 = left, 0.5 = centre, 1 = right).
        prep = (
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT}:(iw-{WIDTH})*{focus}:0,fps={FPS}"
        )
    elif transparent_bg:
        source = ["-f", "lavfi", "-i", f"color=c=0x0E1116:s={WIDTH}x{HEIGHT}:r={FPS}"]
        prep = f"fps={FPS}"
    else:
        source = [
            "-f", "lavfi", "-i",
            BACKGROUND.format(w=WIDTH, h=HEIGHT, seconds=seconds, fps=FPS),
        ]
        prep = f"fps={FPS}"

    source += ["-loop", "1", "-i", str(text_png)]
    audio_index = 0
    if audio:
        # Stock animation clips are silent; a music bed is what makes the
        # format work. This input comes after the overlay PNG.
        source += ["-stream_loop", "-1", "-i", str(audio)]
        audio_index = sum(1 for token in source if token == "-i") - 1
    graph = f"[0:v]{prep}[bg];[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]"

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *source,
        "-t", str(seconds),
        "-filter_complex", graph,
        "-map", "[v]",
    ]
    if audio:
        command += ["-map", f"{audio_index}:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]
    elif clip and keep_audio:
        command += ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"]
    else:
        command += ["-an"]
    command += ["-c:v", encoder]
    if encoder == "h264_videotoolbox":
        command += ["-b:v", "5M", "-allow_sw", "1"]
    else:
        command += ["-preset", "veryfast", "-crf", "21"]
    command += ["-movflags", "+faststart", str(output)]
    return command


def render(text, clip, output, seconds, font_path, encoder, keep_audio, focus, audio=None, cta=None):
    text_png = Path(tempfile.mkstemp(suffix=".png")[1])
    try:
        size, line_count = render_text_png(text, font_path, text_png, cta)
        attempts = [(encoder, False)]
        if encoder != "libx264":
            attempts.append(("libx264", False))
        # Last resort: drop the gradient source, which some builds omit.
        attempts.append(("libx264", True))

        last = None
        for name, transparent in attempts:
            command = build_command(
                clip, text_png, output, seconds, name, transparent, keep_audio, focus, audio
            )
            # stdin must be closed: ffmpeg reads it for interactive keys, and
            # would otherwise eat a byte or two from whatever loop called us.
            result = subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL)
            if result.returncode == 0:
                return size, line_count, name
            last = result.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(f"ffmpeg failed for {output}:\n{last}")
    finally:
        text_png.unlink(missing_ok=True)


def log_render(path, text, clip, seconds):
    RENDER_LOG.parent.mkdir(parents=True, exist_ok=True)
    new_file = not RENDER_LOG.exists()
    path = Path(path)
    try:
        shown = path.resolve().relative_to(ROOT)
    except ValueError:
        shown = path
    with RENDER_LOG.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(["rendered_at", "file", "hook", "clip", "seconds"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            shown,
            " ".join(text.split()),
            Path(clip).name if clip else "gradient",
            seconds,
        ])


def load_pairs(path):
    """One Reel per line:  clip | hook | ask | focus | caption | first comment

    The clip carries the joke, so the line only lands if it was written for that
    footage. Pairs keep the two together instead of dealing random backgrounds.
    Only the first two fields are required; leave focus empty to use --focus.
    """
    pairs = []
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise SystemExit(f"{path}:{number}: need  clip | hook")
        pairs.append({
            "clip": parts[0],
            "hook": parts[1],
            "ask": parts[2] if len(parts) > 2 and parts[2] else "auto",
            "focus": float(parts[3]) if len(parts) > 3 and parts[3] else None,
            "caption": parts[4] if len(parts) > 4 and parts[4] else None,
            "comment": parts[5] if len(parts) > 5 and parts[5] else None,
        })
    if not pairs:
        raise SystemExit(f"{path} has no pair lines (clip | hook).")
    return pairs


def resolve_pair_clip(name):
    path = Path(name)
    if path.exists():
        return path
    candidate = ROOT / "data" / "clips" / name
    if candidate.exists():
        return candidate
    raise SystemExit(
        f"No such clip: {name}\n"
        "Put the file in data/clips/ (any mp4/mov you own or downloaded) and re-run."
    )


def load_variants(path):
    hooks = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            hooks.append(line)
    if not hooks:
        raise SystemExit(f"{path} has no hook lines (one per line, # for comments).")
    return hooks


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--text", help="the overlay hook, one line")
    parser.add_argument("--variants", help="file of hooks, one per line")
    parser.add_argument(
        "--pairs",
        help="file of  clip | hook [| ask | focus]  lines: one Reel per line, text written for that clip",
    )
    parser.add_argument("--clip", help="background video; omit for a dark gradient")
    parser.add_argument("--out", help="output file (single --text only)")
    parser.add_argument("--out-dir", default=str(OUT_DIR), help="where renders go")
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--mute", action="store_true", help="drop the clip's audio")
    parser.add_argument(
        "--audio",
        help="music/sound file to lay under a silent clip (any format ffmpeg reads)",
    )
    parser.add_argument(
        "--focus", type=float, default=0.5,
        help="which slice of a landscape clip survives the 9:16 crop (0 left, 0.5 centre, 1 right)",
    )
    parser.add_argument("--font", help="path to a bold .ttf")
    parser.add_argument(
        "--cta",
        default="auto",
        help='on-screen ask at the bottom: "auto" (bank line per hook), "none", or your own text',
    )
    parser.add_argument("--list-fonts", action="store_true", help="show usable fonts")
    args = parser.parse_args()

    if args.list_fonts:
        for candidate in FONT_CANDIDATES:
            print(("[found]  " if Path(candidate).exists() else "[missing]") + "  " + candidate)
        return

    if not args.text and not args.variants and not args.pairs:
        raise SystemExit(
            "Pass --text \"one hook\", or --variants hooks.txt, or --pairs pairs.txt"
        )

    font_path = find_font(args.font)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")

    if not 0.0 <= args.focus <= 1.0:
        raise SystemExit("--focus must be between 0 and 1")

    if args.pairs:
        if args.clip:
            raise SystemExit("--pairs already names a clip per line; drop --clip.")
        jobs = [
            (pair["hook"], resolve_pair_clip(pair["clip"]), pair["focus"], pair["ask"],
             pair["caption"], pair["comment"])
            for pair in load_pairs(args.pairs)
        ]
    else:
        hooks = load_variants(args.variants) if args.variants else [args.text]
        clips = resolve_clips(args.clip, len(hooks))
        jobs = [(hook, clips[i], None, "auto", None, None) for i, hook in enumerate(hooks)]

    if args.out and len(jobs) > 1:
        raise SystemExit("--out only works with one Reel; drop it for batches.")

    keep_audio = not args.mute
    unique_clips = {str(job[1]) for job in jobs}
    source = "a different clip each" if len(unique_clips) > 1 else (
        jobs[0][1].name if jobs[0][1] else "dark gradient"
    )
    print(f"Rendering {len(jobs)} Reel(s) at {WIDTH}x{HEIGHT}, {args.seconds:g}s on {source}")
    print(f"Audio: {'kept when the clip has sound' if keep_audio else 'none'}\n")
    for index, (hook, clip, pair_focus, pair_ask, pair_caption, pair_comment) in enumerate(jobs, 1):
        focus = pair_focus if pair_focus is not None else args.focus
        if args.out:
            output = Path(args.out)
            output.parent.mkdir(parents=True, exist_ok=True)
        else:
            output = out_dir / f"{stamp}-{index:02d}-{slugify(hook)}.mp4"

        # The pack is what turns a view into a comment, save or send; write it
        # next to the video so posting is a copy-paste, not a rewrite.
        ask = args.cta if pair_ask.strip().lower() == "auto" else pair_ask
        cta = None
        if ask.strip().lower() == "auto":
            cta = caption.random_cta(hook, index - 1)
        elif ask.strip().lower() not in ("none", ""):
            cta = ask.strip()
        pack_path = caption.write_pack(
            output.with_suffix(".txt"), hook, cta, pair_caption, pair_comment
        )

        clip_audio = bool(clip) and keep_audio and (bool(args.audio) or has_audio_signal(clip))
        size, line_count, encoder = render(
            hook, clip, output, args.seconds, font_path, "h264_videotoolbox",
            clip_audio, focus, args.audio, cta,
        )
        log_render(output, hook, clip, args.seconds)

        megabytes = output.stat().st_size / 1_048_576
        print(f"[{index}/{len(jobs)}] {output.name}")
        print(f'    {size}px, {line_count} line(s), {encoder}, {megabytes:.1f} MB')
        print(f'    clip: {clip.name if clip else "dark gradient"}')
        print(f'    hook: "{hook}"')
        if cta:
            print(f'    ask:  "{cta}"')
        print(f"    pack: {pack_path.name} (caption + first comment + hashtags)")

    print(f"\nLogged to {RENDER_LOG.relative_to(ROOT)}")
    print("Post, then run python3 sync.py && python3 report.py to see how it did.")


if __name__ == "__main__":
    main()
