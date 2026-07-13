#!/usr/bin/env python3
"""
VoiceType — a menu-bar app for local, private voice typing on macOS.

Hold or double-tap Left Option, talk, and your words are typed into whatever app
you're using. Everything runs on your Mac. No account, no API key, no internet.

The app is three things:
  • a status-bar item (start/stop, language, history)
  • the hotkey listener + transcriber (dictation.py)
  • a thin indicator line at the bottom of the screen so you can see it listening
"""

import os
import sys
import threading
import time
import webbrowser

import rumps

from . import dictation
from . import store

APP_NAME = "VoiceType"
HOME = os.path.expanduser("~/.voicetype")


class VoiceTypeApp(rumps.App):
    def __init__(self):
        super().__init__(APP_NAME, title="🎙", quit_button=None)
        self.lang_item = rumps.MenuItem("Language", callback=None)
        self.menu = [
            rumps.MenuItem("Voice typing  (double-tap Left ⌥)", callback=self._toggle),
            None,
            rumps.MenuItem("English", callback=self._set_en),
            rumps.MenuItem("Swedish", callback=self._set_sv),
            rumps.MenuItem("Auto-detect", callback=self._set_auto),
            None,
            rumps.MenuItem("Show history…", callback=self._history),
            rumps.MenuItem("Shortcuts…", callback=self._shortcuts),
            None,
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        # the indicator line
        self._pill = None
        self._shown = False
        self._mode = "idle"          # idle | wave | load | fail
        self._fail_until = 0.0
        self._phase = 0.0

        self._dict = None
        try:
            self._dict = dictation.Dictation(on_state=self._on_state)
            self._dict.start()
        except Exception as e:
            rumps.notification(APP_NAME, "Could not start",
                               f"{e}\n\nGive Terminal/VoiceType Accessibility permission "
                               "in System Settings → Privacy & Security → Accessibility.")
        self._sync_menu()
        self._timer = rumps.Timer(self._tick, 0.06)
        self._timer.start()

    # ── menu ────────────────────────────────────────────────────────────────
    def _sync_menu(self):
        on = bool(self._dict and self._dict.enabled)
        self.menu["Voice typing  (double-tap Left ⌥)"].state = 1 if on else 0
        self.title = "🎙" if on else "🎙̸"
        lang = store.get_lang()
        for name, key in (("English", "en"), ("Swedish", "sv"), ("Auto-detect", "auto")):
            self.menu[name].state = 1 if lang == key else 0

    def _toggle(self, _):
        if self._dict:
            self._dict.enabled = not self._dict.enabled
        self._sync_menu()

    def _set_lang(self, key):
        store.set_lang(key)
        if self._dict:
            try:
                self._dict.prewarm(key)
            except Exception:
                pass
        self._sync_menu()

    def _set_en(self, _): self._set_lang("en")
    def _set_sv(self, _): self._set_lang("sv")
    def _set_auto(self, _): self._set_lang("auto")

    def _shortcuts(self, _):
        rumps.alert(
            title="VoiceType shortcuts",
            message=(
                "⌥ ⌥   double-tap Left Option — start listening; tap once to stop & type\n"
                "⌥      hold Left Option — push-to-talk; release to type\n"
                "Esc    cancel — stop and throw it away\n"
                "⌃⌘Z   undo — delete what was just typed\n"
                "⌃⌘V   paste your most recent dictation again"
            ))

    def _history(self, _):
        """Write the history to a plain HTML file and open it. No web server needed."""
        items = store.load()
        rows = "\n".join(
            f"<div class=i><div class=t>{time.strftime('%b %d, %H:%M', time.localtime(it['ts']))}</div>"
            f"<div class=x>{_esc(it['text'])}</div></div>"
            for it in items) or "<p class=e>Nothing yet — double-tap Left Option and start talking.</p>"
        html = f"""<!doctype html><meta charset=utf-8><title>VoiceType history</title>
<style>body{{font-family:-apple-system,system-ui,sans-serif;background:#1a1b1e;color:#ececee;
max-width:760px;margin:40px auto;padding:0 24px;line-height:1.55}}
h1{{font-size:20px}} .i{{display:flex;gap:16px;padding:13px 0;border-top:1px solid #303237}}
.t{{flex:none;width:110px;color:#6c6d74;font-size:12.5px}} .x{{white-space:pre-wrap}}
.e{{color:#9a9ba2}}</style>
<h1>VoiceType history <span style="color:#6c6d74;font-weight:400">· {len(items)}</span></h1>
{rows}"""
        p = os.path.join(HOME, "history.html")
        with open(p, "w") as f:
            f.write(html)
        webbrowser.open("file://" + p)

    def _quit(self, _):
        rumps.quit_application()

    # ── the indicator line ──────────────────────────────────────────────────
    def _on_state(self, state):
        if state == "listening":
            self._mode = "wave"
        elif state == "transcribing":
            self._mode = "load"
        elif state == "failed":
            self._mode = "fail"
            self._fail_until = time.time() + 1.4
        else:
            self._mode = "idle"

    def _make_pill(self):
        from AppKit import (NSBackingStoreBuffered, NSColor, NSPanel, NSScreen,
                            NSWindowStyleMaskBorderless, NSView, NSMakeRect)
        scr = NSScreen.mainScreen().frame()
        w, h = 132.0, 26.0
        rect = NSMakeRect((scr.size.width - w) / 2, 26, w, h)
        p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        p.setLevel_(25)                       # above normal windows
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setIgnoresMouseEvents_(True)
        p.setHasShadow_(False)
        try:
            p.setCollectionBehavior_(1 << 0 | 1 << 8)   # all Spaces + fullscreen aux
        except Exception:
            pass
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        view.setWantsLayer_(True)
        p.setContentView_(view)
        self._pill = p
        self._bars = []
        self._build_bars(view, w, h)

    def _build_bars(self, view, w, h):
        from AppKit import NSColor, NSMakeRect
        from Quartz import CALayer
        n = 13
        bw, gap = 3.0, 4.0
        total = n * bw + (n - 1) * gap
        x0 = (w - total) / 2
        for i in range(n):
            lay = CALayer.layer()
            lay.setBackgroundColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.85).CGColor())
            lay.setCornerRadius_(bw / 2)
            lay.setFrame_(NSMakeRect(x0 + i * (bw + gap), h / 2 - 1, bw, 2))
            view.layer().addSublayer_(lay)
            self._bars.append(lay)

    def _position(self):
        from AppKit import NSScreen
        try:
            scr = NSScreen.mainScreen().frame()
            f = self._pill.frame()
            self._pill.setFrameOrigin_(
                ((scr.origin.x + (scr.size.width - f.size.width) / 2),
                 (scr.origin.y + 26)))
        except Exception:
            pass

    def _animate(self):
        import math
        from AppKit import NSColor, NSMakeRect
        from Quartz import CATransaction
        if not self._bars:
            return
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        self._phase += 0.28
        h = 26.0
        for i, lay in enumerate(self._bars):
            f = lay.frame()
            if self._mode == "wave":
                amp = 7.0 * abs(math.sin(self._phase + i * 0.55))
                bh = max(2.0, amp)
                col = NSColor.whiteColor()
            elif self._mode == "load":
                pos = (self._phase * 1.6) % len(self._bars)
                d = min(abs(i - pos), len(self._bars) - abs(i - pos))
                bh = max(2.0, 7.0 * max(0.0, 1.0 - d / 2.4))
                col = NSColor.whiteColor()
            elif self._mode == "fail":
                bh = 2.0
                col = NSColor.systemRedColor()
            else:
                bh = 2.0
                col = NSColor.whiteColor()
            lay.setFrame_(NSMakeRect(f.origin.x, h / 2 - bh / 2, f.size.width, bh))
            lay.setBackgroundColor_(col.colorWithAlphaComponent_(
                0.9 if self._mode != "idle" else 0.35).CGColor())
        CATransaction.commit()

    def _tick(self, _):
        try:
            if self._mode == "fail" and time.time() > self._fail_until:
                self._mode = "idle"
            on = bool(self._dict and self._dict.enabled)
            if on:
                if self._pill is None:
                    self._make_pill()
                if self._pill is not None:
                    self._position()
                    if not self._shown:
                        self._pill.orderFrontRegardless()
                        self._shown = True
                    self._animate()
            elif self._pill is not None and self._shown:
                self._pill.orderOut_(None)
                self._shown = False
        except Exception:
            pass


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    VoiceTypeApp().run()


if __name__ == "__main__":
    main()
