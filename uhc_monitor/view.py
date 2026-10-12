"""Helpers that turn a before/after pair into excerpts with the changed words marked (HTML-escaped)."""
import html
from difflib import SequenceMatcher

CONTEXT_WORDS = 25   # words kept on each side of a change in an excerpt


def _mark(words, flags, css):
    return [f'<span style="{css}">{html.escape(w)}</span>' if f else html.escape(w) for w, f in zip(words, flags)]


def _window(flags, n):
    hit = [i for i, f in enumerate(flags) if f]
    if not hit:
        return 0, n
    return max(0, hit[0] - CONTEXT_WORDS), min(n, hit[-1] + 1 + CONTEXT_WORDS)


def excerpt_pair(before, after):
    """(before_html, after_html, full_before_html, full_after_html): excerpts around the change, words marked.
    Removed words are struck through in red in the before text, added words are bold green in the after text."""
    b, a = (before or "").split(), (after or "").split()
    bf, af = [False] * len(b), [False] * len(a)
    for op, i1, i2, j1, j2 in SequenceMatcher(None, b, a, autojunk=False).get_opcodes():
        if op != "equal":
            bf[i1:i2] = [True] * (i2 - i1)
            af[j1:j2] = [True] * (j2 - j1)
    bm = _mark(b, bf, "background:#fde2e2;text-decoration:line-through")
    am = _mark(a, af, "background:#d9f2d9;font-weight:600")

    def cut(words, flags):
        lo, hi = _window(flags, len(words))
        return ("… " if lo else "") + " ".join(words[lo:hi]) + (" …" if hi < len(words) else "")

    return cut(bm, bf), cut(am, af), " ".join(bm), " ".join(am)
