#!/bin/bash
# VoiceType — one-command install.
#
#   curl -fsSL https://raw.githubusercontent.com/nasjdas/voicetype/main/install.sh | bash
#
# Downloads VoiceType, installs it into its own folder, and starts it.
set -e

REPO="https://github.com/nasjdas/voicetype.git"
DIR="${VOICETYPE_DIR:-$HOME/voicetype}"

if [ "$(uname -m)" != "arm64" ]; then
  echo "✗ VoiceType needs an Apple Silicon Mac (M1/M2/M3/M4)."
  echo "  The speech model runs on Apple MLX, which is Apple Silicon only."
  exit 1
fi

if [ -f "./voicetype/dictation.py" ]; then
  DIR="$PWD"                                   # already inside a clone
elif [ -d "$DIR/.git" ]; then
  echo "→ updating $DIR"
  git -C "$DIR" pull --ff-only --quiet || true
else
  command -v git >/dev/null 2>&1 || {
    echo "✗ git isn't installed. Run this first:  xcode-select --install"
    exit 1
  }
  echo "→ downloading VoiceType into $DIR"
  git clone --quiet "$REPO" "$DIR"
fi

cd "$DIR"
./setup.sh
echo "→ starting VoiceType (Ctrl-C to stop it; run ./run.sh from $DIR to start it again)"
exec ./run.sh
