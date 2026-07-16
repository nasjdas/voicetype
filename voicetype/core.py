#!/usr/bin/env python3
"""
The engine room — recording, the tap/hold gesture, pasting, undo.

Everything here is platform-free. The OS only shows up as `self.p`, a Platform.
That's deliberate: this file holds the logic that took real bugs to get right, and
it must exist exactly once or the two builds drift.

Audio needs no abstraction at all — sounddevice, numpy and scipy all run on both
platforms unchanged, so the fiddliest code in the project ports for free.
"""

import threading
import time
from fractions import Fraction

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

from . import store
from .asr import TARGET_RATE, get_engine
from .platform import ESC, KEY_V, KEY_Z, MOD
from .text import clean_text


class Dictation:
    HOLD_DELAY = 0.32       # held longer than this, alone → push-to-talk
    TAP_MAX = 0.28          # released within this → a "tap"
    DOUBLE_WINDOW = 0.45    # two taps inside this → double-tap

    def __init__(self, plat, on_state=None):
        self.p = plat
        self.on_state = on_state
        self.enabled = True
        self.recording = False
        self.frames = []
        self.stream = None
        self.level = 0.0            # live mic level 0..1 — drives the overlay
        self.last_text = ""

        self.lock = threading.Lock()
        self._mod_down = False
        self._mod_press_t = 0.0
        self._last_release = 0.0
        self._other_key = False
        self._ptt = False
        self._handsfree = False
        self._hold_timer = None
        self._rate = TARGET_RATE

        self._last_len = 0          # chars we just typed, for undo
        self._last_at = 0.0

        self._paste_lock = threading.Lock()
        self._clip_saved = None
        self._clip_inflight = 0

        self.engine = get_engine()

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self):
        ok = self.p.hotkeys.start(self._on_key)
        threading.Thread(target=self._warm, daemon=True).start()
        return ok

    def _warm(self):
        warm = getattr(self.engine, "warm_all", None)
        if warm:
            warm()
        self.engine.prewarm(store.get_lang())

    def prewarm_lang(self, lang):
        self.engine.prewarm(lang)

    # ── keys ────────────────────────────────────────────────────────────────
    def _on_key(self, ev):
        """Normalised key events in; gesture decisions made here, once, for both
        platforms. The platform layer never decides what a key means."""
        if not self.enabled:
            return
        if ev.key == MOD:
            if ev.down:
                self._mod_pressed()
            else:
                self._mod_released()
            return
        if not ev.down:
            return
        if ev.key == ESC:
            if self.recording:
                self.cancel()
            return
        if ev.cmd and ev.ctrl:
            if ev.key == KEY_V:
                self.paste_last()
                return
            if ev.key == KEY_Z:
                self.undo_last()
                return
        if self._mod_down:
            self._other_key = True      # mod+key is a shortcut, not dictation

    def _mod_pressed(self):
        self._mod_down = True
        self._mod_press_t = time.time()
        self._other_key = False
        self._hold_timer = threading.Timer(self.HOLD_DELAY, self._maybe_ptt)
        self._hold_timer.daemon = True
        self._hold_timer.start()

    def _maybe_ptt(self):
        with self.lock:
            if not (self._mod_down and not self._other_key
                    and not self._handsfree and not self.recording):
                return
            self._ptt = True
        self._begin()

    def _mod_released(self):
        now = time.time()
        dur = now - self._mod_press_t
        if self._hold_timer:
            self._hold_timer.cancel()
        self._mod_down = False

        # Timer.cancel() is a no-op once the callback has started, so _maybe_ptt
        # may be mid-flight right now. Claiming _ptt under the lock means we
        # can't lose the release and strand the mic open forever.
        with self.lock:
            was_ptt = self._ptt
            if was_ptt:
                self._ptt = False
        if was_ptt:
            self._end()
            return

        if dur < self.TAP_MAX and not self._other_key:
            if self.recording and self._handsfree:
                self._handsfree = False         # tap while listening → stop & send
                self._end()
                self._last_release = 0.0
                return
            if not self.recording:
                if (now - self._last_release) < self.DOUBLE_WINDOW:
                    self._last_release = 0.0    # double-tap → hands-free
                    self._handsfree = True
                    self._begin()
                    return
        self._last_release = now

    # ── recording ───────────────────────────────────────────────────────────
    def _set_state(self, s):
        if self.on_state:
            try:
                self.on_state(s)
            except Exception:
                pass

    def _begin(self):
        with self.lock:
            if self.recording:
                return
            self.frames = []
            self.recording = True
        try:
            # Read the rate from the device NOW, not once at startup — plugging in
            # AirPods or a USB mic changes it, and a stale rate resamples the audio
            # to the wrong speed and transcribes as garbage.
            self._rate = int(sd.query_devices(kind="input")["default_samplerate"])
            self.stream = sd.InputStream(samplerate=self._rate, channels=1,
                                         dtype="float32", callback=self._cb)
            self.stream.start()
        except Exception:
            with self.lock:
                self.recording = False
            self._ptt = self._handsfree = False
            self._set_state("failed")
            return
        self._set_state("listening")

    def _cb(self, indata, frames, t, status):
        # PortAudio's real-time C thread — an exception escaping here aborts the
        # whole process, so nothing may leak out.
        try:
            d = indata[:, 0]
            self.frames.append(d.copy())
            if d.size:
                rms = float(np.sqrt(np.mean(d * d)))
                self.level = self.level * 0.55 + min(1.0, rms * 9.0) * 0.45
        except Exception:
            pass

    def _close_stream(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        self.stream = None
        self.level = 0.0

    def cancel(self):
        """Esc — stop and throw the audio away."""
        with self.lock:
            if not self.recording:
                return
            self.recording = False
        self._close_stream()
        self.frames = []
        self._ptt = self._handsfree = False
        self._set_state("idle")

    def _end(self):
        with self.lock:
            if not self.recording:
                return
            self.recording = False
        self._close_stream()
        self._set_state("transcribing")
        threading.Thread(target=self._process, daemon=True).start()

    def _process(self):
        audio = np.concatenate(self.frames) if self.frames else np.zeros(0, np.float32)
        dur = len(audio) / float(self._rate or TARGET_RATE)
        if dur < 0.3:                      # too short to be speech
            self._set_state("idle")
            return
        if self._rate != TARGET_RATE:
            r = Fraction(TARGET_RATE, self._rate).limit_denominator()
            audio = resample_poly(audio, r.numerator, r.denominator).astype(np.float32)

        lang = store.get_lang()
        try:
            raw = self.engine.transcribe(audio, lang)
        except Exception:
            self._set_state("failed")
            return

        text, press_enter = clean_text(
            raw, lang=lang,
            vocab=store.get_vocab(),
            snippets=store.get_snippets(),
            corrections=store.get_corrections())
        if text:
            self.last_text = text
            self._last_len = len(text) + (1 if press_enter else 0)
            self._last_at = time.time()
            try:
                store.add(text, dur=dur, lang=lang, engine=self.engine.name)
            except Exception:
                pass
            self._paste(text, press_enter)
        self._set_state("idle")

    # ── typing ──────────────────────────────────────────────────────────────
    def _paste(self, text, press_enter=False):
        """Put the text on the clipboard, press paste, then put the user's
        clipboard back — but ONLY if they haven't copied something themselves.

        The restore used to fire unconditionally 0.7s later, which silently ate
        whatever the user had just copied. That is the bug behind "copy/paste keeps
        not working". Each of the three guards below fixes a different half of it;
        none is redundant, and the conditional at the end is the actual fix.
        """
        with self._paste_lock:
            if self._clip_inflight == 0:
                # Save ONCE per burst. Re-saving mid-burst would capture our own
                # dictation text as "the user's clipboard" and restore that later.
                self._clip_saved = self.p.clipboard.get_text()
            self._clip_inflight += 1
            if not self.p.clipboard.set_text(text):
                self._clip_inflight -= 1
                return
            # Inside the lock on purpose: it serialises the copy→paste pair so two
            # concurrent pastes can't interleave and paste each other's text.
            time.sleep(self.p.paste_settle)
            self.p.keys.paste()
            if press_enter:
                time.sleep(0.04)
                self.p.keys.enter()

        def _restore():
            time.sleep(0.7)
            with self._paste_lock:
                self._clip_inflight -= 1
                if self._clip_inflight != 0 or self._clip_saved is None:
                    return                  # a later paste still needs the clipboard
                now = self.p.clipboard.get_text()
                if now != text:
                    return                  # they copied something — it's theirs
                self.p.clipboard.set_text(self._clip_saved)
        threading.Thread(target=_restore, daemon=True).start()

    def paste_last(self):
        """⌃⌘V / Ctrl+Ctrl+V — paste the most recent dictation again."""
        txt = self.last_text or store.latest()
        if not txt:
            return
        self._last_len = len(txt)
        self._last_at = time.time()

        def _go():
            time.sleep(0.18)        # let their modifier keys lift first
            self._paste(txt, False)
        threading.Thread(target=_go, daemon=True).start()

    def undo_last(self):
        """⌃⌘Z — delete exactly what we just typed.

        All three guards are safety rails: we're sending backspaces into whatever
        app happens to be focused, so this must never run away.
        """
        n = self._last_len
        if n <= 0 or n > 2000 or (time.time() - self._last_at) > 30:
            return
        self._last_len = 0          # single-shot: a second undo is a no-op

        def _go():
            time.sleep(0.18)        # backspacing while Ctrl is held = delete-word
            self.p.keys.backspace(n)
        threading.Thread(target=_go, daemon=True).start()
