#!/usr/bin/env python3
"""Pick the backend for this OS."""

import sys

from .base import (ESC, KEY_V, KEY_Z, MOD, OTHER, KeyEvent, Platform)

__all__ = ["Platform", "KeyEvent", "MOD", "ESC", "KEY_V", "KEY_Z", "OTHER", "get"]


def get():
    if sys.platform == "darwin":
        from .macos import MacPlatform
        return MacPlatform()
    if sys.platform == "win32":
        from .windows import WinPlatform
        return WinPlatform()
    raise RuntimeError(
        "VoiceType supports macOS and Windows. Linux would be very welcome — "
        "the platform interface is in voicetype/platform/base.py.")
