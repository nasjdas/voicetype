# VoiceType

**Local voice typing for Mac and Windows.** Talk, and your words get typed into whatever app you're in.

It's a free alternative to Wispr Flow and Superwhisper:

- **No account.** Nothing to sign up for.
- **No subscription.** It's free, forever.
- **No API key.** There's nothing to configure.
- **Nothing leaves your machine.** The speech model runs on your own chip, offline.

That last one is the whole point. Wispr Flow has no offline mode at any price — every word you speak goes to their servers, and [their own docs](https://docs.wisprflow.ai/articles/4678293671-feature-context-awareness) say it also uploads a screenshot of your active window. VoiceType makes no network call in the entire dictation path. It works on a plane.

---

## Install

One command. Paste it into a terminal:

**Mac**
```bash
curl -fsSL https://raw.githubusercontent.com/nasjdas/voicetype/main/install.sh | bash
```

**Windows** *(PowerShell)*
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/nasjdas/voicetype/main/install.ps1 | iex"
```

That's it — it downloads VoiceType, installs Python if you don't have it, and starts it. A 🎙 shows up in your menu bar (Mac) or near the clock (Windows).

Then **double-tap the key, talk, and tap it once to stop.** Your words appear at the cursor.

| | key |
|---|---|
| **Mac** | Left **⌥** (Option) |
| **Windows** | Right **Ctrl** |

<details>
<summary>What your computer will ask for</summary>

<br>

**Mac** wants two permissions the first time:

- **Microphone** — so it can hear you.
- **Accessibility** — so it can type into other apps.
  System Settings → Privacy & Security → Accessibility → turn on your terminal app.

**Windows** just asks for the microphone. No admin rights needed at any point.

The first dictation downloads the speech model (~670 MB, once). After that it's instant, and fully offline.

</details>

<details>
<summary>Prefer to install it by hand?</summary>

<br>

```bash
git clone https://github.com/nasjdas/voicetype.git
cd voicetype
./setup.sh        # Windows: .\install.ps1
./run.sh
```

</details>

**Needs:** macOS (Apple Silicon or Intel) or Windows 10/11, and Python 3.10+. The installer handles Python on Windows; on Mac, `brew install python@3.12` if you don't have it.

---

## What it does

You talk, it types. In Slack, in your browser, in your terminal, anywhere the cursor is.

It also cleans up what you said: strips the *ums* and *uhs*, fixes spacing and capitalisation, and turns "new paragraph" into an actual paragraph break.

| Shortcut | What it does |
|---|---|
| **tap tap** | double-tap the key — start listening. Tap once to stop and type. |
| **hold** | push-to-talk — records while you hold it, types when you let go |
| **Esc** | cancel — stop and throw it away |
| **⌃⌘Z** | undo — delete what it just typed |
| **⌃⌘V** | paste your most recent dictation again |

Speaks **English** and **Swedish**, and handles you mixing them mid-sentence.

A thin line at the bottom of your screen shows you when it's listening.

### The dashboard

Open it from the menu. Everything you've ever dictated, searchable, with one-click copy.

- **History** — browse and search everything, copy any line back out, delete what you don't want.
- **Stats** — words dictated, time saved, streak, words-per-day.
- **Words** — teach it names and jargon so it stops mangling them.
- **Snippets** — say a phrase, get a whole block of text.
- **Settings** — language, start-at-login, and delete-everything.

It's served from `127.0.0.1` and never binds a public port. Your history never leaves the machine.

---

## How it works

```
key  →  mic  →  Parakeet (on-device)  →  cleanup  →  paste at your cursor
```

- **Speech model:** NVIDIA's [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3). On Apple Silicon it runs through [MLX](https://github.com/senstella/parakeet-mlx) on the Neural Engine; on Windows and Intel Macs the same weights run through [ONNX Runtime](https://github.com/istupakov/onnx-asr). Same model either way.

- **Why not Whisper?** Whisper picks *one language per 30-second window*, and a push-to-talk utterance is one window — so "jag tycker det är en **really good idea**" gets forced entirely into one language. Parakeet's tokenizer has no such gate. Whisper is also too slow on a CPU at the accuracy we need (large-v3 runs *slower than realtime*), while Parakeet does ~24× realtime on a plain CPU, no GPU required.

- **Hotkeys:** a raw Quartz `CGEventTap` (Mac) / `SetWindowsHookEx` (Windows), reading keycodes only.
  *Not* `pynput` — on macOS it translates each keystroke into a character, which calls a text-input API that **asserts it must run on the main thread**. From a listener thread that hard-crashes the whole process (`SIGTRAP`) while you're just typing. A raw hook never asks what a key *means*, so it never touches that API.

- **Typing:** the text goes on the clipboard and a synthetic paste is posted. Your clipboard is then put back — **but only if you haven't copied something yourself in the meantime.** (Restoring it unconditionally silently eats whatever you just copied. Ask me how I know.)

- **History:** `~/.voicetype/history.db`, SQLite, mode 0600. Yours, on your disk.

### Layout

```
voicetype/
  core.py          the state machine, paste, undo — shared, platform-free
  text.py          cleanup, vocabulary, snippets — pure functions
  store.py         history + settings (sqlite)
  dashboard.py     the local web server (stdlib only)
  asr/             the speech engines
    mlx_parakeet.py    Apple Silicon → Neural Engine
    onnx_parakeet.py   Windows / Intel Mac → same model via ONNX
  platform/        the only place an OS is allowed to leak in
    base.py            the contract: 5 small pieces
    macos.py           Quartz, pbcopy, rumps, NSPanel
    windows.py         Win32 hooks, clipboard, SendInput, pystray, tkinter
```

The rule: the platform layer reports **key events**, it never decides what they *mean*. All the timing, cleanup and paste logic lives above it, exactly once, so the two builds can't drift.

---

## Privacy

- Your audio **never leaves your machine**. There is no network call in the transcription path.
- The model runs on-device.
- History lives in `~/.voicetype/`, readable only by you. Delete the folder and it's gone.
- The dashboard binds `127.0.0.1` on a random port, requires a token that's generated fresh each time and never written to disk, and rejects any request whose `Host` header isn't ours (which is what stops a malicious web page from reading your history via DNS rebinding).

---

## Contributing

Issues and PRs welcome. Genuinely useful right now:

- **Windows testing.** The Windows port was written on a Mac and is verified by [CI](.github/workflows/ci.yml) — it compiles, the Win32 calls bind, the clipboard round-trips, the hook installs. But **nobody has held the key down on a real Windows desktop yet.** If you do, tell me what broke. See [TESTING.md](TESTING.md).
- **Configurable hotkeys** — the modifier is per-platform but not yet user-settable.
- **Linux** — the platform interface is `voicetype/platform/base.py`. Five small pieces.
- **A proper app bundle** so it doesn't need a terminal.
- **More languages** — Parakeet v3 does 25; only the menu is limited.

## Credits

Speech model: [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) by NVIDIA, CC-BY-4.0.

## Licence

MIT — do what you like with it.
