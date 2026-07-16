#!/usr/bin/env python3
"""Start VoiceType when you log in. Registry Run key on Windows, LaunchAgent on macOS."""

import os
import subprocess
import sys

APP = "VoiceType"


def _cmd():
    """The command that relaunches this install."""
    py = sys.executable
    if sys.platform == "win32":
        # pythonw.exe runs with no console window; the user shouldn't stare at one.
        w = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.exists(w):
            py = w
    return py, "-m", "voicetype"


# ── Windows ─────────────────────────────────────────────────────────────────

def _win_set(on):
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                         winreg.KEY_SET_VALUE)
    try:
        if on:
            py, *args = _cmd()
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ,
                              '"%s" %s' % (py, " ".join(args)))
        else:
            try:
                winreg.DeleteValue(key, APP)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def _win_get():
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run")
        try:
            winreg.QueryValueEx(key, APP)
            return True
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False


# ── macOS ───────────────────────────────────────────────────────────────────

PLIST = os.path.expanduser("~/Library/LaunchAgents/com.voicetype.plist")


def _mac_set(on):
    if on:
        os.makedirs(os.path.dirname(PLIST), exist_ok=True)
        args = "".join("<string>%s</string>" % a for a in _cmd())
        with open(PLIST, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>'
                    '<key>Label</key><string>com.voicetype</string>'
                    '<key>ProgramArguments</key><array>%s</array>'
                    '<key>RunAtLoad</key><true/>'
                    '</dict></plist>\n' % args)
        subprocess.run(["launchctl", "load", "-w", PLIST],
                       capture_output=True, timeout=5)
    else:
        subprocess.run(["launchctl", "unload", "-w", PLIST],
                       capture_output=True, timeout=5)
        try:
            os.remove(PLIST)
        except OSError:
            pass


def _mac_get():
    return os.path.exists(PLIST)


# ── api ─────────────────────────────────────────────────────────────────────

def set(on):
    try:
        if sys.platform == "win32":
            _win_set(on)
        elif sys.platform == "darwin":
            _mac_set(on)
        return True
    except Exception:
        return False


def get():
    try:
        if sys.platform == "win32":
            return _win_get()
        if sys.platform == "darwin":
            return _mac_get()
    except Exception:
        pass
    return False
