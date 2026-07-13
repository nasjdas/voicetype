# VoiceType

**Local voice typing for macOS.** Hold a key, talk, and your words get typed into whatever app you're in.

No account. No subscription. No API key. **Nothing leaves your Mac.**

It's a free, private alternative to Wispr Flow / Superwhisper. The speech model runs on your Apple Silicon chip.

---

## What it does

Double-tap **Left ⌥**, talk, tap once to stop — your words appear at the cursor. In Slack, in your browser, in your terminal, anywhere.

It also cleans up what you said: strips the *ums* and *uhs*, fixes the spacing and capitalisation, and turns "new paragraph" into an actual paragraph break.

| Shortcut | What it does |
|---|---|
| **⌥ ⌥** | double-tap Left Option — start listening. Tap once to stop and type. |
| **⌥ (hold)** | push-to-talk — record while held, types when you let go |
| **Esc** | cancel — stop and throw it away |
| **⌃⌘Z** | undo — delete what it just typed |
| **⌃⌘V** | paste your most recent dictation again |

Speaks **English**, **Swedish**, or **auto-detect** — and it handles mixing them mid-sentence.

---

## Install

Needs an **Apple Silicon Mac** (M1/M2/M3/M4) and **Python 3.10+**.

```bash
git clone https://github.com/YOURNAME/voicetype.git
cd voicetype
./setup.sh
./run.sh
```

The first run asks for two macOS permissions:

- **Microphone** — so it can hear you
- **Accessibility** — so it can type into other apps
  (System Settings → Privacy & Security → Accessibility → enable your terminal app)

The first dictation downloads the speech model (~600 MB, once). After that it's instant and fully offline.

A 🎙 appears in your menu bar. A thin line at the bottom of the screen shows you when it's listening.

---

## How it works

```
Left ⌥  →  mic (sounddevice)  →  Parakeet on the Neural Engine  →  cleanup  →  ⌘V at your cursor
```

- **Speech model:** [`parakeet-mlx`](https://github.com/senstella/parakeet-mlx) — NVIDIA's Parakeet, running through Apple's MLX. It's roughly 10× faster than Whisper on a Mac and hallucinates far less on silence. `mlx-whisper` is kept as a fallback.
- **Hotkeys:** a raw Quartz `CGEventTap` reading keycodes only.
  *Not* `pynput` — pynput translates each keystroke into a character, which calls a macOS text-input API that **asserts it must run on the main thread**. Called from a listener thread, it hard-crashes the process (`SIGTRAP`) while you're just typing. A raw tap never asks what a key *means*, so it never touches that API.
- **Typing:** the text goes on the clipboard and a synthetic ⌘V is posted. Your clipboard is put back afterwards — **but only if you haven't copied something yourself in the meantime.** (Restoring it unconditionally silently eats whatever you just copied. Ask me how I know.)
- **History:** stored in `~/.voicetype/dictations.json`. Yours, on your disk. Nothing is uploaded, ever.

Three files, ~600 lines:

| File | What's in it |
|---|---|
| `voicetype/dictation.py` | hotkeys, recording, transcription, cleanup, typing |
| `voicetype/app.py` | menu-bar app + the listening indicator |
| `voicetype/store.py` | local history |

---

## Privacy

- Audio **never leaves your machine**. There is no network call in the transcription path.
- The model runs on-device via MLX.
- History lives in `~/.voicetype/`. Delete the folder and it's gone.

---

## Contributing

Issues and PRs welcome. Things that would genuinely help:

- **Configurable hotkeys** (Left ⌥ is hardcoded)
- **More languages** — Parakeet's multilingual model supports plenty; only the menu is limited
- **Intel Mac support** — currently Apple Silicon only, because MLX is
- **A proper `.app` bundle** so it doesn't need a terminal
- **Custom vocabulary** — feed names/jargon to the model so it stops mangling them
- **Streaming** — transcribe while you speak instead of at the end

## Licence

MIT — do what you like with it.
