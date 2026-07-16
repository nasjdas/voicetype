#!/usr/bin/env python3
"""
Local storage — your dictation history, vocabulary, snippets and settings.

SQLite, from the standard library. No dependency, and it fixes three things the
old JSON file got wrong: history was capped at 500 entries (so stats and streaks
silently went wrong for anyone real), every dictation rewrote the whole file on the
paste path, and search meant loading everything into memory.

Everything lives in ~/.voicetype, locked to your user account (0700). Nothing here
ever touches the network.
"""

import json
import os
import sqlite3
import threading
import time

HOME = os.path.expanduser("~/.voicetype")
DB = os.path.join(HOME, "history.db")
LEGACY_JSON = os.path.join(HOME, "dictations.json")
LEGACY_HTML = os.path.join(HOME, "history.html")
LEGACY_LANG = os.path.join(HOME, ".dictlang")

_lock = threading.RLock()
_local = threading.local()

DEFAULTS = {"lang": "en", "autostart": False, "engine": "auto"}


def _ensure_home():
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    try:
        # makedirs' mode is masked by umask and ignored if the dir already exists,
        # so set it explicitly. Your history is as personal as a diary; it has no
        # business being world-readable on a shared machine.
        os.chmod(HOME, 0o700)
    except OSError:
        pass


def _conn():
    c = getattr(_local, "c", None)
    if c is not None:
        return c
    _ensure_home()
    fresh = not os.path.exists(DB)
    c = sqlite3.connect(DB, timeout=5.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    _init(c)
    if fresh:
        try:
            os.chmod(DB, 0o600)
        except OSError:
            pass
    _local.c = c
    return c


def _init(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS entries (
            id      INTEGER PRIMARY KEY,
            ts      REAL    NOT NULL,
            text    TEXT    NOT NULL,
            dur     REAL,
            words   INTEGER,
            lang    TEXT,
            engine  TEXT
        );
        CREATE INDEX IF NOT EXISTS entries_ts ON entries(ts DESC);
        CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
    """)
    c.commit()
    _migrate(c)


def _migrate(c):
    """One-time move off the old JSON file."""
    if not os.path.exists(LEGACY_JSON):
        return
    try:
        with open(LEGACY_JSON, encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        items = []
    for it in items or []:
        txt = (it.get("text") or "").strip()
        if not txt:
            continue
        c.execute("INSERT OR IGNORE INTO entries(id, ts, text, words) VALUES(?,?,?,?)",
                  (it.get("id") or int(it.get("ts", 0) * 1000), it.get("ts", 0),
                   txt, len(txt.split())))
    if os.path.exists(LEGACY_LANG):
        try:
            with open(LEGACY_LANG, encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                c.execute("INSERT OR REPLACE INTO kv VALUES('lang', ?)", (json.dumps(v),))
        except Exception:
            pass
    c.commit()
    for p in (LEGACY_JSON, LEGACY_LANG):
        try:
            os.replace(p, p + ".migrated")
        except OSError:
            pass
    # The old history viewer wrote a full plaintext copy of everything here, and
    # "clear history" never knew about it. Delete it.
    try:
        os.remove(LEGACY_HTML)
    except OSError:
        pass


# ── settings ────────────────────────────────────────────────────────────────

def get_setting(k, default=None):
    with _lock:
        r = _conn().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    if r is None:
        return DEFAULTS.get(k, default)
    try:
        return json.loads(r["v"])
    except Exception:
        return DEFAULTS.get(k, default)


def set_setting(k, v):
    with _lock:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))
        c.commit()


def get_lang():
    v = get_setting("lang", "en")
    return v if v in ("en", "sv", "auto") else "en"


def set_lang(v):
    set_setting("lang", (v or "en").strip())


def get_vocab():
    return get_setting("vocab", []) or []


def set_vocab(terms):
    set_setting("vocab", [t.strip() for t in terms if t and t.strip()])


def get_corrections():
    return get_setting("corrections", {}) or {}


def set_corrections(pairs):
    set_setting("corrections", {k.strip(): v.strip() for k, v in (pairs or {}).items()
                                if k and k.strip() and v and v.strip()})


def get_snippets():
    return get_setting("snippets", []) or []


def set_snippets(items):
    set_setting("snippets", [
        {"trigger": s.get("trigger", "").strip(), "text": s.get("text", "")}
        for s in (items or []) if s.get("trigger", "").strip()])


# ── history ─────────────────────────────────────────────────────────────────

def add(text, dur=None, lang=None, engine=None, ts=None):
    text = (text or "").strip()
    if not text:
        return None
    ts = ts or time.time()
    item = {"id": int(ts * 1000), "ts": ts, "text": text, "dur": dur,
            "words": len(text.split()), "lang": lang, "engine": engine}
    with _lock:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO entries(id,ts,text,dur,words,lang,engine)"
                  " VALUES(:id,:ts,:text,:dur,:words,:lang,:engine)", item)
        c.commit()
    return item


def load(limit=200, offset=0, q=None):
    with _lock:
        c = _conn()
        if q:
            like = "%" + q.strip() + "%"
            rows = c.execute(
                "SELECT * FROM entries WHERE text LIKE ? ORDER BY ts DESC"
                " LIMIT ? OFFSET ?", (like, limit, offset)).fetchall()
        else:
            rows = c.execute("SELECT * FROM entries ORDER BY ts DESC"
                             " LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def count(q=None):
    with _lock:
        c = _conn()
        if q:
            r = c.execute("SELECT COUNT(*) n FROM entries WHERE text LIKE ?",
                          ("%" + q.strip() + "%",)).fetchone()
        else:
            r = c.execute("SELECT COUNT(*) n FROM entries").fetchone()
    return r["n"]


def delete(item_id):
    with _lock:
        c = _conn()
        c.execute("DELETE FROM entries WHERE id=?", (item_id,))
        c.commit()


def clear():
    with _lock:
        c = _conn()
        c.execute("DELETE FROM entries")
        c.commit()


def latest():
    with _lock:
        r = _conn().execute("SELECT text FROM entries ORDER BY ts DESC LIMIT 1").fetchone()
    return r["text"] if r else ""


# Typing speed we compare against, for "time saved". 40 wpm is a realistic average
# for prose on a keyboard; pros hit 70+, so this is an estimate, not a measurement —
# the dashboard says so rather than dressing it up as fact.
TYPING_WPM = 40


def stats():
    with _lock:
        c = _conn()
        tot = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(words),0) w, COALESCE(SUM(dur),0) d,"
            "       COUNT(dur) nd FROM entries").fetchone()
        per_day = c.execute(
            "SELECT date(ts,'unixepoch','localtime') d, SUM(words) w, COUNT(*) n"
            " FROM entries GROUP BY d ORDER BY d DESC LIMIT 365").fetchall()
    words = tot["w"] or 0
    spoken = tot["d"] or 0.0

    # Only count time actually measured. Rows from before we recorded duration
    # would otherwise make this a guess stacked on a guess.
    typed = words / TYPING_WPM * 60.0
    saved = max(0.0, typed - spoken) if tot["nd"] else 0.0

    days = [dict(r) for r in per_day]
    streak = 0
    if days:
        import datetime
        today = datetime.date.today()
        have = {d["d"] for d in days}
        cur = today
        if str(today) not in have:
            cur = today - datetime.timedelta(days=1)   # today isn't over yet
        while str(cur) in have:
            streak += 1
            cur -= datetime.timedelta(days=1)
    return {
        "entries": tot["n"] or 0,
        "words": words,
        "spoken_sec": spoken,
        "saved_sec": saved,
        "saved_measured": bool(tot["nd"]),
        "avg_words": round(words / tot["n"], 1) if tot["n"] else 0,
        "streak": streak,
        "per_day": list(reversed(days)),
        "typing_wpm": TYPING_WPM,
    }
