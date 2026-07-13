#!/usr/bin/env python3
"""
VoiceType — local, system-wide voice typing for macOS.

Left Option (⌥):
  • DOUBLE-TAP → start hands-free (stays listening); single tap to stop & send
  • HOLD       → push-to-talk (record while held, insert on release)

It records the mic, transcribes locally with Whisper, removes filler words,
tidies punctuation, and pastes the text at your cursor in any app.
Needs Accessibility permission (to read the key + type for you).
"""

import re
import subprocess
import sys
import threading
import time
from fractions import Fraction

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

from . import store as dictstore

TARGET_RATE = 16000
# Parakeet on Apple Silicon: ~10x faster than Whisper, far less silence-hallucination.
# English picks the English-only model so it can NEVER drift to another language;
# Swedish / Auto use the multilingual model.
PARAKEET_EN = "mlx-community/parakeet-tdt-0.6b-v2"      # English-only, fastest
PARAKEET_MULTI = "mlx-community/parakeet-tdt-0.6b-v3"   # multilingual (incl. Swedish)
# Fallback engine if Parakeet can't load at all.
MODEL_REPO = "mlx-community/whisper-large-v3-turbo"

_instance = None        # the live Dictation (so the server can prewarm on language change)

_FILLERS = re.compile(r"\b(?:um+|uh+|erm+|ah+|hmm+|uh[\s-]?huh|mm+hmm)\b[,]?", re.I)
_ENTER_TAIL = re.compile(r"[\s,\.]*\b(press enter|send it|send message|hit enter)\b[\s\.\!]*$", re.I)


def clean_text(t: str):
    """Local cleanup: strip fillers, handle voice commands, fix spacing/caps. Returns (text, press_enter)."""
    t = (t or "").strip()
    if not t:
        return "", False
    press_enter = bool(_ENTER_TAIL.search(t))
    t = _ENTER_TAIL.sub("", t).strip()
    t = re.sub(r"\bnew paragraph\b", "\n\n", t, flags=re.I)
    t = re.sub(r"\bnew line\b", "\n", t, flags=re.I)
    t = _FILLERS.sub("", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t).strip()
    t = re.sub(r"\bi\b", "I", t)            # standalone "i" → "I"
    t = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    if t:
        t = t[0].upper() + t[1:]
    return t, press_enter


# Virtual key codes (ANSI / US layout — independent of the active input source).
VK_V = 9
VK_RETURN = 36
VK_DELETE = 51          # backspace

# Keycodes we watch for on the raw event tap.
KC_V = 9
KC_Z = 6
KC_ESC = 53
KC_OPT_L = 58           # Left Option


def _post_key(vk, command=False):
    """Post a key down+up via Quartz CGEvent — thread-safe, unlike pynput typing.

    pynput's typing path calls a macOS Text-Input-Source API that must run on the
    main thread; doing it from a worker thread traps (SIGTRAP) on recent macOS.
    CGEvent posting has no such restriction and ignores the input source.
    """
    import Quartz
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    flags = Quartz.kCGEventFlagMaskCommand if command else 0
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(src, vk, down)
        if flags:
            Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.002)


_paste_lock = threading.Lock()
_clip_saved = None        # the user's real clipboard, captured at the start of a paste burst
_clip_inflight = 0        # how many pastes are mid-flight (restore only when this hits 0)
_paste_gen = 0


def _paste(text, press_enter=False):
    """Paste `text` at the cursor via clipboard + ⌘V, then put the user's clipboard back.

    The restore is the dangerous part. It used to fire unconditionally 0.7s later, so if
    the user copied something in that window we overwrote their fresh copy with the stale
    one we had saved — which is exactly why copy/paste kept "not copying". Now we only put
    the old clipboard back if the clipboard is STILL the text we pasted. The moment the
    user copies anything themselves, that's theirs and we leave it alone."""
    global _clip_saved, _clip_inflight, _paste_gen
    with _paste_lock:
        if _clip_inflight == 0:                       # start of a burst → save the real clipboard
            try:
                _clip_saved = subprocess.run(["pbpaste"], capture_output=True,
                                             text=True, timeout=2).stdout
            except Exception:
                _clip_saved = None
        _clip_inflight += 1
        _paste_gen += 1
        try:
            subprocess.run(["pbcopy"], input=text, text=True, timeout=2)
        except Exception:
            _clip_inflight -= 1
            return
        time.sleep(0.12)                              # give the foreground app time to see it
        _post_key(VK_V, command=True)                # ⌘V
        if press_enter:
            time.sleep(0.04)
            _post_key(VK_RETURN)

    def _restore():
        time.sleep(0.7)
        with _paste_lock:
            global _clip_inflight
            _clip_inflight -= 1
            if _clip_inflight != 0 or _clip_saved is None:
                return                                # not the last of the burst → leave it
            try:
                now = subprocess.run(["pbpaste"], capture_output=True,
                                     text=True, timeout=2).stdout
            except Exception:
                return
            if now != text:
                return              # the user copied something since — that's theirs, keep it
            try:
                subprocess.run(["pbcopy"], input=_clip_saved, text=True, timeout=2)
            except Exception:
                pass
    threading.Thread(target=_restore, daemon=True).start()


class Dictation:
    HOLD_DELAY = 0.32       # held longer than this (alone) → push-to-talk
    TAP_MAX = 0.28          # release within this → a "tap"
    DOUBLE_WINDOW = 0.45    # two taps within this → double-tap (start hands-free)

    def __init__(self, on_state=None):
        self.on_state = on_state          # callback(active: bool) — for the UI pill
        self.enabled = True
        self.recording = False
        self.frames = []
        self.stream = None
        self.native_rate = int(sd.query_devices(kind="input")["default_samplerate"])
        self.lock = threading.Lock()
        self._alt_down = False
        self._alt_press_t = 0.0
        self._last_release = 0.0
        self._other_key = False
        self._ptt = False
        self._handsfree = False
        self._hold_timer = None
        self._listener = None
        self._tap = None
        self._cmd = False
        self._ctrl = False
        self.last_text = ""
        self._last_inserted_len = 0   # chars we just typed (for ⌃⌘Z undo)
        self._last_inserted_at = 0.0
        self.level = 0.0          # live mic level 0..1 (drives the UI waveform)
        self._models = {}         # repo -> loaded Parakeet model
        self._loading = set()     # repos currently downloading/loading
        global _instance
        _instance = self

    def start(self):
        # We listen with a RAW Quartz event tap instead of pynput.
        #
        # pynput's listener turns every keystroke into a character, and to do that macOS
        # calls a Text-Input-Source API (TSMGetInputSourceProperty) that ASSERTS it is on
        # the main thread. pynput calls it from its own event-tap thread, so macOS trapped
        # and killed the whole app (SIGTRAP) — it crashed while the user was simply typing,
        # and a Python try/except cannot catch a native abort.
        #
        # A raw tap only ever reads the keycode + modifier flags. It never asks macOS what
        # character a key means, so the text-input API is never touched and nothing traps.
        threading.Thread(target=self._run_tap, daemon=True).start()
        threading.Thread(target=self._warm, daemon=True).start()

    def _run_tap(self):
        import Quartz
        from CoreFoundation import (CFRunLoopAddSource, CFRunLoopGetCurrent,
                                    CFRunLoopRun, kCFRunLoopCommonModes)

        def _cb(proxy, etype, event, refcon):
            # Runs on the tap thread. Never let anything escape into the C callback.
            try:
                if etype in (Quartz.kCGEventTapDisabledByTimeout,
                             Quartz.kCGEventTapDisabledByUserInput):
                    Quartz.CGEventTapEnable(self._tap, True)   # macOS disabled us → re-arm
                else:
                    self._on_event(etype, event)
            except Exception:
                pass
            return event                                        # listen-only; pass it through

        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) |
                Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp) |
                Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly, mask, _cb, None)
        if not self._tap:
            print("[dictation] no Accessibility permission — hotkeys are off",
                  file=sys.stderr, flush=True)
            return
        src = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), src, kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        CFRunLoopRun()

    def _on_event(self, etype, event):
        """Keycodes + flags only — we never ask macOS to translate a key to a character."""
        import Quartz
        if not self.enabled:
            return
        kc = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
        flags = Quartz.CGEventGetFlags(event)

        if etype == Quartz.kCGEventFlagsChanged:
            if kc == KC_OPT_L:                       # Left Option went down or up
                down = bool(flags & Quartz.kCGEventFlagMaskAlternate)
                if down and not self._alt_down:
                    self._alt_pressed()
                elif not down and self._alt_down:
                    self._alt_released()
            return

        if etype != Quartz.kCGEventKeyDown:
            return
        if kc == KC_ESC:
            if self.recording:
                self._cancel()
            return
        cmd = bool(flags & Quartz.kCGEventFlagMaskCommand)
        ctrl = bool(flags & Quartz.kCGEventFlagMaskControl)
        if cmd and ctrl:
            if kc == KC_V:                           # ⌃⌘V → recover / paste last
                self.paste_last()
                return
            if kc == KC_Z:                           # ⌃⌘Z → undo last dictation
                self.undo_last()
                return
        if self._alt_down:
            self._other_key = True                   # ⌥+key is a shortcut, not dictation

    @staticmethod
    def _repo_for_lang(lang):
        return PARAKEET_EN if lang == "en" else PARAKEET_MULTI

    def _get_model(self, repo, block=True):
        """Return a loaded model, or (block=False) kick off a background load and
        return None if it isn't ready yet."""
        m = self._models.get(repo)
        if m is not None:
            return m
        if not block:
            if repo not in self._loading:
                self._loading.add(repo)
                threading.Thread(target=lambda: self._get_model(repo, True),
                                 daemon=True).start()
            return None
        try:
            from parakeet_mlx import from_pretrained
            m = from_pretrained(repo)
            self._models[repo] = m
            return m
        except Exception:
            return None
        finally:
            self._loading.discard(repo)

    def prewarm_lang(self, lang):
        """Start loading the model for a language (no-op if already loaded)."""
        self._get_model(self._repo_for_lang(lang), block=False)

    def _warm(self):
        """Preload models so the first dictation is instant."""
        # Multilingual model first — already on disk → instant fast fallback for any lang.
        try:
            import mlx.core as mx
            from parakeet_mlx.audio import get_logmel
            m = self._get_model(PARAKEET_MULTI, block=True)
            if m is not None:
                mel = get_logmel(mx.array(np.zeros(TARGET_RATE, np.float32)),
                                 m.preprocessor_config)
                m.generate(mel)                  # trigger Metal kernel JIT
        except Exception:
            pass
        # Then load the model for the currently-selected language in the background.
        self.prewarm_lang(dictstore.get_lang())
        if not self._models:                     # Parakeet unavailable → warm Whisper
            try:
                import mlx_whisper
                mlx_whisper.transcribe(np.zeros(TARGET_RATE, np.float32),
                                       path_or_hf_repo=MODEL_REPO, language="en")
            except Exception:
                pass

    def _transcribe(self, audio):
        lang = dictstore.get_lang()
        repo = self._repo_for_lang(lang)
        m = self._get_model(repo, block=False)   # don't block dictation on a download
        if m is None:                            # chosen model still loading → use any ready one
            m = next(iter(self._models.values()), None)
        if m is not None:
            try:
                import mlx.core as mx
                from parakeet_mlx.audio import get_logmel
                mel = get_logmel(mx.array(audio), m.preprocessor_config)
                return m.generate(mel)[0].text
            except Exception:
                pass
        import mlx_whisper
        wl = lang if lang in ("en", "sv") else None
        res = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL_REPO,
                                     language=wl, condition_on_previous_text=False)
        return res.get("text", "")

    # ---- key handling ----
    def _alt_pressed(self):
        self._alt_down = True
        self._alt_press_t = time.time()
        self._other_key = False
        self._hold_timer = threading.Timer(self.HOLD_DELAY, self._maybe_ptt)
        self._hold_timer.daemon = True
        self._hold_timer.start()

    def _maybe_ptt(self):
        if self._alt_down and not self._other_key and not self._handsfree and not self.recording:
            self._ptt = True
            self._begin()

    def _alt_released(self):
        now = time.time()
        dur = now - self._alt_press_t
        if self._hold_timer:
            self._hold_timer.cancel()
        self._alt_down = False
        if self._ptt and self.recording:       # end push-to-talk
            self._ptt = False
            self._end()
            return
        if dur < self.TAP_MAX and not self._other_key:
            if self.recording and self._handsfree:
                # single tap while listening → stop & send
                self._handsfree = False
                self._end()
                self._last_release = 0.0
            elif not self.recording:
                # idle → require a DOUBLE-tap to start (avoids accidental triggers)
                if (now - self._last_release) < self.DOUBLE_WINDOW:
                    self._last_release = 0.0
                    self._handsfree = True
                    self._begin()
                else:
                    self._last_release = now
            else:
                self._last_release = now
        else:
            self._last_release = now

    # ---- recording ----
    def _set_state(self, state):
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:
                pass

    def _begin(self):
        with self.lock:
            if self.recording:
                return
            self.frames = []
            self.recording = True
        try:
            self.stream = sd.InputStream(samplerate=self.native_rate, channels=1,
                                         dtype="float32", callback=self._cb)
            self.stream.start()
        except Exception:
            self.recording = False
            return
        self._set_state("listening")

    def _cb(self, indata, frames, t, status):
        # Runs on PortAudio's real-time C thread — an exception escaping here would
        # abort the whole app, so never let one out.
        try:
            d = indata[:, 0]
            self.frames.append(d.copy())
            if d.size:
                rms = float(np.sqrt(np.mean(d * d)))
                lv = min(1.0, rms * 9.0)
                self.level = self.level * 0.55 + lv * 0.45
        except Exception:
            pass

    def _cancel(self):
        """Esc — stop recording and throw the audio away (nothing gets typed)."""
        with self.lock:
            if not self.recording:
                return
            self.recording = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        self.stream = None
        self.frames = []
        self.level = 0.0
        self._ptt = False
        self._handsfree = False
        self._set_state("idle")

    def _end(self):
        with self.lock:
            if not self.recording:
                return
            self.recording = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        self.stream = None
        self.level = 0.0
        self._set_state("transcribing")
        threading.Thread(target=self._process, daemon=True).start()

    def _process(self):
        audio = np.concatenate(self.frames) if self.frames else np.zeros(0, np.float32)
        if audio.size < self.native_rate * 0.3:        # too short to be speech
            self._set_state("idle")
            return
        if self.native_rate != TARGET_RATE:
            r = Fraction(TARGET_RATE, self.native_rate).limit_denominator()
            audio = resample_poly(audio, r.numerator, r.denominator).astype(np.float32)
        try:
            raw = self._transcribe(audio)
        except Exception:
            self._set_state("failed")
            return
        text, press_enter = clean_text(raw)
        if text:
            self.last_text = text
            self._last_inserted_len = len(text) + (1 if press_enter else 0)
            self._last_inserted_at = time.time()
            try:
                dictstore.add(text)
            except Exception:
                pass
            _paste(text, press_enter)
        self._set_state("idle")

    def paste_last(self):
        """⌃⌘V — re-paste the most recent dictation at the cursor (recover)."""
        txt = self.last_text or dictstore.latest()
        if not txt:
            return
        self._last_inserted_len = len(txt)
        self._last_inserted_at = time.time()

        def _go():
            time.sleep(0.18)          # let the user's ⌃⌘ keys lift first
            _paste(txt, False)
        threading.Thread(target=_go, daemon=True).start()

    def undo_last(self):
        """⌃⌘Z — delete exactly what we just typed (only right after, and capped,
        so it can never run away deleting unrelated content in whatever app is focused)."""
        n = self._last_inserted_len
        if n <= 0 or n > 2000 or (time.time() - self._last_inserted_at) > 30:
            return
        self._last_inserted_len = 0

        def _go():
            time.sleep(0.18)          # let the user's ⌃⌘ keys lift first
            for _ in range(n):
                _post_key(VK_DELETE)
        threading.Thread(target=_go, daemon=True).start()
