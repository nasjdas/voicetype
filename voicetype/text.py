#!/usr/bin/env python3
"""
Text cleanup — turning what you said into what you meant to write.

Pure functions, no OS calls, no model. Both platforms import this, so a fix here
fixes Windows and macOS at once.

Order matters and is not arbitrary:
  vocabulary  → fix misheard names BEFORE anything else reads the words
  snippets    → expand triggers into their text
  commands    → "new paragraph", "press enter"
  fillers     → drop the ums
  punctuation → spacing and capitals last, over the final wording
"""


import re
import unicodedata

_FILLERS = re.compile(r"\b(?:um+|uh+|erm+|ah+|hmm+|uh[\s-]?huh|mm+hmm)\b[,]?", re.I)
_ENTER_TAIL = re.compile(
    r"[\s,\.]*\b(press enter|send it|send message|hit enter)\b[\s\.\!]*$", re.I)

# Languages where a lone "i" is a real word and must never be capitalised.
# Swedish "jag är i skolan" must not become "jag är I skolan".
_I_IS_A_WORD = {"sv", "da", "no", "nb", "nn"}


def _norm(s):
    """Fold the curly quotes a speech model emits, so later regexes match."""
    s = unicodedata.normalize("NFKC", s or "")
    return (s.replace("’", "'").replace("‘", "'")
             .replace("“", '"').replace("”", '"'))


def apply_vocab(t, terms, corrections=None):
    """Fix names and jargon in a transcript.

    Two deliberately separate mechanisms:

    `terms`       — your words, spelled the way you want them. Matched
                    case-insensitively as whole words, so "openai" → "OpenAI".
    `corrections` — explicit wrong→right pairs: {"clawd": "Claude"}. For when
                    the model reliably mishears something.

    There is deliberately NO fuzzy guessing. It cannot be made safe: "clawd" and
    "cloud" are the *same* edit distance from "Claude" (0.727 by difflib), so any
    cutoff that catches the typo also rewrites the real word. Guessing wrong here
    silently corrupts what you said, which is worse than not helping. Ask the user
    for the pair instead — that's what Wispr's dictionary does, and it's honest.

    This is post-hoc correction, not model biasing. Parakeet exposes no prompt or
    hotword hook, so there is nothing to bias; we fix the text after the fact. Say
    so in the UI rather than implying the model learns.
    """
    if not t:
        return t
    table = {}
    for term in (terms or []):
        term = term.strip()
        if term:
            table[term.lower()] = term
    for wrong, right in (corrections or {}).items():
        wrong = (wrong or "").strip()
        if wrong and right:
            table[wrong.lower()] = right
    if not table:
        return t

    # Longest first, so "New York City" wins over "New York".
    pattern = "|".join(_bounded(k) for k in sorted(table, key=len, reverse=True))
    return re.sub(pattern,
                  lambda m: table.get(m.group(0).lower(), m.group(0)),
                  t, flags=re.I)


def _bounded(term):
    """Wrap a term in the right word boundaries for its own edges.

    A plain \\b won't do. "C++" ends in a non-word character, so a trailing \\b
    demands a word character right after the plus signs — which never happens, and
    the term silently never matches. Same for C#, .NET, and any term someone in
    tech is actually likely to add.
    """
    esc = re.escape(term)
    left = r"\b" if term[:1].isalnum() or term[:1] == "_" else r"(?<!\S)"
    right = r"\b" if term[-1:].isalnum() or term[-1:] == "_" else r"(?!\S)"
    return "(?:%s%s%s)" % (left, esc, right)


def apply_snippets(t, snippets):
    """Expand voice triggers into saved blocks of text.

    snippets: [{"trigger": "my calendar", "text": "https://cal.com/..."}]
    Longest trigger first, so "my work email" beats "my email".
    """
    if not snippets or not t:
        return t
    items = [(s.get("trigger", "").strip(), s.get("text", ""))
             for s in snippets if s.get("trigger", "").strip()]
    for trig, body in sorted(items, key=lambda x: len(x[0]), reverse=True):
        t = re.sub(r"\b%s\b" % re.escape(trig), lambda _m, b=body: b, t, flags=re.I)
    return t


def clean_text(t, lang="en", vocab=None, snippets=None, corrections=None):
    """Clean a raw transcript. Returns (text, press_enter).

    lang gates the English-only rules — see _I_IS_A_WORD.
    """
    t = _norm(t).strip()
    if not t:
        return "", False

    t = apply_vocab(t, vocab, corrections)
    t = apply_snippets(t, snippets)

    press_enter = bool(_ENTER_TAIL.search(t))
    t = _ENTER_TAIL.sub("", t).strip()

    t = re.sub(r"\bnew paragraph\b", "\n\n", t, flags=re.I)
    t = re.sub(r"\bnew line\b", "\n", t, flags=re.I)
    t = _FILLERS.sub("", t)
    # [ \t] not \s — \s would eat the paragraph breaks we just inserted.
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t).strip()

    if lang not in _I_IS_A_WORD:
        t = re.sub(r"\bi\b", "I", t)

    t = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    if t:
        t = t[0].upper() + t[1:]
    return t, press_enter
