#!/usr/bin/env python3
"""
Storage for voice-typing (dictation) history — kept completely separate from
call recordings, transcripts and the Pronounce vocabulary.

One small JSON file, newest first. Each entry: {id, text, ts (epoch seconds)}.
"""

import json
import os
import threading
import time

HOME = os.path.expanduser("~/.voicetype")
os.makedirs(HOME, exist_ok=True)
FILE = os.path.join(HOME, "dictations.json")
LANG_FILE = os.path.join(HOME, ".dictlang")
_lock = threading.Lock()
MAX_ITEMS = 500


def get_lang():
    """Dictation language: 'en' (default), 'sv', or 'auto'. Voice typing only."""
    try:
        v = open(LANG_FILE).read().strip()
        return v or "en"
    except Exception:
        return "en"


def set_lang(v):
    v = (v or "en").strip()
    try:
        open(LANG_FILE, "w").write(v)
    except Exception:
        pass


def load():
    try:
        with open(FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save(items):
    tmp = FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(items, f)
    os.replace(tmp, FILE)


def add(text, ts=None):
    text = (text or "").strip()
    if not text:
        return None
    ts = ts or time.time()
    item = {"id": int(ts * 1000), "text": text, "ts": ts}
    with _lock:
        items = load()
        items.insert(0, item)
        _save(items[:MAX_ITEMS])
    return item


def delete(item_id):
    with _lock:
        items = [x for x in load() if x.get("id") != item_id]
        _save(items)


def clear():
    with _lock:
        _save([])


def latest():
    items = load()
    return items[0]["text"] if items else ""
