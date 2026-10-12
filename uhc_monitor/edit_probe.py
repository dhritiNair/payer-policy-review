"""Run the classifier on each edit of a simulation file separately, and save the results.

Usage: python -m uhc_monitor.edit_probe config/simulations/<file>.json results/<name>.json [note]
Each edit is applied alone to the real policy text, so every change is attributed to exactly one edit.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import classify as rules
from .classify import AMBIGUOUS, AMBIGUOUS_SUBTITLE, compare, compare_with_notes
from .extract import extract_policy
from .simulate import load_edits, make_simulated_older

ROOT = Path(__file__).resolve().parent.parent


def main(edits_path, out_path):
    real = extract_policy(ROOT / "sources" / "original" / "beds-mattresses.pdf")
    edits = load_edits(edits_path)
    results = []
    for edit in edits:
        changes, notes = compare_with_notes(make_simulated_older(real, [edit]), real)
        results.append({"edit": edit, "notes": notes, "changes": [
            {k: c[k] for k in ("label", "rule_id", "rule", "evidence", "change_type", "path",
                               "before_text", "after_text", "before_page", "after_page")} for c in changes]})
    combined = compare(make_simulated_older(real, edits), real)

    for r in results:
        e = r["edit"]
        print(f"{e['id']} [{e['kind']}] {e['description']}")
        if not r["changes"]:
            print("   -> NO CHANGE REPORTED" + (f" (note: {'; '.join(r['notes'])})" if r["notes"] else ""))
        for c in r["changes"]:
            label = c["label"].upper() + (f" ({AMBIGUOUS_SUBTITLE})" if c["label"] == AMBIGUOUS else "")
            print(f"   -> {label} | rule {c['rule_id']} | {c['change_type']} | {c['path']}")
            print(f"      evidence: {c['evidence']}")
        print()
    print(f"all edits together: {len(combined)} changes (sum of separate runs: {sum(len(r['changes']) for r in results)})")

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": sys.argv[3] if len(sys.argv) > 3 else "",
        "config": {"classifier": "rules-only (no model)", "coverage_sections": sorted(rules.COVERAGE_SECTIONS),
                   "coverage_words": sorted(rules.COVERAGE_WORDS), "modal_words": sorted(rules.MODAL_WORDS),
                   "rules": {k: v[0] for k, v in rules.RULES.items()}},
        "code_commit": head, "edits_file": str(edits_path), "results": results}, indent=2, ensure_ascii=False))
    print(f"saved {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
