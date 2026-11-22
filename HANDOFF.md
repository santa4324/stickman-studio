# Where we stopped — 22 Nov 2026

Paused until the meme clips are gathered. Pick up from the "To resume" section.

## State

- **`out/stackshield/`** — current set, built from the last30days research pass.
  8 Reels, 8 unique clips, 1080x1920, 8s, all pass the ffmpeg-skill Reels check
  (0 failed, 2 warnings: no audio track, no subtitles).
- **`out/action/`** — earlier action batch using the same clips with older copy.
  Each `.mp4` has a `.txt` pack beside it (on-screen hook, ask, caption,
  first comment to pin, hashtags).
- **`out/recognition/`** — the earlier portrait set. Kept for comparison only;
  superseded by `out/action/` because the portraits had no room for the caption.
- **`data/pairs.txt`** — the clips and their lines. This is the source of truth
  for what gets rendered. One Reel per line.
- **`data/renders.csv`** — log of every render with its clip and hook, for
  comparing against real analytics later.

## What is already settled

- Text style, from the user's own examples: small (28-40px) regular white type,
  near the top of the frame (~11%), soft shadow plus a hairline edge, never a
  thick outline. Constants at the top of `make_reel.py`.
- Every Reel needs its own background clip; nothing shared across a batch.
- The clip carries the joke, so the line is written for the footage. That is
  what `--pairs` exists for. Random text over random stock does not work.
- Their best-performing posts so far: golf chase-cam 330 views, thinking-guy
  meme 245, podium meme 223, Etsy screen recording 179 (highest shares).

## To resume

1. Put the gathered clips in a folder, e.g. `data/clips/memes/`.
2. Add one line per Reel to `data/pairs.txt`:

   ```
   clips/memes/whatever.mp4 | the line written for that shot | ask line | focus | caption body | first comment
   ```
   Only clip and line are required. `focus` is 0-1 (left to right) and only
   matters for landscape sources where the subject is off-centre. Field 5
   replaces the generic caption paragraph with your own (a number, a source,
   a caveat); field 6 replaces the first comment.
3. Check the crop before committing to a line:
   `ffmpeg -ss 2 -i data/clips/memes/x.mp4 -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -frames:v 1 /tmp/x.jpg`
   A close-up on a 16:9 source fills the phone and the caption lands on eyelids.
4. Render:
   `python3 make_reel.py --pairs data/pairs.txt --seconds 8 --out-dir out/memes`
5. Post, then `python3 sync.py && python3 report.py` to see how they did.

## Skills installed (`.agents/skills/`)

- **last30days** (`mvanhorn/last30days-skill`) - research pass over Reddit, HN,
  YouTube, TikTok, Instagram, GitHub. Keyless sources work; Reddit and TikTok
  were unreachable on the first run, so coverage was HN + YouTube + GitHub.
  Run: `python3 .agents/skills/last30days/scripts/last30days.py "<topic>"
  --auto-resolve --no-browser-cookies --days 30 --emit=compact --save-dir ""`
- **humanizer** (`blader/humanizer`) - strips AI tells from copy before it ships.
  Applied to every caption in `out/stackshield/`.
- **ffmpeg-skill** (`kajisho5/ffmpeg-skill`) - 42 local FFmpeg tools plus a
  platform checker. `check.py --platform reels <file>` is the delivery gate.
  It prefers Homebrew's `ffmpeg-full`, which is broken on this machine
  (missing `libx265.216.dylib`); put `/tmp/ffbin` wrappers first on PATH, or
  `brew reinstall ffmpeg-full`, before using it.

- **directing-stickman-videos** (`kaomei/stickman-video-director`) - installed
  at `.agents/skills/directing-stickman-videos/` with `npm ci` run. It is the B1
  "Code Engine" route: authored scene JSON, local render, voiceover and captions.
  `OpenMontage` remains uninstalled (AGPL, 700+ files, Remotion stack).

## Stickman explainer (first film)

- Film: `out/stackshield/stickman-stackshield-explainer.mp4` (720x1280, 15.4s,
  -15 LUFS, burned captions) plus `.srt` beside it.
- Project: `out/stickman/stackshield-explain/`. Three scripts rebuild it:
  `build_voice.py` (Piper narration + measured caption timings), `art.py`
  (draws the cast and props), `build_project.py` (scene JSON from the timing).
- Render: `cd .agents/skills/directing-stickman-videos && node scripts/autopilot.mjs
  preview <project.json> --seconds 15.4 --replace`.
- Voice: local Piper, `data/voices/en_US-ryan-high.onnx`. edge-tts is installed
  but Microsoft now returns 403 for this client, so Piper is the working route.
- The runtime marks the export `pending_independent_review` and
  `delivery_ready: false`. That gate needs a reviewer watching and listening to
  the actual MP4; it is not the same as the technical checks passing.

## Stickman studio (self-serve UI)

- `python3 studio.py` serves http://127.0.0.1:8765 and opens the browser
  (`--port`, `--no-browser` exist). Paste a script, press Render, watch status
  move through scenes -> narration -> render, then play the film in the page.
- Backend is `stickman.py` (stdlib + Piper + the installed skill). One worker
  thread, so renders queue instead of fighting for CPU.
- Output: `out/studio/<job id>/film.mp4` + `film.srt`; job metadata is held in
  memory, so restarting the server clears the recents list but not the files.
- Timing on this M1: 3 lines ~7s, 8 lines ~20s of video in ~35s of wall clock.
- Heading is a one-line uppercase gloss capped at 30 chars (the runtime draws
  text on one line and clips overflow); captions break at ~28 chars, balanced so
  no cue ends with a single word.
- If a render fails, the error is shown in the page status bar and the full
  traceback goes to the server log. Most failures so far were text placed
  outside a declared region: fix the layer geometry, do not widen the region
  past the safe stage (right edge 1100 in the 1200x2000 plane is the limit).
- The server process must own port 8765: `lsof -nP -iTCP:8765 -sTCP:LISTEN`
  before blaming the code, because an older instance silently serves stale
  logic after an edit.

## Things we learned the hard way

- Pixabay ignores `orientation`; identical results for vertical/horizontal/all.
  There is no vertical filter. Vertical sources have to come from elsewhere.
- Stock libraries have no famous faces or meme moments — golf searches return
  drone shots of empty courses. Celebrity footage is copyrighted, so it is not
  fetched here; the user supplies those clips.
- A 16:9 close-up cannot be cropped into a framed 9:16 shot. Pick medium/wide
  shots with space above the subject, or supply vertical footage.
- Roughly 20 old 3D-mascot clips are still in `data/clips/` (~80 MB). Safe to
  delete; nothing in `pairs.txt` uses them.
- Stock clips often carry a silent audio track. `make_reel.py` now measures the
  source and drops audio below -50 dBFS, so the Reels are video-only instead of
  silently broken. Add a track in the app when posting.
