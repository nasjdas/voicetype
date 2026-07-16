#!/usr/bin/env python3
"""
Windows backend — a low-level keyboard hook, the Win32 clipboard, SendInput,
a tray icon and a click-through overlay.

Honest note: this was written on a Mac and has never run on Windows. It is
deliberately defensive — every OS call is wrapped, nothing raises into a callback,
and the whole thing degrades to "hotkeys off" rather than crashing. If you're on
Windows and something here is wrong, the issue tracker is the right place.

Three Windows-specific traps this code is built around:

1. THE HOOK MUST NEVER BLOCK. If the callback takes longer than
   LowLevelHooksTimeout (300ms by default), Windows silently stops sending it
   events. Unlike macOS there is nothing to re-arm — the hook stays "installed"
   and just never fires. So the callback only ever enqueues and returns.

2. OUR OWN KEYSTROKES COME BACK TO US. SendInput events arrive at the hook with
   LLKHF_INJECTED set. Without filtering that, an undo of 200 characters re-enters
   the handler 200 times.

3. THE CLIPBOARD IS A GLOBAL LOCK. OpenClipboard routinely fails with
   ERROR_ACCESS_DENIED because Office, a browser, or a clipboard manager holds it.
   Retry with backoff, and ALWAYS close it — a leaked open clipboard wedges the
   whole desktop for every app.
"""

import ctypes
import ctypes.wintypes as wt
import threading
import time

from .base import (ESC, KEY_V, KEY_Z, MOD, OTHER, Clipboard, Hotkeys, KeyEvent,
                   Keystrokes, Overlay, Platform, Tray)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ctypes assumes every function returns a 32-bit int unless told otherwise. On
# 64-bit Windows that silently truncates every HANDLE, HGLOBAL and pointer to its
# low half — the call "succeeds" and hands back a corrupt handle, and the next
# call on it fails for no visible reason. Declaring restype/argtypes is not
# tidiness here; without it the clipboard simply does not work.
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10

VK_BACK = 0x08
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_CONTROL = 0x11
VK_V = 0x56
VK_Z = 0x5A
VK_RCONTROL = 0xA3

# Right Ctrl is the dictation modifier on Windows.
#   Left Alt  → tapping it alone opens the menu bar / ribbon key tips everywhere.
#   Right Alt → this is AltGr on Swedish and other European layouts; stealing it
#               would break @ \ $ € for exactly the people who need them.
#   Left Win  → the shell eats it.
# Right Ctrl has no default single-tap behaviour, which is what we need.
MOD_VK = VK_RCONTROL


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    wt.LPARAM, ctypes.c_int, wt.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT))

# ── the signatures (see the ULONG_PTR note above — these are load-bearing) ────
user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wt.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wt.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
user32.IsClipboardFormatAvailable.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE

kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = wt.LPVOID
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.restype = wt.BOOL
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wt.DWORD

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelKeyboardProc,
                                     wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM,
                                  ctypes.POINTER(KBDLLHOOKSTRUCT)]
user32.CallNextHookEx.restype = wt.LPARAM
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.restype = wt.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


class WinHotkeys(Hotkeys):
    modifier_name = "Right Ctrl"

    def __init__(self):
        self._on_key = None
        self._hook = None
        self._thread_id = None
        self._proc = None       # must outlive the hook or Windows calls freed memory
        self._mod_down = False

    def start(self, on_key):
        self._on_key = on_key
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True).start()
        ready.wait(timeout=3.0)
        return self._hook is not None

    def _run(self, ready):
        # A WH_KEYBOARD_LL hook only fires if the installing thread pumps messages.
        # This GetMessage loop is the structural twin of the Mac's CFRunLoopRun().
        self._proc = LowLevelKeyboardProc(self._callback)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        self._thread_id = kernel32.GetCurrentThreadId()
        ready.set()
        if not self._hook:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _callback(self, nCode, wParam, lParam):
        # Never block, never raise. Exceeding LowLevelHooksTimeout gets us silently
        # skipped, and there is no way to detect or recover from that.
        try:
            if nCode == 0:
                kb = lParam.contents
                if not (kb.flags & LLKHF_INJECTED):     # ignore our own SendInput
                    self._dispatch(kb.vkCode, wParam)
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _dispatch(self, vk, wParam):
        down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        up = wParam in (WM_KEYUP, WM_SYSKEYUP)
        if not (down or up):
            return
        ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)

        if vk == MOD_VK:
            if down != self._mod_down:
                self._mod_down = down
                self._on_key(KeyEvent(MOD, down, ctrl, ctrl))
            return
        if not down:
            return
        if vk == VK_ESCAPE:
            self._on_key(KeyEvent(ESC, True, ctrl, ctrl))
        elif vk == VK_V:
            self._on_key(KeyEvent(KEY_V, True, ctrl, ctrl))
        elif vk == VK_Z:
            self._on_key(KeyEvent(KEY_Z, True, ctrl, ctrl))
        else:
            self._on_key(KeyEvent(OTHER, True, ctrl, ctrl))

    def stop(self):
        try:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
            if self._thread_id:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class WinClipboard(Clipboard):
    """Win32 clipboard with the retry that pbcopy never needs."""

    @staticmethod
    def _open(tries=10):
        for i in range(tries):
            if user32.OpenClipboard(None):
                return True
            time.sleep(0.015 * (i + 1))     # another app holds the global lock
        return False

    def get_text(self):
        if not self._open():
            return None
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return None
            p = kernel32.GlobalLock(h)
            if not p:
                return None
            try:
                return ctypes.c_wchar_p(p).value or ""
            finally:
                kernel32.GlobalUnlock(h)
        except Exception:
            return None
        finally:
            user32.CloseClipboard()     # a leaked open clipboard wedges the desktop

    def set_text(self, text):
        if not self._open():
            return False
        try:
            user32.EmptyClipboard()
            buf = ctypes.create_unicode_buffer(text)
            size = ctypes.sizeof(buf)
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not h:
                return False
            p = kernel32.GlobalLock(h)
            if not p:
                kernel32.GlobalFree(h)
                return False
            ctypes.memmove(p, buf, size)
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                kernel32.GlobalFree(h)     # ownership only transfers on success
                return False
            return True
        except Exception:
            return False
        finally:
            user32.CloseClipboard()


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTunion(ctypes.Union):
    # MOUSEINPUT is the largest member of the real union. Without padding to it,
    # sizeof(INPUT) is too small and SendInput rejects every call.
    _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT


class WinKeystrokes(Keystrokes):
    """SendInput — the modern replacement for the deprecated keybd_event."""

    @staticmethod
    def _send(vk, up=False):
        ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP if up else 0,
                        time=0, dwExtraInfo=0)
        inp = INPUT(type=INPUT_KEYBOARD, u=_INPUTunion(ki=ki))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def _tap(self, vk):
        self._send(vk, False)
        time.sleep(0.002)
        self._send(vk, True)

    def paste(self):
        self._send(VK_CONTROL, False)
        self._tap(VK_V)
        self._send(VK_CONTROL, True)

    def enter(self):
        self._tap(VK_RETURN)

    def backspace(self, n=1):
        for _ in range(n):
            self._tap(VK_BACK)


class WinOverlay(Overlay):
    """A frameless, always-on-top, click-through tkinter window.

    tkinter is stdlib, so this costs no dependency. -transparentcolor alone does
    NOT give click-through — that needs WS_EX_TRANSPARENT | WS_EX_LAYERED.
    """

    KEY = "#010203"      # a colour nothing else uses, punched out to transparent

    def __init__(self):
        self._root = None
        self._canvas = None
        self._bars = []
        self._phase = 0.0
        self._shown = False

    def _build(self):
        import tkinter as tk
        r = tk.Tk()
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        try:
            r.attributes("-transparentcolor", self.KEY)
        except Exception:
            pass
        r.configure(bg=self.KEY)
        w, h = 132, 26
        sw = r.winfo_screenwidth()
        sh = r.winfo_screenheight()
        r.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, sh - h - 60))
        c = tk.Canvas(r, width=w, height=h, bg=self.KEY, highlightthickness=0)
        c.pack()
        n, bw, gap = 13, 3, 4
        x0 = (w - (n * bw + (n - 1) * gap)) / 2
        for i in range(n):
            x = x0 + i * (bw + gap)
            self._bars.append(c.create_rectangle(x, h / 2 - 1, x + bw, h / 2 + 1,
                                                 fill="white", outline=""))
        self._root, self._canvas = r, c
        r.update_idletasks()
        self._click_through()

    def _click_through(self):
        try:
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080     # keep it out of the taskbar/alt-tab
            hwnd = int(self._root.frame(), 16)
            # GetWindowLongW truncates the style to 32 bits on 64-bit Windows;
            # the Ptr variants are the correct ones there.
            get, set_ = ((user32.GetWindowLongPtrW, user32.SetWindowLongPtrW)
                         if ctypes.sizeof(ctypes.c_void_p) == 8 else
                         (user32.GetWindowLongW, user32.SetWindowLongW))
            get.argtypes = [wt.HWND, ctypes.c_int]
            get.restype = ULONG_PTR
            set_.argtypes = [wt.HWND, ctypes.c_int, ULONG_PTR]
            set_.restype = ULONG_PTR
            cur = get(hwnd, GWL_EXSTYLE)
            set_(hwnd, GWL_EXSTYLE,
                 cur | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def render(self, mode, level=0.0):
        import math
        try:
            if self._root is None:
                self._build()
            if not self._shown:
                self._root.deiconify()
                self._shown = True
            self._phase += 0.28
            h = 26
            for i, bar in enumerate(self._bars):
                if mode == "wave":
                    amp = (0.35 + 0.65 * min(1.0, level * 1.6)) * 7.0
                    bh = max(2.0, amp * abs(math.sin(self._phase + i * 0.55)))
                    col = "white"
                elif mode == "load":
                    pos = (self._phase * 1.6) % len(self._bars)
                    d = min(abs(i - pos), len(self._bars) - abs(i - pos))
                    bh = max(2.0, 7.0 * max(0.0, 1.0 - d / 2.4))
                    col = "white"
                elif mode == "fail":
                    bh, col = 2.0, "#E5484D"
                else:
                    bh, col = 2.0, "#666666"
                x0, _, x1, _ = self._canvas.coords(bar)
                self._canvas.coords(bar, x0, h / 2 - bh / 2, x1, h / 2 + bh / 2)
                self._canvas.itemconfig(bar, fill=col)
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            pass

    def hide(self):
        try:
            if self._root is not None and self._shown:
                self._root.withdraw()
                self._shown = False
        except Exception:
            pass


class WinTray(Tray):
    """pystray tray icon. Unlike rumps' push model, pystray pulls checked state
    through a callback, so we keep the truth here and let it ask."""

    def __init__(self):
        self._icon = None
        self._checked = {}
        self._active = True
        self._timers = []

    @staticmethod
    def _image(active):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        col = (255, 255, 255, 255) if active else (255, 255, 255, 90)
        d.rounded_rectangle((26, 12, 38, 38), radius=6, fill=col)
        d.arc((18, 26, 46, 48), start=0, end=180, fill=col, width=4)
        d.line((32, 46, 32, 54), fill=col, width=4)
        return img

    def run(self, menu):
        import pystray
        items = []
        for it in menu:
            if it is None:
                items.append(pystray.Menu.SEPARATOR)
                continue
            iid = it["id"]
            action = it.get("action")
            if it.get("checkable"):
                items.append(pystray.MenuItem(
                    it["title"], self._wrap(action),
                    checked=lambda _i, k=iid: self._checked.get(k, False)))
            else:
                items.append(pystray.MenuItem(it["title"], self._wrap(action)))
        self._icon = pystray.Icon("VoiceType", self._image(True), "VoiceType",
                                  pystray.Menu(*items))
        self._icon.run()        # blocks, owns the main thread

    @staticmethod
    def _wrap(fn):
        if fn is None:
            return None

        def _cb(_icon=None, _item=None):
            try:
                fn()
            except Exception:
                pass
        return _cb

    def set_active(self, on):
        self._active = on
        try:
            if self._icon:
                self._icon.icon = self._image(on)
        except Exception:
            pass

    def set_checked(self, item_id, on):
        self._checked[item_id] = on
        try:
            if self._icon:
                self._icon.update_menu()
        except Exception:
            pass

    def alert(self, title, message):
        self.notify(title, message)

    def notify(self, title, message):
        try:
            if self._icon:
                self._icon.notify(message, title)
        except Exception:
            pass

    def every(self, seconds, fn):
        # pystray has no timer. A plain thread is fine here: unlike AppKit, the
        # tkinter overlay is only ever touched from this one thread.
        stop = threading.Event()

        def _loop():
            while not stop.wait(seconds):
                try:
                    fn()
                except Exception:
                    pass
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        self._timers.append(stop)
        return stop

    def quit(self):
        for s in self._timers:
            s.set()
        try:
            if self._icon:
                self._icon.stop()
        except Exception:
            pass


class WinPlatform(Platform):
    paste_settle = 0.06     # Win32 SetClipboardData is synchronous, so apps see the
                            # text immediately — no lazy pasteboard read to race.

    def __init__(self):
        self.hotkeys = WinHotkeys()
        self.clipboard = WinClipboard()
        self.keys = WinKeystrokes()
        self.tray = WinTray()
        self.overlay = WinOverlay()
