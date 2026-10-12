"""Helpers that turn a before/after pair into excerpts with the changed words marked (HTML-escaped)."""
import html
from difflib import SequenceMatcher

# Theme-neutral: text keeps the theme's own colour (color:inherit); the tint is semi-transparent so it works on
# light and dark backgrounds. Meaning does not rest on colour: removed = strikethrough, added = bold + underline.
REMOVED_STYLE = "background:rgba(248,81,73,0.25);color:inherit;text-decoration:line-through;border-radius:3px"
ADDED_STYLE = ("background:rgba(46,160,67,0.25);color:inherit;font-weight:700;text-decoration:underline;"
               "text-underline-offset:2px;border-radius:3px")

CONTEXT_WORDS = 25   # words kept on each side of a change in an excerpt


def _mark(words, flags, css):
    """HTML-escape the words; each run of consecutive changed words becomes one styled span."""
    out, run = [], []
    for w, f in zip(words + [""], flags + [False]):
        if f:
            run.append(html.escape(w))
            continue
        if run:
            out.append(f'<span style="{css}">{" ".join(run)}</span>')
            run = []
        out.append(html.escape(w))
    return out[:-1]


def _window(flags, n):  # (first, last+1) word index to show, with context
    hit = [i for i, f in enumerate(flags) if f]
    if not hit:
        return 0, n
    return max(0, hit[0] - CONTEXT_WORDS), min(n, hit[-1] + 1 + CONTEXT_WORDS)


def excerpt_pair(before, after):
    """(before_html, after_html, full_before_html, full_after_html): excerpts around the change, words marked.
    Removed words are struck through (red tint) in the before text; added words are bold and underlined
    (green tint) in the after. Every word is HTML-escaped."""
    b, a = (before or "").split(), (after or "").split()
    bf, af = [False] * len(b), [False] * len(a)
    for op, i1, i2, j1, j2 in SequenceMatcher(None, b, a, autojunk=False).get_opcodes():
        if op != "equal":
            bf[i1:i2] = [True] * (i2 - i1)
            af[j1:j2] = [True] * (j2 - j1)

    def render(words, flags, css, windowed):
        lo, hi = _window(flags, len(words)) if windowed else (0, len(words))
        text = " ".join(_mark(words[lo:hi], flags[lo:hi], css))
        return ("… " if lo else "") + text + (" …" if hi < len(words) else "")

    return (render(b, bf, REMOVED_STYLE, True), render(a, af, ADDED_STYLE, True),
            render(b, bf, REMOVED_STYLE, False), render(a, af, ADDED_STYLE, False))
