"""Diff the SIMULATED older Beds and Mattresses against the real one and show every decision."""
import copy
import textwrap
from pathlib import Path

from .classify import AMBIGUOUS, AMBIGUOUS_SUBTITLE, compare
from .extract import extract_policy
from .simulate import load_edits, make_simulated_older

ROOT = Path(__file__).resolve().parent.parent


def clip(text, n=190):
    return text if len(text) <= n else text[:n] + "..."


def main():
    real = extract_policy(ROOT / "sources" / "original" / "beds-mattresses.pdf")
    older = make_simulated_older(real, load_edits(ROOT / "config" / "simulations" / "beds-mattresses.json"))
    changes = compare(older, real)
    print(f"SIMULATED older -> real current: {len(changes)} changes\n")
    for i, c in enumerate(changes, 1):
        label = c["label"].upper() + (f" ({AMBIGUOUS_SUBTITLE})" if c["label"] == AMBIGUOUS else "")
        bp = f"p.{c['before_page']}" if c["before_page"] else "-"
        ap = f"p.{c['after_page']}" if c["after_page"] else "-"
        print(f"#{i} {label} | {c['change_type']} | {c['path']} | SIMULATED {bp} -> real {ap}")
        print(f"   before: {clip(c['before_text']) if c['before_text'] else '(nothing)'}")
        print(f"   after:  {clip(c['after_text']) if c['after_text'] else '(nothing)'}")
        print(f"   rule {c['rule_id']}: {c['rule']}")
        print(f"   evidence: {c['evidence']}\n")
    tally = {}
    for c in changes:
        tally[c["label"]] = tally.get(c["label"], 0) + 1
    print("tally:", tally)
    print("real vs real:", len(compare(real, copy.deepcopy(real))), "changes")
    history_only = copy.deepcopy(real)
    for s in history_only["sections"]:
        if s.get("excluded_from_diff"):
            s["paragraphs"][0]["text"] += " (edited)"
    print("real vs real with History table edited:", len(compare(real, history_only)), "changes")


if __name__ == "__main__":
    main()
