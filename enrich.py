#!/usr/bin/env python3
"""Download each Reel, transcribe it, and pull storyboard frames.

Optional layer. Without it you still get metrics; with it, every Reel gains a
transcript, hook text, words-per-minute, a detected CTA, and still frames --
which is what turns "this one got more views" into "this one opened with a
question in the first 2 seconds while talking at 210 wpm".

The frames land in data/frames/<media_id>/ so an AI with filesystem access can
look at them directly.

Needs: ffmpeg + ffprobe on PATH, and `pip install yt-dlp faster-whisper`.

Usage:
    python3 enrich.py                 # every Reel not yet enriched
    python3 enrich.py --limit 5
    python3 enrich.py --redo          # re-transcribe everything
    python3 enrich.py --frames 0      # skip frame extraction
"""

import argparse
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import igapi
import store

ROOT = Path(__file__).resolve().parent
MEDIA_DIR = ROOT / "data" / "media"
FRAMES_DIR = ROOT / "data" / "frames"
HOOK_WINDOW_S = 3.0
NO_SPEECH_MAX = 0.6
MIN_WORDS = 5

CTA_PATTERN = re.compile(
    r"\b(follow|subscribe|comment|save this|save it|share this|dm me|link in bio|"
    r"check the link|tag a friend|drop a)\b",
    re.IGNORECASE,
)


def require_binaries():
    missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        raise SystemExit(
            f"Missing on PATH: {', '.join(missing)}\n"
            "  macOS:  brew install ffmpeg\n"
            "  Ubuntu: sudo apt install ffmpeg"
        )


def download(media_id, media_url):
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    target = MEDIA_DIR / f"{media_id}.mp4"
    if target.exists() and target.stat().st_size > 1024:
        return target

    if media_url:
        request = urllib.request.Request(media_url, headers={"User-Agent": "reels-analyzer/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                target.write_bytes(response.read())
            return target
        except Exception as exc:  # noqa: BLE001 - fall through to yt-dlp
            print(f"    CDN download failed ({exc}); trying yt-dlp")

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        print("    ! no CDN url and yt-dlp not installed")
        return None

    permalink = None
    connection = store.connect()
    row = connection.execute("SELECT permalink FROM media WHERE id = ?", (media_id,)).fetchone()
    connection.close()
    if row:
        permalink = row["permalink"]
    if not permalink:
        return None

    subprocess.run(
        [sys.executable, "-m", "yt_dlp", "-f", "mp4", "-o", str(target), permalink],
        check=False,
        capture_output=True,
    )
    return target if target.exists() else None


def probe_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=False,
    )
    try:
        return round(float(result.stdout.strip()), 2)
    except ValueError:
        return None


def extract_frames(path, media_id, count):
    if count <= 0:
        return None
    duration = probe_duration(path)
    if not duration:
        return None
    out_dir = FRAMES_DIR / media_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        timestamp = duration * (index + 0.5) / count
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{timestamp:.2f}",
             "-i", str(path), "-frames:v", "1", "-vf", "scale=480:-1",
             str(out_dir / f"{index + 1:02d}.jpg")],
            check=False, capture_output=True,
        )
    return str(out_dir.relative_to(ROOT)) if any(out_dir.iterdir()) else None


def load_audio(path, sample_rate=16000):
    """Decode to mono float32 with ffmpeg instead of letting Whisper use PyAV.

    faster-whisper calls av.open(..., metadata_errors="ignore"), which newer
    PyAV releases no longer accept -- it raises TypeError before any audio is
    read. Handing transcribe() a float32 array skips that path, and ffmpeg is
    already required for frame extraction.
    """
    command = [
        "ffmpeg", "-nostdin", "-threads", "0", "-i", str(path),
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le",
        "-ar", str(sample_rate), "-",
    ]
    result = subprocess.run(command, capture_output=True)
    if not result.stdout:
        tail = result.stderr.decode("utf-8", "replace").strip()[-300:]
        raise SystemExit(f"ffmpeg could not decode {path}:\n{tail}")

    import numpy

    return numpy.frombuffer(result.stdout, numpy.int16).astype(numpy.float32) / 32768.0


def transcribe(path):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("faster-whisper not installed: pip install faster-whisper")

    model_name = igapi.os.environ.get("WHISPER_MODEL", "base.en").strip() or "base.en"
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(load_audio(path), vad_filter=True)

    text_parts, hook_parts = [], []
    for segment in segments:
        # Over a music bed Whisper invents sentences. Anything it flags as
        # probably-not-speech is dropped rather than stored as a real hook.
        if getattr(segment, "no_speech_prob", 0.0) > NO_SPEECH_MAX:
            continue
        text_parts.append(segment.text.strip())
        if segment.start < HOOK_WINDOW_S:
            hook_parts.append(segment.text.strip())

    transcript = " ".join(part for part in text_parts if part).strip()
    if len(transcript.split()) < MIN_WORDS:
        # Silent text-overlay Reel: the hook lives in the frames, not the audio.
        return None, None

    hook = " ".join(part for part in hook_parts if part).strip()
    if not hook:
        hook = " ".join(transcript.split()[:12])
    return transcript, hook


def find_cta(transcript):
    for sentence in re.split(r"(?<=[.!?])\s+", transcript or ""):
        if CTA_PATTERN.search(sentence):
            return sentence.strip()[:200]
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="max Reels to process")
    parser.add_argument("--redo", action="store_true", help="re-process already enriched Reels")
    parser.add_argument("--frames", type=int, default=6, help="storyboard frames per Reel (0 = none)")
    args = parser.parse_args()

    igapi.require_token()
    require_binaries()

    connection = store.connect()
    done = set() if args.redo else set(store.all_enriched(connection))
    media = store.all_media(connection)
    queue = [
        item for item in media
        if item["id"] not in done and (item.get("media_product_type") or "").upper() == "REELS"
    ]
    if args.limit:
        queue = queue[: args.limit]
    if not queue:
        print("Nothing to enrich. Run sync.py first, or pass --redo.")
        return

    print(f"Enriching {len(queue)} Reel(s)...")
    for index, item in enumerate(queue, 1):
        media_id = item["id"]
        label = " ".join((item.get("caption") or "").split())[:50]
        print(f"[{index}/{len(queue)}] {label or media_id}")

        media_url = None
        try:
            meta = igapi.get(media_id, {"fields": "media_url,permalink"})
            media_url = meta.get("media_url")
        except igapi.ApiError:
            pass

        path = download(media_id, media_url)
        if not path:
            print("    skipped: could not download")
            continue

        duration = probe_duration(path)
        frames = extract_frames(path, media_id, args.frames)
        transcript, hook = transcribe(path)
        words = len(transcript.split()) if transcript else 0
        wpm = round(words / duration * 60) if (duration and words) else None

        store.save_enriched(connection, {
            "media_id": media_id,
            "duration_s": duration,
            "transcript": transcript,
            "word_count": words,
            "wpm": wpm,
            "hook_text": hook,
            "cta_text": find_cta(transcript),
            "frames_dir": frames,
        })
        connection.commit()
        print(f"    {duration or '?'}s, {words} words, {wpm or '?'} wpm, frames={frames or 'none'}")
        if hook:
            print(f"    hook: \"{hook[:100]}\"")
        elif frames:
            print("    no speech detected -- read the hook from the frames")

    connection.close()
    print("\nDone. Re-run report.py to fold watch-through into the ranking.")


if __name__ == "__main__":
    main()
