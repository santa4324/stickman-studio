#!/usr/bin/env bash
# One-time local setup for the stickman studio.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> python packages"
python3 -m pip install --quiet piper-tts

echo "==> voice model (115 MB, skipped if present)"
mkdir -p data/voices
for f in en_US-ryan-high.onnx en_US-ryan-high.onnx.json; do
  if [ ! -f "data/voices/$f" ]; then
    curl -fL --progress-bar \
      "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/$f" \
      -o "data/voices/$f"
  fi
done

echo "==> render skill (npm install inside the skill)"
SKILL=".agents/skills/directing-stickman-videos"
if [ ! -d "$SKILL" ]; then
  git clone --depth 1 https://github.com/kaomei/stickman-video-director "$SKILL"
fi
(cd "$SKILL" && npm ci)

echo "==> checking ffmpeg"
if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg missing: brew install ffmpeg   (or: sudo apt install ffmpeg)" >&2
fi

echo
echo "Ready. Start the studio with: python3 studio.py"
