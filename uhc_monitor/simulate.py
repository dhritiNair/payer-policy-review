"""Build a SIMULATED older version by applying edits to extracted policy text. No PDF is created."""
import copy
import json

SIMULATED_LABEL = "SIMULATED older version (derived from the real policy text; not a real UHC publication)"


def load_edits(path):
    with open(path) as f:
        return json.load(f)["edits"]


def _section(doc, heading):
    matches = [s for s in doc["sections"] if s["heading"] == heading]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one section named {heading!r}, found {len(matches)}")
    return matches[0]


def _paragraph(section, needle):
    matches = [p for p in section["paragraphs"] if needle in p["text"]]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one paragraph containing {needle!r} in {section['heading']!r}, found {len(matches)}")
    return matches[0]


def apply_edit(doc, edit):
    op = edit["op"]
    if op == "replace_text":
        p = _paragraph(_section(doc, edit["section"]), edit["find"])
        p["text"] = p["text"].replace(edit["find"], edit["replace"], 1)
    elif op == "delete_paragraph":
        section = _section(doc, edit["section"])
        section["paragraphs"].remove(_paragraph(section, edit["find"]))
    elif op == "swap_paragraphs":
        section = _section(doc, edit["section"])
        a, b = _paragraph(section, edit["find"]), _paragraph(section, edit["find2"])
        ia, ib = section["paragraphs"].index(a), section["paragraphs"].index(b)
        section["paragraphs"][ia], section["paragraphs"][ib] = b, a
    elif op == "delete_code":
        matches = [(s, r) for s in doc["sections"] for r in s.get("code_table", []) if r["code"] == edit["code"]]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one code row {edit['code']!r}, found {len(matches)}")
        matches[0][0]["code_table"].remove(matches[0][1])
    else:
        raise ValueError(f"unknown edit op {op!r}")


def make_simulated_older(real_extracted, edits):
    """Return a deep copy of the real extraction with the edits applied, labelled as simulated."""
    older = copy.deepcopy(real_extracted)
    for edit in edits:
        apply_edit(older, edit)
    older["source_file"] = None
    older["simulated"] = True
    older["simulation"] = {"label": SIMULATED_LABEL, "edits": edits}
    return older
