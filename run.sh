#!/bin/bash
# Start VoiceType.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { echo "Run ./setup.sh first."; exit 1; }
exec .venv/bin/python -m voicetype
