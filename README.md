# VoiceType

**Local voice typing for your MacBook.** Talk, and your words get typed into whatever app you're in.

It's a free alternative to Wispr Flow and Superwhisper:

- **No account.** Nothing to sign up for.
- **No subscription.** It's free, forever.
- **No API key.** There's nothing to configure.
- **Nothing leaves your Mac.** The speech model runs on your own chip, offline.

---

## Install

One command. Paste it into Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/nasjdas/voicetype/main/install.sh | bash
```

That's it — it downloads VoiceType, sets it up, and starts it. A 🎙 shows up in your menu bar.

Then **double-tap Left ⌥ (Option), talk, and tap it once to stop.** Your words appear at the cursor.

<details>
<summary>Two things macOS will ask you for</summary>

<br>

The first time you run it, macOS asks for:

- **Microphone** — so it can hear you.
- **Accessibility** — so it can type into other apps.
  System Settings → Privacy & Security → Accessibility → turn on your terminal app.

The first dictation downloads the speech model (~600 MB, once). After that it's instant, and fully offline.

</details>

<details>
<summary>Prefer to install it by hand?</summary>

<br>

```bash
git clone https://github.com/nasjdas/voicetype.git
cd voicetype
./setup.sh
./run.sh
```

</details>

**Requirements:** an Apple Silicon Mac (M1/M2/M3/M4) and Python 3.10+ (`brew install python@3.12` if you don't have it).

To start it again later: `cd ~/voicetype && ./run.sh`

---

## What it does

You talk, it types. In Slack, in your browser, in your terminal, anywhere the cursor is.

It also cleans up what you said: it strips the *ums* and *uhs*, fixes spacing and capitalisation, and turns "new paragraph" into an actual paragraph break.

| Shortcut | What it does |
|---|---|
| **⌥ ⌥** | double-tap Left Option — start listening. Tap once to stop and type. |
| **⌥ (hold)** | push-to-talk — records while you hold it, types when you let go |
| **Esc** | cancel — stop and throw it away |
| **⌃⌘Z** | undo — delete what it just typed |
| **⌃⌘V** | paste your most recent dictation again |

It speaks **English**, **Swedish**, or **auto-detect** — and it handles you mixing them mid-sentence.

A thin line at the bottom of your screen shows you when it's listening.

---

## How it works

```
Left ⌥  →  mic  →  Parakeet on the Neural Engine  →  cleanup  →  ⌘V at your cursor
```

- **Speech model:** [`parakeet-mlx`](https://github.com/senstella/parakeet-mlx) — NVIDIA's Parakeet, running through Apple's MLX. Roughly 10× faster than Whisper on a Mac, and it hallucinates far less on silence. `mlx-whisper` is kept as a fallback.
- **Hotkeys:** a raw Quartz `CGEventTap` that reads keycodes only.
  *Not* `pynput` — pynput translates every keystroke into a character, which calls a macOS text-input API that **asserts it must run on the main thread**. Called from a listener thread, it hard-crashes the whole process (`SIGTRAP`) while you're just typing. A raw tap never asks what a key *means*, so it never touches that API.
- **Typing:** the text goes onto the clipboard and a synthetic ⌘V is posted. Your clipboard is then put back — **but only if you haven't copied something yourself in the meantime.** (Restoring it unconditionally silently eats whatever you just copied. Ask me how I know.)
- **History:** `~/.voicetype/dictations.json`. Yours, on your disk.

Three files, ~600 lines:

| File | What's in it |
|---|---|
| `voicetype/dictation.py` | hotkeys, recording, transcription, cleanup, typing |
| `voicetype/app.py` | menu-bar app + the listening indicator |
| `voicetype/store.py` | local history |

---

## Privacy

- Your audio **never leaves your machine**. There is no network call anywhere in the transcription path.
- The model runs on-device, on the Neural Engine.
- History lives in `~/.voicetype/`. Delete the folder and it's gone.

---

## Contributing

Issues and PRs welcome. Things that would genuinely help:

- **Configurable hotkeys** — Left ⌥ is hardcoded
- **A proper `.app` bundle** so it doesn't need a terminal
- **Custom vocabulary** — feed it names and jargon so it stops mangling them
- **Streaming** — transcribe while you speak instead of at the end
- **More languages** — Parakeet's multilingual model supports plenty; only the menu is limited
- **Intel Mac support** — Apple Silicon only right now, because MLX is

## Licence

MIT — do what you like with it.
