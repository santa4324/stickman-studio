# Reels analyzer

Connects your Instagram account to this folder so an AI (me, in this chat, or any
agent with filesystem access) can read your actual Reels performance and tell you
what to change.

You generate the content in Fastlane. This watches how it performs and explains
why. No SaaS subscription, no rotating free accounts, no scraping.

## Why this exists

Three things a dashboard subscription will never give you:

1. **History.** Instagram serves ~30 days of insight history and ~90 days of
   account metrics. Every day you don't snapshot is a day gone. `sync.py` keeps it.
2. **Your own baseline.** "Is 40% skip rate good?" is unanswerable in the
   abstract. It's answerable against your last 30 Reels.
3. **A join between numbers and content.** Metrics alone say a Reel won.
   Metrics + transcript + hook + pacing say *why*, which is the only version you
   can reuse.

## Setup

### 1. Confirm the account type

Instagram → Settings → Account type. It must be **Business** or **Creator**.
Switching is free and takes about a minute. A personal account cannot expose
insights through any API.

### 2. Get a token (about 5 minutes, once)

1. Go to <https://developers.facebook.com/apps> → **Create app** → use case
   **Manage messaging & content on Instagram** → app type **Business**.
2. Open the app → **Use cases** → **Customise** (there is no "Products" menu any
   more; that screen *is* the product).
3. In the left sidebar pick the setup tab that matches your app:
   - **API setup with Unified login** — the normal choice for a new app. It uses
     `graph.facebook.com` and needs a business portfolio, but no Facebook Page.
   - **API setup with Facebook login** — only if your Instagram account is
     already linked to a Facebook Page.
4. If your app has no business portfolio yet, create one (free, name + email).
5. **Configure your app** → **Create new configuration** → **Edit permissions**
   and make sure `instagram_manage_insights` is included. The default set does
   **not** include it, and without it every metric call comes back empty.
6. **Generate access tokens** → **Add account** → invite your Instagram account
   as a tester → accept the invite in Instagram (**Settings → Website
   permissions → Apps and websites → Tester invites**) → **Generate token** →
   copy it.

A Redirect URL may be demanded when saving the configuration. Any HTTPS URL you
control works (`https://localhost:8443/callback` is fine) — the test token above
does not use it.

You do **not** need a Facebook Page or business verification for your own
account's insights.

### 3. Configure

```bash
cp .env.example .env
# paste the token into IG_ACCESS_TOKEN
```

### 4. Sync and report

```bash
python3 sync.py       # pull every Reel + metrics into data/reels.db
python3 report.py     # rank them against your own baseline
python3 learn.py      # what your winners share, cohort by cohort
```

No dependencies to install for these two — standard library only.

### 5. Optional: transcripts and frames

```bash
brew install ffmpeg              # or: sudo apt install ffmpeg
pip install -r requirements.txt  # yt-dlp + faster-whisper
python3 enrich.py
python3 report.py                # now watch-through is included
```

This downloads each Reel, transcribes it locally with Whisper, detects the CTA,
computes words-per-minute, and saves 6 evenly spaced frames per Reel to
`data/frames/<media_id>/`.

### 6. Ask me

Once `data/report.md` and `data/reels.json` exist, just say:

> read data/report.md and data/reels.json, then tell me what my top 5 Reels have
> in common that my bottom 5 don't

I read those files directly. No API key, no upload, no third party.

## What you get

```
data/
  reels.db        # every snapshot, forever
  report.md       # ranked table + baseline + winners/losers
  learnings.md    # cohorts: caption shape, ask, duration, render -> post join
  reels.json      # same data, structured, for the AI to query
  media/          # downloaded Reels (gitignored)
  frames/<id>/    # storyboard stills
```

`report.md` ranks on four signals, each as a **percentile within your own
cohort**, so a low-view Reel with a great hook still ranks well:

| Signal | Weight | Direction | Why |
|---|---|---|---|
| `reels_skip_rate` | 30% | lower better | first-3-second bail rate — your hook, measured |
| watch-through | 25% | higher better | avg watch time ÷ Reel duration |
| shares / 1k views | 25% | higher better | strongest distribution signal |
| saves / 1k views | 20% | higher better | intent to come back |

Missing signals are dropped and the remaining weights renormalise, so a Reel
with no duration data still gets a fair score.

## What's working, not just what scored

`report.py` answers "which Reel won". `learn.py` answers "what do my winners
have in common": it groups every synced Reel by caption shape (does it open by
selling?), whether the caption asks for anything, and duration band, then writes
`data/learnings.md`.

It is built to resist flattering itself. Cohorts under three Reels are labelled
thin, a skip-rate gap under 3 points is called noise instead of a winner, and
interaction is compared in absolute counts before any per-1k ratio -- one
comment on a 20-view Reel reads as 50 comments per 1k and means nothing. If the
account has almost no interactions in total, it says that rather than ranking
shapes nobody has tested.

The last table joins `data/renders.csv` to your synced Reels by matching the
start of a posted caption to a render's hook, so a Reel made here traces back to
the exact line and ask type that earned it. It stays empty until you post
something rendered here with its caption pasted from the pack.

```bash
python3 sync.py && python3 report.py && python3 learn.py
```

## Keeping it alive

Run the sync daily. Cron:

```cron
15 6 * * * cd /path/to/this/folder && /usr/bin/python3 sync.py >> data/sync.log 2>&1
```

**Token lifetime depends on your setup.** An Instagram Login token lasts 60 days
and can be extended in place:

```bash
python3 sync.py --refresh   # writes the new token into .env
```

A Unified Login / Facebook Login token is a business system-user token and is
not refreshed that way — if it stops working, generate a new one in the
dashboard and replace the line in `.env`.

If any metric comes back empty, check `IG_GRAPH_HOST` and `IG_API_VERSION` in
`.env` — both must match your setup tab (`graph.facebook.com` + `v27.0` for
Unified Login, `graph.instagram.com` for Instagram Login). Meta also returns an
empty set (not zero) for metrics the account doesn't qualify for yet; some Reels
metrics require 100+ followers.

## Honest limits

- **No per-second retention curve.** No API exposes one. Skip rate plus average
  watch time are the closest you get; treat beat-level drop-off as inference,
  not measurement.
- **Metrics lag up to 48h.** A Reel posted this morning has numbers you should
  not trust yet.
- **`impressions` and `plays` were retired in April 2025.** They're replaced by
  `views`. Anything plotting both on one axis is wrong.
- **This reads, it does not publish.** If you want to schedule posts from here,
  add [Postiz](https://github.com/gitroomhq/postiz-app) — but you're generating
  in Fastlane already, so this is probably the wrong side of the problem.

## Stickman explainer studio

A small local web app: paste a script, get a narrated hand-drawn stickman
explainer (9:16, 720x1280, burned captions, SRT next to it). Same look as
`out/stackshield/stickman-stackshield-explainer.mp4`.

```bash
./setup.sh                   # pip + 115 MB voice + render skill (once)
python3 studio.py            # opens http://127.0.0.1:8765 in your browser
python3 studio.py --port 9000 --no-browser
```

Paste one idea per line, press **Render**, and the film appears in the page when
the queue finishes (about 20-40s for a 8-10 line script on the M1). Finished
films live in `out/studio/<job>/film.mp4` with `film.srt`. The page lists
recent renders; nothing leaves the machine and no API key is involved.

How the script becomes a film, for when you want to steer it:

- each line is one shot; the line also becomes the sentence-case caption
- a short uppercase title is pulled from the line for the top of the frame
- a keyword picks the scene art (`app`, `warning`, `stat`, `tables`, `repo`,
  `fix`, `cta`), so words like *Supabase*, *key*, *leak* or *scan* matter
- lead with the problem, put the ask (`stackshield.org`) on the last line
- captions break at ~28 characters so they stay on one line at phone size

Narration is local Piper (`data/voices/en_US-ryan-high.onnx`); the artwork is
drawn by `stickman_art.py`, and the render runs through the installed
`directing-stickman-videos` skill in `.agents/skills/`.

```bash
python3 stickman.py --script notes.txt --out out/stickman/run1   # CLI, no UI
```

## Making Reels locally

`make_reel.py` renders the format the metrics favour: 9:16, silent-or-meme-audio,
2-3 lines of text, no voiceover. ffmpeg plus Pillow, about a second per render, a
gigabyte of disk for a thousand of them, no GPU or model weights — safe on 8 GB.

```bash
python3 clips.py --query "3d character" --preview        # thumbnail sheet, no downloads
python3 clips.py --id 27678                          # fetch just the one you picked
python3 clips.py --query "3d character" --type animation  # 3D / cartoon clips only
python3 clips.py --query golf --provider commons --type all  # keyless, CC-licensed
python3 clips.py --list                              # show what you have
python3 make_reel.py --variants hooks.txt --clip random --seconds 8 --out-dir out/batch
```

A batch deals a **different clip to every hook** when you pass a folder or
`random`, so no two Reels share footage. Name a single file only when you
deliberately want to A/B hooks on one background.

The winning look, from your own account and from `examples/`: real footage, a
medium or wide shot with room above the subject, and a small plain caption near
the top -- around 34px of regular type with a soft shadow, not a bold outlined
headline. The text layer is deliberately quiet so the clip reads as a meme
screenshot instead of an ad. The 3D mascots on plain backgrounds read as stock,
so filter with `--type film`, preview first, and pick the clip whose expression
matches the line.

Frame the shot before you pick it: a close-up on a 16:9 source loses its edges
when cropped to 9:16, so the face fills the phone and the caption lands on
eyelids. Choose clips where the subject is smaller in frame or the camera is
wide, and use `--focus` to slide the crop onto the subject.

`--preview` is worth using: Pixabay keyword search is literal, so `--query "3d
character"` also returns planets and emoji wallpapers. The sheet lets you pick
before spending disk. Sort is by popularity, which is why the same few creators
recur -- `julientromeur` in particular has a whole library of 3D cartoon
characters on plain backgrounds. That look reads as stock; your own top Reels
are real footage, so use it sparingly.

Stock animation clips are **silent**, so lay a track under them:

```bash
python3 make_reel.py --text "me watching the auditor open my access logs" \\
  --clip data/clips/chef-restaurant-3d-27678.mp4 --audio track.mp3
```

Clip providers, in the order `auto` tries them: **pixabay** (key shown at
pixabay.com/api/docs once logged in, no approval), **pexels** (new keys paused
as of Nov 2026), **commons** (Wikimedia, no key at all, but CC licensing usually
requires credit), **local** (files you drop in `data/clips/`).

`--focus 0..1` picks which slice of a landscape clip survives the 9:16 crop
(0 left, 0.5 centre, 1 right).

### The ask -- what actually gets it shown

A hook stops the scroll. An *ask* is what turns the view into a comment, a save
or a send, and those are the three signals the ranking system reads before it
pushes a Reel at strangers. So every render now draws a short ask low in the
frame, and writes a `.txt` pack next to the `.mp4` with the caption, a first
comment to post and pin, and five hashtags:

```bash
python3 make_reel.py --pairs data/pairs.txt               # clip | hook | ask | focus | caption | comment
python3 make_reel.py --text "..." --clip random         # ask picked per hook
python3 make_reel.py --text "..." --cta 'comment "audit" for the checklist'
python3 make_reel.py --text "..." --cta none            # hook only, no ask
python3 caption.py --hook "me checking for leaked secrets before launch"
python3 caption.py --variants hooks.txt --out-dir out   # packs, no renders
python3 caption.py --cta                                # just one ask line
```

Hooks beginning `free:` get a tip-style first comment that actually hands over
the check, and anything mentioning a privacy policy gets a real source
(General Legal's CC0, attorney-drafted US and GDPR templates). A tip Reel that
only teases is just an ad.

The ask type rotates per hook -- comment, save, or share -- chosen from a hash
of the hook text. Re-rendering the same line produces the same pack, which is
what makes an A/B against `data/renders.csv` readable: one variable changes at a
time. No model and no key: the copy is a curated bank in `caption.py`, so edit
the lists there when you find a phrasing that works.

Post the pack's first comment yourself and pin it -- a pinned question is the
cheapest comment section you will ever get.

Clips can also be files you drop into `data/clips/` yourself — screen recordings
of a real scan beat any stock footage, and they are the only content no one else
can copy. Each render is logged to `data/renders.csv` next to the hook, so when
the numbers come back from `report.py` you can tell which line earned them.

Clip audio is kept by default (in a reaction meme the sound is half the joke);
pass `--mute` for silent footage and add trending audio in the Instagram editor.
