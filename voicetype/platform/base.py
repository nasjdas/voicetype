#!/usr/bin/env python3
"""
The platform contract — the only place either OS is allowed to leak in.

Five small pieces. Everything else (the gesture timing, the cleanup, the paste
algorithm, the undo guard, the history) lives above this line and is shared, because
duplicating that logic per platform is how the two builds silently drift apart.

The important rule: **this layer reports key events, it never decides what they
mean.** Hold-vs-tap-vs-double-tap timing stays in the shared state machine. Give a
platform its own timing and you've forked the product's feel.

Two contracts that hold on both, and are what make this work at all:
  • Tray.run() blocks and owns the main thread. AppKit demands it; Tcl/tkinter is
    not thread-safe. So it's an honest shared rule, not a Mac accommodation.
  • Hotkeys must drop OS-injected events. We type by synthesising keystrokes, and
    those come straight back through our own listener. On macOS that's harmless by
    luck; on Windows it is a guaranteed feedback loop.
"""

from abc import ABC, abstractmethod

# Normalised keys the shared state machine understands.
MOD = "mod"        # the dictation modifier (Left ⌥ on Mac, Right Ctrl on Windows)
ESC = "esc"
KEY_V = "v"
KEY_Z = "z"
OTHER = "other"    # any other key — means "this is a shortcut, not dictation"


class KeyEvent:
    __slots__ = ("key", "down", "cmd", "ctrl")

    def __init__(self, key, down, cmd=False, ctrl=False):
        self.key = key
        self.down = down
        self.cmd = cmd      # ⌘ on Mac; on Windows this mirrors ctrl
        self.ctrl = ctrl


class Hotkeys(ABC):
    @abstractmethod
    def start(self, on_key):
        """Install a global, listen-only hook. Returns False if the OS refused
        (macOS: no Accessibility permission). Non-blocking.

        Must re-arm itself if the OS disables it, and must drop injected events.
        """

    @abstractmethod
    def stop(self):
        ...

    @property
    @abstractmethod
    def modifier_name(self):
        """Human name of the dictation modifier, for the UI."""


class Clipboard(ABC):
    @abstractmethod
    def get_text(self):
        """Returns str, or None on failure. Never raises."""

    @abstractmethod
    def set_text(self, text):
        """Returns True on success. Never raises."""


class Keystrokes(ABC):
    @abstractmethod
    def paste(self):
        """⌘V / Ctrl+V."""

    @abstractmethod
    def enter(self):
        ...

    @abstractmethod
    def backspace(self, n=1):
        ...


class Tray(ABC):
    @abstractmethod
    def run(self, menu):
        """BLOCKS — owns the main thread until quit."""

    @abstractmethod
    def set_active(self, on):
        ...

    @abstractmethod
    def set_checked(self, item_id, on):
        ...

    @abstractmethod
    def alert(self, title, message):
        ...

    @abstractmethod
    def every(self, seconds, fn):
        """Call fn on the main thread every `seconds`. Drives the overlay."""

    @abstractmethod
    def quit(self):
        ...


class Overlay(ABC):
    """The listening indicator. Decorative — each platform draws it natively."""

    @abstractmethod
    def render(self, mode, level):
        """mode: idle|wave|load|fail.  level: live mic level 0..1."""

    @abstractmethod
    def hide(self):
        ...


class Platform(ABC):
    hotkeys = None
    clipboard = None
    keys = None
    tray = None
    overlay = None

    @property
    @abstractmethod
    def paste_settle(self):
        """Seconds to wait after putting text on the clipboard before pasting.

        Empirically tuned PER PLATFORM — do not copy the Mac number to Windows.
        Cocoa apps read the pasteboard lazily when ⌘V arrives, so posting too
        early pastes the PREVIOUS clipboard contents.
        """
