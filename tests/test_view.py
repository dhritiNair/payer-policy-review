"""Excerpt and changed-word helpers used by the page."""
import re

from uhc_monitor.view import ADDED_STYLE, REMOVED_STYLE, changed_summary, excerpt_pair

strip = lambda html: re.sub(r"<[^>]+>", "", html)


def test_changed_words_are_marked_with_theme_neutral_styles():
    b, a, _, _ = excerpt_pair("covered when any of the following", "covered when all of the following")
    assert REMOVED_STYLE in b and ADDED_STYLE in a and "line-through" in REMOVED_STYLE and "underline" in ADDED_STYLE
    assert "color:inherit" in REMOVED_STYLE and "rgba(" in ADDED_STYLE and "#" not in ADDED_STYLE + REMOVED_STYLE
    assert strip(b) == "covered when any of the following" and strip(a) == "covered when all of the following"


def test_adjacent_changed_words_share_one_span_and_html_is_escaped():
    _, a, _, _ = excerpt_pair(None, "• Medications to <b>address</b> seizures")
    assert a.count("<span") == 1 and "&lt;b&gt;address&lt;/b&gt;" in a and "<b>" not in a


def test_long_text_is_windowed_around_the_change_but_full_text_is_kept():
    words = [f"w{i}" for i in range(200)]
    b, a, fb, fa = excerpt_pair(" ".join(words), " ".join(words[:100] + ["NEW"] + words[101:]))
    assert b.startswith("… ") and b.endswith(" …") and "w100" in b and "w0 " not in b
    assert strip(fb) == " ".join(words) and strip(fa).split()[100] == "NEW"


def test_changed_summary():
    ch = lambda **kw: {"change_type": "modified", "before_text": "", "after_text": "", "code": None, **kw}
    assert changed_summary(ch(before_text="when any of", after_text="when all of")) == "any → all"
    assert changed_summary(ch(change_type="code_added", code="E0911")) == "+ E0911"
    assert changed_summary(ch(change_type="added", after_text="x " * 100)).endswith("…")
    assert changed_summary(ch(change_type="moved", from_path="A > B")) == "moved from A > B"
