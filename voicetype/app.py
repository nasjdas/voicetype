#!/usr/bin/env python3
"""
VoiceType — local voice typing for macOS and Windows.

Talk, and your words get typed into whatever app you're in. Everything runs on
your machine: no account, no API key, no internet.

This file is deliberately thin. The OS work lives in platform/, the speech engine
in asr/, and the logic that matters in core.py — so this is just wiring.
"""

import threading
import time

from . import autostart, dashboard, store
from . import platform as plat
from .core import Dictation

APP = "VoiceType"


class App:
    def __init__(self):
        self.p = plat.get()
        self.dict = None
        self._mode = "idle"
        self._fail_until = 0.0

    # ── menu ────────────────────────────────────────────────────────────────
    def _menu(self):
        mod = self.p.hotkeys.modifier_name
        return [
            {"id": "toggle", "title": "Voice typing  (double-tap %s)" % mod,
             "action": self._toggle, "checkable": True},
            None,
            {"id": "dash", "title": "Dashboard…", "action": self._dashboard},
            None,
            {"id": "en", "title": "English", "action": lambda: self._lang("en"),
             "checkable": True},
            {"id": "sv", "title": "Svenska", "action": lambda: self._lang("sv"),
             "checkable": True},
            {"id": "auto", "title": "Auto-detect", "action": lambda: self._lang("auto"),
             "checkable": True},
            None,
            {"id": "keys", "title": "Shortcuts…", "action": self._shortcuts},
            None,
            {"id": "quit", "title": "Quit", "action": self._quit},
        ]

    def _sync(self):
        on = bool(self.dict and self.dict.enabled)
        self.p.tray.set_active(on)
        self.p.tray.set_checked("toggle", on)
        lang = store.get_lang()
        for k in ("en", "sv", "auto"):
            self.p.tray.set_checked(k, lang == k)

    def _toggle(self):
        if self.dict:
            self.dict.enabled = not self.dict.enabled
            if not self.dict.enabled and self.dict.recording:
                self.dict.cancel()
        self._sync()

    def _lang(self, key):
        store.set_lang(key)
        if self.dict:
            self.dict.prewarm_lang(key)     # start loading it now, not on first use
        self._sync()

    def _dashboard(self):
        dashboard.open_in_browser(
            dictation=self.dict,
            modifier=self.p.hotkeys.modifier_name,
            engine=self.dict.engine.name if self.dict else "")

    def _shortcuts(self):
        mod = self.p.hotkeys.modifier_name
        self.p.tray.alert(APP + " shortcuts", "\n".join([
            "%s %s   double-tap — start listening; tap once to stop & type" % (mod, mod),
            "%s      hold — push-to-talk; release to type" % mod,
            "Esc    cancel — stop and throw it away",
            "⌃⌘Z   undo — delete what was just typed",
            "⌃⌘V   paste your most recent dictation again",
        ]))

    def _quit(self):
        dashboard.stop()
        if self.dict:
            self.p.hotkeys.stop()
        self.p.tray.quit()

    # ── the listening indicator ─────────────────────────────────────────────
    def _on_state(self, state):
        if state == "failed":
            self._mode = "fail"
            self._fail_until = time.time() + 1.4
        else:
            self._mode = {"listening": "wave", "transcribing": "load"}.get(state, "idle")

    def _tick(self):
        try:
            if self._mode == "fail" and time.time() > self._fail_until:
                self._mode = "idle"
            if self.dict and self.dict.enabled:
                self.p.overlay.render(self._mode, self.dict.level)
            else:
                self.p.overlay.hide()
        except Exception:
            pass

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self):
        ok = False
        try:
            self.dict = Dictation(self.p, on_state=self._on_state)
            ok = self.dict.start()
        except Exception as e:
            print("[voicetype] %s" % e)

        if not ok:
            mac = self.p.__class__.__name__ == "MacPlatform"

            def _warn():
                time.sleep(1.0)
                self.p.tray.alert(
                    "VoiceType can't see your keyboard",
                    ("Give it Accessibility permission, then restart it.\n\n"
                     "System Settings → Privacy & Security → Accessibility → "
                     "enable your terminal app.") if mac else
                    "Windows blocked the keyboard hook. Try restarting VoiceType.")
            threading.Thread(target=_warn, daemon=True).start()

        store.set_setting("autostart", autostart.get())
        self.p.tray.every(0.06, self._tick)
        self._sync()
        self.p.tray.run(self._menu())      # blocks — owns the main thread


def main():
    App().run()


if __name__ == "__main__":
    main()
