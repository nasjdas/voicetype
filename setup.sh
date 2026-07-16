#!/bin/bash
# One-time setup for VoiceType on macOS. (Windows: use install.ps1.)
set -e
cd "$(dirname "$0")"

# No Apple-Silicon gate any more. Intel Macs skip MLX and run the same Parakeet
# model through ONNX Runtime instead — see requirements.txt.
if [ "$(uname -s)" != "Darwin" ]; then
  echo "✗ This script is for macOS."
  echo "  On Windows, run this in PowerShell instead:"
  echo '      powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/nasjdas/voicetype/main/install.ps1 | iex"'
  exit 1
fi

# macOS ships Python 3.9, but the speech libraries need 3.10+. Find a real one.
# Getting this wrong is silent and nasty: pip "succeeds", then the import fails.
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    V=$("$c" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)
    if [ "$V" -ge 310 ]; then PY="$c"; break; fi
  fi
done

if [ -z "$PY" ]; then
  echo "✗ Need Python 3.10 or newer (macOS ships 3.9, which is too old)."
  echo
  echo "  Install it:"
  echo "      brew install python@3.12"
  echo "  then run ./setup.sh again."
  exit 1
fi

echo "→ using $($PY --version) on $(uname -m)"
rm -rf .venv
"$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip -q
echo "→ installing dependencies (the speech library is big — a few minutes the first time)"
./.venv/bin/pip install -r requirements.txt

echo "→ checking everything actually imported"
./.venv/bin/python - <<'CHECK'
import platform
import sys

mods = ["rumps", "Quartz", "numpy", "scipy", "sounddevice"]
if platform.machine() == "arm64":
    mods += ["mlx.core", "parakeet_mlx"]      # Apple Silicon → Neural Engine
else:
    mods += ["onnx_asr"]                      # Intel Mac → same model via ONNX

bad = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        bad.append("%s: %s" % (m, e))
if bad:
    print("✗ these failed to import:")
    for b in bad:
        print("   ", b)
    sys.exit(1)
print("✓ all good")
CHECK

cat <<'MSG'

✓ Setup done.

Start it:      ./run.sh

The FIRST time you run it, macOS asks for two permissions:
  • Microphone     — so it can hear you
  • Accessibility  — so it can type into other apps
    (System Settings → Privacy & Security → Accessibility → enable your terminal)

Then: double-tap Left Option, talk, tap once to stop.
Your words get typed wherever the cursor is.

The first dictation downloads the speech model (~670 MB, once).
After that it's fully offline.
MSG
