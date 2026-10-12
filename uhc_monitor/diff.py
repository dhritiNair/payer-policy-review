"""Compare two extracted policy versions and list the changes between them.

Only the 'sections' content is compared: the Policy History table (excluded_from_diff) is skipped,
and simulation metadata is never read.
"""
import re
from difflib import SequenceMatcher

PAIR_SIMILARITY = 0.6   # a replaced paragraph pairs with a new one if at least this similar


def tokens(text):
    """Lower-case words and numbers; case, spacing, punctuation and hyphenation drop out."""
    return re.findall(r"[a-z0-9]+", text.lower())


def word_changes(before, after):
    """(removed words, added words) between two texts."""
    a, b = tokens(before), tokens(after)
    removed, added = [], []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            removed += a[i1:i2]
            added += b[j1:j2]
    return removed, added


def _similarity(a, b):
    # autojunk=False: by default difflib ignores common characters in strings over 200 chars,
    # which made one-word edits to long paragraphs look unrelated (ratio 0.3 instead of 0.99)
    return SequenceMatcher(None, a.lower(), b.lower(), autojunk=False).ratio()


def _norm(text):
    return " ".join(text.split())


def _index_sections(doc):
    """key -> info for every comparable section. A section's top-level parent (h1) is tracked."""
    out, seen, h1 = {}, {}, None
    for s in doc["sections"]:
        if s["level"] in (0, 1):
            h1 = s["heading"]
        if s.get("excluded_from_diff"):
            continue
        n = seen[s["heading"]] = seen.get(s["heading"], 0) + 1
        out[(s["heading"], n)] = {"section": s, "h1": h1}
    return out


def _change(kind, info, before=None, after=None, code=None):
    s = info["section"]
    return {
        "change_type": kind,                    # modified | added | removed | moved | code_added | code_removed | code_modified
        "section": s["heading"],
        "h1": info["h1"],
        "path": s["heading"] if info["h1"] == s["heading"] else f"{info['h1']} > {s['heading']}",
        "code": code,
        "before_text": before["text"] if before else None,
        "after_text": after["text"] if after else None,
        "before_page": before["page"] if before else None,
        "after_page": after["page"] if after else None,
        "is_toc": bool((before or after)["text"].startswith("Table of Contents")),
    }


def _diff_paragraphs(info_b, info_a):
    pb = info_b["section"]["paragraphs"] if info_b else []
    pa = info_a["section"]["paragraphs"] if info_a else []
    info = info_a or info_b
    out = []
    ops = SequenceMatcher(None, [_norm(p["text"]) for p in pb], [_norm(p["text"]) for p in pa],
                          autojunk=False).get_opcodes()
    for op, i1, i2, j1, j2 in ops:
        if op == "equal":
            continue
        left, right = list(range(i1, i2)), list(range(j1, j2))
        for i in left:
            best = max(right, key=lambda j: _similarity(pb[i]["text"], pa[j]["text"]), default=None)
            if best is not None and _similarity(pb[i]["text"], pa[best]["text"]) >= PAIR_SIMILARITY:
                right.remove(best)
                out.append(_change("modified", info, pb[i], pa[best]))
            else:
                out.append(_change("removed", info, pb[i], None))
        for j in right:
            out.append(_change("added", info, None, pa[j]))
    return out


def _diff_codes(info_b, info_a):
    cb = {r["code"]: r for r in (info_b["section"].get("code_table", []) if info_b else [])}
    ca = {r["code"]: r for r in (info_a["section"].get("code_table", []) if info_a else [])}
    info = info_a or info_b
    out = []
    as_para = lambda r: {"text": f"{r['code']} {r['description']}", "page": r["page"]}
    for code in sorted(set(cb) | set(ca)):
        if code not in ca:
            out.append(_change("code_removed", info, as_para(cb[code]), None, code))
        elif code not in cb:
            out.append(_change("code_added", info, None, as_para(ca[code]), code))
        elif _norm(cb[code]["description"]) != _norm(ca[code]["description"]):
            out.append(_change("code_modified", info, as_para(cb[code]), as_para(ca[code]), code))
    return out


def _resolve_moves(changes):
    """Pair a removed paragraph with an identical added one.
    Same section: a pure reorder, dropped from the changes and returned as a quiet note.
    Different sections: merged into one 'moved' change."""
    kept, notes = [], []
    unmatched_added = [c for c in changes if c["change_type"] == "added"]
    reordered = {}
    dropped = set()
    for c in changes:
        if c["change_type"] != "removed":
            continue
        match = next((a for a in unmatched_added if _norm(a["after_text"]) == _norm(c["before_text"])), None)
        if match is None:
            continue
        unmatched_added.remove(match)
        dropped.update((id(c), id(match)))
        if match["section"] == c["section"]:
            reordered[c["path"]] = reordered.get(c["path"], 0) + 1
        else:
            kept.append({**match, "change_type": "moved", "before_text": c["before_text"],
                         "before_page": c["before_page"], "from_path": c["path"]})
    kept += [c for c in changes if id(c) not in dropped]
    for path, n in reordered.items():
        notes.append(f"{n} paragraph(s) reordered within '{path}' (wording unchanged; not reported as a change)")
    return kept, notes


def diff_documents_with_notes(before, after):
    """(changes, notes): all differences from `before` to `after`, plus quiet notes (e.g. pure reorders)."""
    sb, sa = _index_sections(before), _index_sections(after)
    keys = list(sa) + [k for k in sb if k not in sa]
    changes = []
    for k in keys:
        changes += _diff_paragraphs(sb.get(k), sa.get(k))
        changes += _diff_codes(sb.get(k), sa.get(k))
    return _resolve_moves(changes)


def diff_documents(before, after):
    """All differences from `before` to `after`, in document order."""
    return diff_documents_with_notes(before, after)[0]
