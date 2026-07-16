# Testing

## The honest state of the Windows port

The Windows backend was written on a Mac. I don't own a Windows machine, so I'll say plainly what is proven and what isn't.

### Proven by CI, on a real Windows runner, every push

- Everything compiles and lints.
- `pip install -r requirements.txt` resolves on Windows — and **no Apple libraries leak in** (this matters more than it sounds: `mlx` now publishes Windows wheels, so without the environment markers a Windows user would silently install Apple's ML stack, drag in all of PyTorch through `mlx-whisper`, and get an engine that cannot run. No error. It just wouldn't work).
- `WinPlatform` constructs and every Win32 symbol binds.
- The clipboard round-trips real Unicode through `OpenClipboard`/`SetClipboardData` — including `åäö` and emoji.
- `SetWindowsHookEx(WH_KEYBOARD_LL)` installs and uninstalls.
- The engine picker chooses ONNX, and the store and dashboard work on Windows paths.
- The dashboard still refuses an unauthenticated caller there.

### Proven on a Mac, which is enough for these

- **The speech engine.** ONNX Runtime runs on macOS too, so the *Windows* engine was tested here on real audio, with the CPU-only execution provider — no CoreML, no GPU — which is exactly what a GPU-less Windows laptop gets.
  - 2.7s of speech → 0.11s (**~24× realtime**)
  - 180s of speech → 8.2s, complete, nothing truncated
  - English and Swedish both transcribe correctly.
- **All the shared logic** — the cleanup rules, vocabulary, snippets, the paste/restore algorithm and the undo guards — is platform-free and unit-tested, including with a fake clipboard.

### NOT proven — this is what needs a human

Nobody has held a key down on a real Windows desktop. CI can't press keys. Specifically unverified:

1. **Right Ctrl as the modifier.** Chosen because Left Alt opens menu bars everywhere and Right Alt is AltGr on Swedish layouts (it types `@ \ $ €` — stealing it would be hostile). Right Ctrl *should* be free. Untested in the wild.
2. **The tap/hold/double-tap feel.** The timings (0.32s hold, 0.28s tap, 0.45s double-tap window) were tuned on a Mac. They may want different numbers on Windows.
3. **`paste_settle = 0.06`.** How long to wait after setting the clipboard before pressing Ctrl+V. On macOS this is 0.12 because Cocoa reads the pasteboard lazily and pasting too early pastes the *previous* clipboard. Win32's `SetClipboardData` is synchronous so it should need less — but 0.06 is an educated guess, not a measurement. **If pasting on Windows sometimes inserts the wrong text, this number is the first suspect.**
4. **The overlay.** A frameless click-through tkinter window. Whether `WS_EX_TRANSPARENT | WS_EX_LAYERED` actually makes it click-through, and whether it stays out of the taskbar, is untested.
5. **The tray icon** and its menu, including whether pystray's pull-model checkmarks render right.
6. **Injected-event filtering.** We check `LLKHF_INJECTED` so our own synthetic keystrokes don't re-enter the hook. If this is wrong, an undo of 200 characters re-enters the handler 200 times. Reasoned carefully; not observed.
7. **The installer end to end** on a clean machine — especially the no-Python path and the Microsoft Store stub.

If you're on Windows and something misbehaves, an issue with what you did and what happened is genuinely the most useful thing you can send.

## Running the tests

```bash
./.venv/bin/python -m unittest discover -s tests -v
```

## Testing the Windows speech engine on a Mac

```bash
VOICETYPE_ENGINE=onnx ./.venv/bin/python -m voicetype
```

This forces the ONNX path — the same code Windows runs. It's the fastest way to catch an engine bug without a Windows box.
