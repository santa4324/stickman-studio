#!/usr/bin/env python3
"""Find and cache clips to put text on -- the part Fastlane does for you.

Fastlane searches for a funny reaction clip and overlays a line. This does the
same job with footage you are allowed to use:

  local    files you drop into data/clips/ -- your own screen recordings, or
           reaction clips you have the rights to. No key, always works.
  pixabay  free stock footage. Key appears at https://pixabay.com/api/docs/
           once you are logged in -- no approval step. PIXABAY_API_KEY in .env
  pexels   free stock footage, but new keys are paused (Nov 2026). Kept for
           anyone who already has one. PEXELS_API_KEY in .env
  commons  Wikimedia Commons. No key at all, no signup. CC-licensed, so most
           files need attribution -- fine for testing, check before posting.

    python3 clips.py --list
    python3 clips.py --query "man in glasses reaction" --limit 5
    python3 clips.py --query golf --provider commons
    python3 clips.py --locate --query golf     # print a cached path, for scripting

What this deliberately does not do: pull The Rock or Trump memes off TikTok.
Those clips are someone else's IP, and posting them from a company account is a
takedown and a muted-audio risk that lands on you, not on Fastlane.
"""

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLIPS_DIR = ROOT / "data" / "clips"
MANIFEST = CLIPS_DIR / "index.csv"
PREVIEW_DIR = ROOT / "data" / "preview"
PEXELS_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks for a descriptive User-Agent with contact info.
USER_AGENT = "reels-analyzer/1.0 (local Instagram metrics pipeline)"
# Replace the site with your own URL or email; WMF throttles anonymous clients.
COMMONS_UA = "reels-analyzer/1.0 (https://stackshield.org; contact via that site)"
VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".m4v")
MANIFEST_FIELDS = [
    "file", "query", "provider", "duration", "width", "height", "source", "credit", "downloaded_at",
]


def load_env():
    """Read .env the same way the other scripts do, without printing secrets."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def slugify(text, limit=4):
    words = re.sub(r"[^a-z0-9\s-]", "", text.lower()).split()
    return ("-".join(words[:limit]) or "clip")[:40]


def read_manifest():
    if not MANIFEST.exists():
        return []
    with MANIFEST.open(newline="") as handle:
        return list(csv.DictReader(handle))


def append_manifest(row):
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not MANIFEST.exists()
    with MANIFEST.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def probe_clip(path):
    """Duration and size for clips that were dropped in by hand, not downloaded."""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    try:
        info = json.loads(subprocess.run(command, capture_output=True).stdout or b"{}")
        stream = (info.get("streams") or [{}])[0]
        return (
            f"{float(info.get('format', {}).get('duration', 0) or 0):.0f}",
            stream.get("width", "?"),
            stream.get("height", "?"),
        )
    except (ValueError, KeyError, IndexError):
        return "?", "?", "?"


def local_clips(query=None):
    """Everything on disk, filtered by the query text if one is given."""
    if not CLIPS_DIR.exists():
        return []
    files = sorted(p for p in CLIPS_DIR.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
    if query:
        needle = query.lower()
        manifest = {row["file"]: row for row in read_manifest()}
        matched = [
            p for p in files
            if needle in p.name.lower()
            or needle in (manifest.get(p.name, {}).get("query", "") or "").lower()
        ]
        if matched:
            return matched
        print(f"No clip on disk matches '{query}'; use the whole bank.", file=sys.stderr)
    return files


def best_file(video):
    """The smallest portrait MP4 that is still at least 1280px tall."""
    files = [
        f for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4" and f.get("link")
    ]
    if not files:
        return None
    portrait = [f for f in files if (f.get("height") or 0) >= (f.get("width") or 0)]
    pool = portrait or files
    big_enough = [f for f in pool if (f.get("height") or 0) >= 1280]
    if big_enough:
        return min(big_enough, key=lambda f: f.get("height") or 0)
    return max(pool, key=lambda f: f.get("height") or 0)


def download(url, destination, api_key=None, user_agent=USER_AGENT, retries=3):
    """Fetch a file, backing off when a host throttles us.

    Wikimedia answers a bare User-Agent with HTTP 429 and asks for contact
    details, so be polite, retry, and do not hammer it.
    """
    import time

    headers = {"User-Agent": user_agent}
    if api_key:
        headers["Authorization"] = api_key
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=120) as response:
                destination.write_bytes(response.read())
            return
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 503) and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    raise last


def fetch_pexels(query, limit):
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "PEXELS_API_KEY is not set.\n"
            "Free key, no approval, takes a minute: https://www.pexels.com/api/\n"
            "Add it to .env as  PEXELS_API_KEY=...  and re-run.\n"
            "Or skip the API: drop your own clips into data/clips/ and use --list."
        )

    params = {
        "query": query,
        "orientation": "portrait",
        "per_page": str(min(max(limit * 3, 3), 80)),
        "size": "medium",
    }
    url = f"{PEXELS_SEARCH}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"Authorization": api_key, "User-Agent": "reels-analyzer/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Pexels returned HTTP {exc.code}. Check the key, or try again later.")

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for video in payload.get("videos", []):
        if len(saved) >= limit:
            break
        chosen = best_file(video)
        if not chosen:
            continue
        name = f"{slugify(query)}-{video.get('id')}.mp4"
        destination = CLIPS_DIR / name
        if not destination.exists():
            try:
                download(chosen["link"], destination)
            except Exception as exc:  # noqa: BLE001 - one bad file should not stop the batch
                print(f"    ! {name}: {exc}", file=sys.stderr)
                continue
        append_manifest({
            "file": name,
            "query": query,
            "provider": "pexels",
            "duration": f"{video.get('duration') or '?'}",
            "width": chosen.get("width"),
            "height": chosen.get("height"),
            "source": video.get("url"),
            "credit": (video.get("user") or {}).get("name", ""),
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        saved.append(destination)
    return saved


def pixabay_key():
    api_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "PIXABAY_API_KEY is not set.\n"
            "Log in at https://pixabay.com/ then open https://pixabay.com/api/docs/ --\n"
            "your key is printed in the 'key (required)' row. No approval needed.\n"
            "Add it to .env as  PIXABAY_API_KEY=...\n"
            "No key at all?  --provider commons  or drop clips into data/clips/."
        )
    return api_key


def search_pixabay(query, per_page, video_type="animation", video_id=None):
    params = {
        "key": pixabay_key(),
        "per_page": str(min(max(per_page, 3), 200)),
        "safesearch": "true",
        "order": "popular",
        # Keyword search alone returns literal matches (planets, hearts); the
        # animation filter is what surfaces 3D characters and cartoon loops.
        "video_type": video_type,
    }
    if video_id is not None:
        params["id"] = str(video_id)
    else:
        params["q"] = query
    url = f"{PIXABAY_SEARCH}?{urllib.parse.urlencode(params)}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode()).get("hits", [])
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Pixabay returned HTTP {exc.code}. Check the key.")


def best_tier(hit):
    sizes = hit.get("videos") or {}
    usable = [
        (name, data) for name, data in sizes.items()
        if data.get("url") and (data.get("height") or 0) >= 720
    ]
    if not usable:
        usable = [(n, d) for n, d in sizes.items() if d.get("url")]
    if not usable:
        return None
    # Aim at 1080p: 720p landscape leaves ~405px of real detail after the
    # 9:16 crop, but 4K costs decode time and disk for no visible gain.
    return min(usable, key=lambda pair: abs((pair[1].get("height") or 0) - 1080))[1]


def save_pixabay_hit(hit, query):
    chosen = best_tier(hit)
    if not chosen:
        return None
    name = f"{slugify(query)}-{hit.get('id')}.mp4"
    destination = CLIPS_DIR / name
    if not destination.exists():
        try:
            download(chosen["url"], destination)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {name}: {exc}", file=sys.stderr)
            return None
    append_manifest({
        "file": name,
        "query": query,
        "provider": "pixabay",
        "duration": f"{hit.get('duration', '?')}",
        "width": chosen.get("width"),
        "height": chosen.get("height"),
        "source": hit.get("pageURL", ""),
        "credit": hit.get("user", ""),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return destination


def fetch_pixabay(query, limit, video_type="animation"):
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for hit in search_pixabay(query, limit * 3, video_type):
        if len(saved) >= limit:
            break
        path = save_pixabay_hit(hit, query)
        if path:
            saved.append(path)
    return saved


def fetch_pixabay_id(video_id):
    hits = search_pixabay("", 3, "all", video_id=video_id)
    if not hits:
        raise SystemExit(f"No Pixabay video with id {video_id}.")
    hit = hits[0]
    label = " ".join((hit.get("tags") or "clip").split(",")[:3]).strip() or "clip"
    path = save_pixabay_hit(hit, label)
    if not path:
        raise SystemExit(f"Could not download id {video_id}.")
    print(f"Saved {path.name}")
    return path


def preview_pixabay(query, limit, video_type="animation"):
    """Tile the search thumbnails so you can pick before spending disk.

    Downloading a 26MB 4K clip to discover it is a planet gets old fast; this
    costs a handful of small JPEGs instead.
    """
    hits = search_pixabay(query, limit * 2, video_type)[:limit]
    if not hits:
        raise SystemExit(f"Nothing on Pixabay for '{query}' ({video_type}).")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise SystemExit("Preview needs Pillow:  python3 -m pip install pillow")

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, hit in enumerate(hits, start=1):
        tier = best_tier(hit) or {}
        thumb = tier.get("thumbnail") or next(
            (d["thumbnail"] for d in (hit.get("videos") or {}).values() if d.get("thumbnail")),
            None,
        )
        if not thumb:
            continue
        target = PREVIEW_DIR / f"{index:02d}.jpg"
        try:
            download(thumb, target)
        except Exception:  # noqa: BLE001
            continue
        rows.append((index, hit, target))

    if not rows:
        raise SystemExit("Pixabay returned no usable thumbnails.")

    cell_w, cell_h, cols = 320, 180, 4
    sheet_rows = (len(rows) + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, (cell_h + 26) * sheet_rows), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 16)
    except OSError:
        font = ImageFont.load_default()

    for slot, (index, hit, target) in enumerate(rows):
        image = Image.open(target).convert("RGB")
        image.thumbnail((cell_w, cell_h))
        x = (slot % cols) * cell_w
        y = (slot // cols) * (cell_h + 26)
        sheet.paste(image, (x + (cell_w - image.width) // 2, y + (cell_h - image.height) // 2))
        draw.text((x + 6, y + cell_h + 4), f"{index}. id {hit.get('id')}", fill=(250, 220, 60), font=font)

    sheet_path = PREVIEW_DIR / f"preview-{slugify(query)}.png"
    sheet.save(sheet_path)

    print(f"\nPixabay: {len(hits)} hits for '{query}' ({video_type}). Sheet: {sheet_path}\n")
    for index, hit, target in rows:
        tier = best_tier(hit) or {}
        print(
            f"  {index:>2}. id {hit.get('id')}  {hit.get('duration', '?')}s  "
            f"{tier.get('width')}x{tier.get('height')}  by {hit.get('user', '?')}"
        )
    print("\nKeep one:  python3 clips.py --id <id>")
    return sheet_path


def fetch_commons(query, limit):
    """Keyless fallback. Mostly WebM, mostly landscape; ffmpeg crops to 9:16."""
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"filetype:video {query}",
        "gsrnamespace": "6",
        "gsrlimit": str(min(max(limit * 2, 5), 40)),
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
    }
    url = f"{COMMONS_API}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Wikimedia Commons returned HTTP {exc.code}.")

    pages = list((payload.get("query") or {}).get("pages", {}).values())
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for page in pages:
        if len(saved) >= limit:
            break
        info = (page.get("imageinfo") or [{}])[0]
        source_url = info.get("url")
        mime = info.get("mime", "")
        if not source_url or not mime.startswith("video/"):
            continue
        # Anything under 720 on its short side turns to mush at 1080x1920.
        if min(info.get("width") or 0, info.get("height") or 0) < 720:
            continue
        suffix = Path(urllib.parse.urlparse(source_url).path).suffix or ".webm"
        name = f"{slugify(query)}-commons-{page.get('pageid')}{suffix}"
        destination = CLIPS_DIR / name
        if not destination.exists():
            try:
                download(source_url, destination, user_agent=COMMONS_UA)
            except Exception as exc:  # noqa: BLE001
                print(f"    ! {name}: {exc}", file=sys.stderr)
                continue
        time.sleep(1.5)  # stay well inside Wikimedia's expectations
        meta = info.get("extmetadata") or {}
        append_manifest({
            "file": name,
            "query": query,
            "provider": "commons",
            "duration": "?",
            "width": info.get("width"),
            "height": info.get("height"),
            "source": f"https://commons.wikimedia.org/?curid={page.get('pageid')}",
            "credit": (meta.get("Artist") or {}).get("value", "")[:120],
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        saved.append(destination)
    return saved


def pick_provider(requested):
    """auto: whichever key exists, falling back to the keyless source."""
    if requested != "auto":
        return requested
    if os.environ.get("PIXABAY_API_KEY", "").strip():
        return "pixabay"
    if os.environ.get("PEXELS_API_KEY", "").strip():
        return "pexels"
    return "commons"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--query", help="what the clip should show")
    parser.add_argument("--limit", type=int, default=5, help="how many to download")
    parser.add_argument(
        "--type", default="animation", choices=["animation", "film", "all"],
        dest="video_type",
        help="Pixabay videos only: animation (3D/cartoon) or film (real footage)",
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="show a thumbnail sheet of matches instead of downloading them",
    )
    parser.add_argument("--id", type=int, help="download one Pixabay video by its id")
    parser.add_argument("--list", action="store_true", help="show cached clips")
    parser.add_argument("--locate", action="store_true", help="print one cached path")
    parser.add_argument(
        "--provider",
        choices=["auto", "pixabay", "pexels", "commons", "local"],
        default="auto",
        help="auto = pixabay if keyed, else pexels if keyed, else commons",
    )
    args = parser.parse_args()

    load_env()

    if args.list:
        clips = local_clips(args.query)
        if not clips:
            print(f"No clips in {CLIPS_DIR.relative_to(ROOT)}.")
            print("Fetch some:  python3 clips.py --query \"person reacting\" --limit 5")
            print("Or copy your own screen recordings in there.")
            return
        manifest = {row["file"]: row for row in read_manifest()}
        for clip in clips:
            size = clip.stat().st_size / 1_048_576
            meta = manifest.get(clip.name) or {}
            if meta.get("duration") not in (None, "", "?"):
                duration, width, height = meta["duration"], meta.get("width"), meta.get("height")
            else:
                duration, width, height = probe_clip(clip)
            print(f"{clip.name:<44} {size:>6.1f} MB  {duration}s, {width}x{height}")
        print(f"\n{len(clips)} clip(s) in {CLIPS_DIR.relative_to(ROOT)}")
        return

    if args.locate:
        clips = local_clips(args.query)
        if not clips:
            raise SystemExit("No matching clips on disk. Run with --list to see what exists.")
        print(random.choice(clips))
        return

    if args.id:
        fetch_pixabay_id(args.id)
        return

    if args.preview:
        if not args.query:
            raise SystemExit("Pass --query \"what to show\" with --preview.")
        preview_pixabay(args.query, args.limit, args.video_type)
        return

    if not args.query:
        raise SystemExit("Pass --query \"what to show\", or --list / --locate.")

    provider = pick_provider(args.provider)

    if provider == "local":
        clips = local_clips(args.query)
        for clip in clips[: args.limit]:
            print(clip)
        if not clips:
            raise SystemExit("No matching clips on disk.")
        return

    fetchers = {
        "pixabay": fetch_pixabay,
        "pexels": fetch_pexels,
        "commons": fetch_commons,
    }
    print(f"Searching {provider} for '{args.query}'...")
    if provider == "pixabay":
        saved = fetch_pixabay(args.query, args.limit, args.video_type)
    else:
        saved = fetchers[provider](args.query, args.limit)
    if not saved:
        raise SystemExit("Nothing usable came back. Try a different query.")
    print(f"\nSaved {len(saved)} clip(s) to {CLIPS_DIR.relative_to(ROOT)}:")
    for clip in saved:
        print(f"  {clip.name}")
    print("\nNext:\n  python3 make_reel.py --variants hooks.txt --clip random")


if __name__ == "__main__":
    main()
