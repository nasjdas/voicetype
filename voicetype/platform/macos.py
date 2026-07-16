#!/usr/bin/env python3
"""
macOS backend — Quartz event tap, pbcopy/pbpaste, rumps menu bar, NSPanel overlay.

This is the original, working code moved behind the platform interface. Behaviour
is deliberately unchanged; every magic constant here was tuned against real apps.
"""

import math
import subprocess
import sys
import threading
import time

import rumps

from .base import (ESC, KEY_V, KEY_Z, MOD, OTHER, Clipboard, Hotkeys, KeyEvent,
                   Keystrokes, Overlay, Platform, Tray)

# macOS ANSI virtual keycodes.
VK_V = 9
VK_RETURN = 36
VK_BACKSPACE = 51      # NOT "delete" — Mac keyboards label backspace "delete".
                       # Mapping this to Windows VK_DELETE deletes text to the
                       # RIGHT of the cursor and eats the user's work.
KC_V = 9
KC_Z = 6
KC_ESC = 53
KC_OPT_L = 58          # Left Option


class MacHotkeys(Hotkeys):
    """A RAW event tap — keycodes and modifier flags only, never characters.

    Not pynput: pynput translates each keystroke into a character, which calls a
    macOS Text-Input-Source API that ASSERTS it is on the main thread. From a
    listener thread that hard-crashes the process (SIGTRAP) while you're simply
    typing — and a native abort is not catchable from Python. A raw tap never asks
    what a key means, so it never touches that API.
    """

    modifier_name = "Left ⌥"

    def __init__(self):
        self._tap = None
        self._on_key = None
        self._alt_down = False

    def start(self, on_key):
        self._on_key = on_key
        ok = threading.Event()
        threading.Thread(target=self._run, args=(ok,), daemon=True).start()
        ok.wait(timeout=3.0)
        return self._tap is not None

    def _run(self, ok):
        import Quartz
        from CoreFoundation import (CFRunLoopAddSource, CFRunLoopGetCurrent,
                                    CFRunLoopRun, kCFRunLoopCommonModes)

        def _cb(proxy, etype, event, refcon):
            # Runs on the tap thread, inside a C callback — an exception escaping
            # here aborts the whole process, so nothing may leak out.
            try:
                if etype in (Quartz.kCGEventTapDisabledByTimeout,
                             Quartz.kCGEventTapDisabledByUserInput):
                    # macOS disabled us (callback too slow, or certain input).
                    # Without this re-arm the app stays alive and lit while the
                    # hotkey silently never fires again.
                    Quartz.CGEventTapEnable(self._tap, True)
                else:
                    self._dispatch(etype, event)
            except Exception:
                pass
            return event        # listen-only: always pass the event through

        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) |
                Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp) |
                Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly, mask, _cb, None)
        ok.set()
        if not self._tap:
            print("[voicetype] no Accessibility permission — hotkeys are off",
                  file=sys.stderr, flush=True)
            return
        src = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), src, kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        CFRunLoopRun()

    def _dispatch(self, etype, event):
        import Quartz
        kc = int(Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode))
        flags = Quartz.CGEventGetFlags(event)
        cmd = bool(flags & Quartz.kCGEventFlagMaskCommand)
        ctrl = bool(flags & Quartz.kCGEventFlagMaskControl)

        if etype == Quartz.kCGEventFlagsChanged:
            if kc == KC_OPT_L:
                down = bool(flags & Quartz.kCGEventFlagMaskAlternate)
                if down != self._alt_down:
                    self._alt_down = down
                    self._on_key(KeyEvent(MOD, down, cmd, ctrl))
            return
        if etype != Quartz.kCGEventKeyDown:
            return
        if kc == KC_ESC:
            self._on_key(KeyEvent(ESC, True, cmd, ctrl))
        elif kc == KC_V:
            self._on_key(KeyEvent(KEY_V, True, cmd, ctrl))
        elif kc == KC_Z:
            self._on_key(KeyEvent(KEY_Z, True, cmd, ctrl))
        else:
            self._on_key(KeyEvent(OTHER, True, cmd, ctrl))

    def stop(self):
        try:
            import Quartz
            if self._tap:
                Quartz.CGEventTapEnable(self._tap, False)
        except Exception:
            pass


class MacClipboard(Clipboard):
    def get_text(self):
        try:
            return subprocess.run(["pbpaste"], capture_output=True,
                                  text=True, timeout=2).stdout
        except Exception:
            return None

    def set_text(self, text):
        try:
            subprocess.run(["pbcopy"], input=text, text=True, timeout=2)
            return True
        except Exception:
            return False


class MacKeystrokes(Keystrokes):
    """Synthesise keys via Quartz CGEvent — thread-safe, unlike pynput's typing."""

    @staticmethod
    def _post(vk, command=False):
        import Quartz
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        flags = Quartz.kCGEventFlagMaskCommand if command else 0
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(src, vk, down)
            if flags:
                Quartz.CGEventSetFlags(ev, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.002)

    def paste(self):
        self._post(VK_V, command=True)

    def enter(self):
        self._post(VK_RETURN)

    def backspace(self, n=1):
        for _ in range(n):
            self._post(VK_BACKSPACE)


class MacOverlay(Overlay):
    """A borderless NSPanel with CALayer bars, floating above everything."""

    def __init__(self):
        self._panel = None
        self._bars = []
        self._shown = False
        self._phase = 0.0

    def _build(self):
        from AppKit import (NSBackingStoreBuffered, NSColor, NSMakeRect, NSPanel,
                            NSScreen, NSView, NSWindowStyleMaskBorderless)
        from Quartz import CALayer
        scr = NSScreen.mainScreen().frame()
        w, h = 132.0, 26.0
        rect = NSMakeRect((scr.size.width - w) / 2, 26, w, h)
        p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        p.setLevel_(25)
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setIgnoresMouseEvents_(True)
        p.setHasShadow_(False)
        try:
            p.setCollectionBehavior_(1 << 0 | 1 << 8)   # all Spaces + fullscreen
        except Exception:
            pass
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        view.setWantsLayer_(True)
        p.setContentView_(view)
        n, bw, gap = 13, 3.0, 4.0
        x0 = (w - (n * bw + (n - 1) * gap)) / 2
        for i in range(n):
            lay = CALayer.layer()
            lay.setBackgroundColor_(
                NSColor.whiteColor().colorWithAlphaComponent_(0.85).CGColor())
            lay.setCornerRadius_(bw / 2)
            lay.setFrame_(NSMakeRect(x0 + i * (bw + gap), h / 2 - 1, bw, 2))
            view.layer().addSublayer_(lay)
            self._bars.append(lay)
        self._panel = p

    def render(self, mode, level=0.0):
        from AppKit import NSColor, NSMakeRect, NSScreen
        from Quartz import CATransaction
        if self._panel is None:
            self._build()
        if self._panel is None:
            return
        try:
            scr = NSScreen.mainScreen().frame()
            f = self._panel.frame()
            self._panel.setFrameOrigin_(
                (scr.origin.x + (scr.size.width - f.size.width) / 2,
                 scr.origin.y + 26))
        except Exception:
            pass
        if not self._shown:
            self._panel.orderFrontRegardless()
            self._shown = True

        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        self._phase += 0.28
        h = 26.0
        for i, lay in enumerate(self._bars):
            f = lay.frame()
            if mode == "wave":
                # Ride the real mic level, with a floor so it always breathes.
                amp = (0.35 + 0.65 * min(1.0, level * 1.6)) * 7.0
                bh = max(2.0, amp * abs(math.sin(self._phase + i * 0.55)))
                col = NSColor.whiteColor()
            elif mode == "load":
                pos = (self._phase * 1.6) % len(self._bars)
                d = min(abs(i - pos), len(self._bars) - abs(i - pos))
                bh = max(2.0, 7.0 * max(0.0, 1.0 - d / 2.4))
                col = NSColor.whiteColor()
            elif mode == "fail":
                bh, col = 2.0, NSColor.systemRedColor()
            else:
                bh, col = 2.0, NSColor.whiteColor()
            lay.setFrame_(NSMakeRect(f.origin.x, h / 2 - bh / 2, f.size.width, bh))
            lay.setBackgroundColor_(col.colorWithAlphaComponent_(
                0.9 if mode != "idle" else 0.35).CGColor())
        CATransaction.commit()

    def hide(self):
        if self._panel is not None and self._shown:
            self._panel.orderOut_(None)
            self._shown = False


class MacTray(Tray):
    """rumps menu bar. rumps is a base class, not a library, so the app object
    IS the tray — we wrap it here rather than let that inversion spread."""

    def __init__(self):
        self._app = None
        self._items = {}

    def run(self, menu):
        app = rumps.App("VoiceType", title="🎙", quit_button=None)
        built = []
        for item in menu:
            if item is None:
                built.append(None)
                continue
            mi = rumps.MenuItem(item["title"], callback=self._wrap(item.get("action")))
            self._items[item["id"]] = mi
            built.append(mi)
        app.menu = built
        self._app = app
        app.run()          # blocks, owns the main thread

    @staticmethod
    def _wrap(fn):
        if fn is None:
            return None

        def _cb(_sender):
            try:
                fn()
            except Exception:
                pass
        return _cb

    def set_active(self, on):
        if self._app:
            self._app.title = "🎙" if on else "🎙̸"

    def set_checked(self, item_id, on):
        mi = self._items.get(item_id)
        if mi is not None:
            mi.state = 1 if on else 0

    def alert(self, title, message):
        try:
            rumps.alert(title=title, message=message)
        except Exception:
            pass

    def notify(self, title, message):
        try:
            rumps.notification("VoiceType", title, message)
        except Exception:
            pass

    def every(self, seconds, fn):
        t = rumps.Timer(lambda _t: fn(), seconds)
        t.start()
        return t

    def quit(self):
        rumps.quit_application()


class MacPlatform(Platform):
    paste_settle = 0.12     # Cocoa reads the pasteboard lazily on ⌘V; posting
                            # sooner pastes the PREVIOUS clipboard contents.

    def __init__(self):
        self.hotkeys = MacHotkeys()
        self.clipboard = MacClipboard()
        self.keys = MacKeystrokes()
        self.tray = MacTray()
        self.overlay = MacOverlay()
