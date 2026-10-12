"""Run the classifier on each edit of a simulation file separately, and save the results.

Usage: python -m uhc_monitor.edit_probe config/simulations/<file>.json results/<name>.json [note]
Each edit is applied alone to the real policy text, so every change is attributed to exactly one edit.
"""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import classify as rules
from .classify import AMBIGUOUS, AMBIGUOUS_SUBTITLE, compare, compare_with_notes
from .llm import apply_assessments
from .extract import extract_policy
from .simulate import load_edits, make_simulated_older

ROOT = Path(__file__).resolve().parent.parent


def rules_config():
    return {"classifier": "rules-only (no model)", "coverage_sections": sorted(rules.COVERAGE_SECTIONS),
            "coverage_words": sorted(rules.COVERAGE_WORDS), "modal_words": sorted(rules.MODAL_WORDS),
            "rules": {k: v[0] for k, v in rules.RULES.items()}}


CHANGE_FIELDS = ("label", "rule_id", "rule", "evidence", "change_type", "path", "before_text", "after_text",
                 "before_page", "after_page")


def probe_edits(edits, policy="beds-mattresses", assessor=None):
    """Apply each edit alone to the real policy text and classify what changes. Returns [{edit, notes, changes}].
    With an assessor, each ambiguous change is also sent to Claude (advisory; it can only escalate), and the change
    records carry final_label, final_source and the full Claude record under 'llm'."""
    real = extract_policy(ROOT / "sources" / "original" / f"{policy}.pdf")
    results = []
    for edit in edits:
        changes, notes = compare_with_notes(make_simulated_older(real, [edit]), real)
        if assessor:
            changes = apply_assessments(changes, assessor, real)
        keep = CHANGE_FIELDS + (("final_label", "final_source", "llm") if assessor else ())
        results.append({"edit": edit, "notes": notes, "changes": [{k: c[k] for k in keep} for c in changes]})
    return real, results


def source_of(edits_path):
    data = json.loads(Path(edits_path).read_text())
    return data.get("source") or data.get("policy") or "beds-mattresses"


def main(edits_path, out_path):
    edits = load_edits(edits_path)
    source = source_of(edits_path)
    real, results = probe_edits(edits, source)
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
        "config": rules_config(),
        "code_commit": head, "edits_file": str(edits_path), "source": source,
        "real_pdf_sha256": hashlib.sha256((ROOT / "sources" / "original" / f"{source}.pdf").read_bytes()).hexdigest(),
        "results": results}, indent=2, ensure_ascii=False))
    print(f"saved {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
