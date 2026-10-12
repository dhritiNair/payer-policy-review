"""Evaluation: score the classifier's decisions on edit sets against ground-truth labels.

One command, any folder of examples:
    python -m uhc_monitor.evaluate [FOLDER] [--out results/NAME] [--force]
Every `*.json` in FOLDER is an edit set (same format as config/simulations/*.json). Labels come from an optional
sibling `<name>.labels.json` ({"labels": {"<edit id>": "substantive|cosmetic|ambiguous|excluded"}}) or, failing that,
from each edit's own `kind`. Edits with no label are classified and listed but not scored.

To score a run that was saved earlier (without re-running anything):
    python -m uhc_monitor.evaluate --saved results/x-run.json --labels labels.json --out results/NAME

Output: results/NAME.json and results/NAME.txt. An existing result is never overwritten without --force, so an
'initial' result cannot be replaced by accident. Samples here are small: results are diagnostic, not an accuracy figure.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from .edit_probe import probe_edits, rules_config
from .llm import load_config as load_llm_config
from .score import CAUSE, outcome, predict
from .simulate import load_edits

ROOT = Path(__file__).resolve().parent.parent
TRUTHS = ("substantive", "cosmetic", "ambiguous", "excluded")
DISCLAIMER = ("Small samples: these results are diagnostic only. They show where the rules behave well or badly on "
              "these examples, not how accurate the tool is in general.")


def around_change(before, after, context=10):
    """Plain-text excerpts of before/after around the change, changed words in [[ ]] (for the text report)."""
    b, a = (before or "").split(), (after or "").split()
    bf, af = [False] * len(b), [False] * len(a)
    for op, i1, i2, j1, j2 in SequenceMatcher(None, b, a, autojunk=False).get_opcodes():
        if op != "equal":
            bf[i1:i2] = [True] * (i2 - i1)
            af[j1:j2] = [True] * (j2 - j1)

    def cut(words, flags):
        hit = [i for i, f in enumerate(flags) if f]
        lo, hi = (max(0, hit[0] - context), min(len(words), hit[-1] + 1 + context)) if hit else (0, min(len(words), 2 * context))
        marked = [f"[[{w}]]" if f else w for w, f in zip(words, flags)][lo:hi]
        return ("… " if lo else "") + " ".join(marked) + (" …" if hi < len(words) else "")
    return cut(b, bf), cut(a, af)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git():
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "HEAD"), "uncommitted_changes": bool(run("status", "--porcelain", "--", "uhc_monitor", "config"))}


def find_sets(folder):
    """[(name, edits_path, labels_path or None)] for every edit set in the folder."""
    out = []
    for p in sorted(Path(folder).glob("*.json")):
        if p.name.endswith(".labels.json"):
            continue
        labels = p.with_name(p.stem + ".labels.json")
        out.append((p.stem, p, labels if labels.exists() else None))
    return out


def truth_for(edit, labels):
    t = labels.get(edit["id"], edit.get("kind"))
    return t if t in TRUTHS else None


def likely_cause(row):
    rules = sorted({c["rule_id"] for c in row["changes"]})
    base = "; ".join(CAUSE.get(r, r) for r in rules) if rules else "no change was reported for this edit"
    tail = {
        "human_review": {
            "substantive": " Sent to a human, so not missed, but the rules did not detect this substantive change on their own.",
            "cosmetic": " Sent to a human although the key says it is cosmetic: avoidable review work, no rule recognises it as harmless.",
        },
        "miss": {"substantive": " The change matters but nothing was flagged.", "ambiguous": " The change was not surfaced for review."},
        "false_positive": {"cosmetic": " Alerted on a change the key says is cosmetic.", "excluded": " Alerted on text that is excluded from comparison.",
                           "ambiguous": " Labelled substantive with confidence on a change the key says is genuinely unclear."},
    }.get(row["outcome"], {}).get(row["truth"], "")
    return base + "." + tail if not base.endswith(".") else base + tail


def score_results(results, labels):
    rows = []
    for r in results:
        e = r["edit"]
        truth = truth_for(e, labels)
        pred = predict(r["changes"])
        row = {"id": e["id"], "description": e["description"], "truth": truth, "predicted": pred,
               "rule_ids": sorted({c["rule_id"] for c in r["changes"]}), "notes": r.get("notes", []),
               "changes": [{k: c[k] for k in ("label", "rule_id", "evidence", "change_type", "path", "before_text",
                                              "after_text", "before_page", "after_page")} for c in r["changes"]],
               "outcome": outcome(truth, pred) if truth else "unscored"}
        if row["outcome"] not in ("correct_detection", "correct", "unscored"):
            row["likely_cause"] = likely_cause(row)
        rows.append(row)
    return rows


def summarize(rows):
    scored = [r for r in rows if r["outcome"] != "unscored"]
    count = lambda f: sum(1 for r in scored if f(r))
    return {
        "edits": len(rows), "scored": len(scored),
        "true_substantive": count(lambda r: r["truth"] == "substantive"),
        "correct_substantive_detections": count(lambda r: r["outcome"] == "correct_detection"),
        "misses": count(lambda r: r["outcome"] == "miss"),
        "false_positives": count(lambda r: r["outcome"] == "false_positive"),
        "human_review_as_intended": count(lambda r: r["outcome"] == "correct" and r["truth"] == "ambiguous"),
        "human_review_for_true_substantive": count(lambda r: r["outcome"] == "human_review" and r["truth"] == "substantive"),
        "human_review_for_true_cosmetic": count(lambda r: r["outcome"] == "human_review" and r["truth"] == "cosmetic"),
        "correct_without_review": count(lambda r: r["outcome"] == "correct" and r["truth"] != "ambiguous"),
    }


def evaluate_sets(sets):
    """sets: [(name, results, labels, files)] -> report dict (no I/O)."""
    report = {"sets": [], "total": None}
    all_rows = []
    for name, results, labels, files in sets:
        rows = score_results(results, labels)
        all_rows += rows
        report["sets"].append({"name": name, "files": files, "summary": summarize(rows), "rows": rows})
    report["total"] = summarize(all_rows)
    return report


def metadata(sets_files, saved_from=None):
    llm = load_llm_config()
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code": _git(), "scored_saved_run": saved_from,
        "classifier": rules_config(),
        "claude": {"used_in_this_run": False, "model": llm["model"], "prompt_version": llm["prompt_version"],
                   "effort": llm["effort"], "note": "Claude is not called by the evaluation; model and prompt version "
                   "are logged so a run that does use it can be reproduced."},
        "real_policy_sha256": _sha(ROOT / "sources" / "original" / "beds-mattresses.pdf"),
        "disclaimer": DISCLAIMER,
    }


def render(report, meta):
    out = ["UHC policy monitor: evaluation", "",
           f"run at {meta['run_at']} | code commit {meta['code']['commit'][:7]}"
           f"{' (+ uncommitted changes)' if meta['code']['uncommitted_changes'] else ''}"
           + (f" | scoring saved run {meta['scored_saved_run']}" if meta["scored_saved_run"] else ""),
           f"classifier: {meta['classifier']['classifier']} | Claude used in this run: "
           f"{'yes' if meta['claude']['used_in_this_run'] else 'no'} (configured: {meta['claude']['model']}, prompt "
           f"{meta['claude']['prompt_version']}, effort {meta['claude']['effort']})", ""]
    for s in report["sets"]:
        out += [f"=== {s['name']}  ({s['summary']['edits']} edits, {s['summary']['scored']} scored)"]
        out += [f"{'edit':5} {'truth':12} {'predicted':12} {'rule(s)':26} outcome"]
        for r in s["rows"]:
            out.append(f"{r['id']:5} {str(r['truth']):12} {r['predicted']:12} {','.join(r['rule_ids']) or '-':26} {r['outcome']}")
        out += ["", _summary_lines(s["summary"]), ""]
        errs = [r for r in s["rows"] if "likely_cause" in r]
        if errs:
            out.append("Errors and avoidable review (source evidence and likely cause):")
        for r in errs:
            out.append(f"\n  {r['id']} [{r['outcome']}] truth={r['truth']} predicted={r['predicted']}: {r['description']}")
            for c in r["changes"]:
                b, a = around_change(c["before_text"], c["after_text"])
                out += [f"     ({c['change_type']}, {c['path']}, before p.{c['before_page']} -> after p.{c['after_page']})",
                        f"        before: {b or '(nothing)'}", f"        after:  {a or '(nothing)'}",
                        f"        rule {c['rule_id']}: {c['evidence']}"]
            for n in r["notes"]:
                out.append(f"     note: {n}")
            out.append(f"     likely cause: {r['likely_cause']}")
        if errs:
            out.append("")
    out += ["=== ALL SETS", _summary_lines(report["total"]), "", meta["disclaimer"]]
    return "\n".join(out)


def _summary_lines(m):
    return (f"  automatic detection : {m['correct_substantive_detections']} of {m['true_substantive']} true-substantive "
            f"edits detected as substantive\n"
            f"  misses              : {m['misses']}\n"
            f"  false positives     : {m['false_positives']}\n"
            f"  human review        : {m['human_review_as_intended'] + m['human_review_for_true_substantive'] + m['human_review_for_true_cosmetic']} "
            f"sent to a human ({m['human_review_as_intended']} as intended for genuinely ambiguous edits, "
            f"{m['human_review_for_true_substantive']} true-substantive not auto-detected, "
            f"{m['human_review_for_true_cosmetic']} true-cosmetic avoidable)\n"
            f"  correct, no review  : {m['correct_without_review']}")


def _write(out_base, report, meta, force):
    out_base = Path(out_base)
    js, tx = out_base.with_suffix(".json"), out_base.with_suffix(".txt")
    if (js.exists() or tx.exists()) and not force:
        sys.exit(f"refusing to overwrite {js} (an existing result); use --force or another --out")
    js.parent.mkdir(parents=True, exist_ok=True)
    js.write_text(json.dumps({"meta": meta, **report}, indent=2, ensure_ascii=False))
    tx.write_text(render(report, meta) + "\n")
    return js, tx


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score classifier decisions on edit sets against labels.")
    ap.add_argument("folder", nargs="?", default=str(ROOT / "config" / "simulations"))
    ap.add_argument("--saved", help="score this saved edit_probe results file instead of running the edits")
    ap.add_argument("--labels", help="labels file for --saved")
    ap.add_argument("--out", help="output base path, default results/eval-<folder name>-<timestamp>")
    ap.add_argument("--force", action="store_true", help="overwrite an existing result")
    a = ap.parse_args(argv)

    sets, files_meta = [], {}
    if a.saved:
        saved = json.loads(Path(a.saved).read_text())
        labels = json.loads(Path(a.labels).read_text())["labels"] if a.labels else {}
        sets.append((Path(a.saved).stem, saved["results"], labels,
                     {"results": a.saved, "results_sha256": _sha(a.saved), "labels": a.labels,
                      "labels_sha256": _sha(a.labels) if a.labels else None}))
    else:
        for name, edits_path, labels_path in find_sets(a.folder):
            data = json.loads(edits_path.read_text())
            _, results = probe_edits(data["edits"], data.get("policy", "beds-mattresses"))
            labels = json.loads(labels_path.read_text())["labels"] if labels_path else {}
            sets.append((name, results, labels, {"edits": str(edits_path), "edits_sha256": _sha(edits_path),
                                                 "labels": str(labels_path) if labels_path else None,
                                                 "labels_sha256": _sha(labels_path) if labels_path else None}))
    if not sets:
        sys.exit(f"no edit sets (*.json) found in {a.folder}")
    report = evaluate_sets(sets)
    meta = metadata(None, saved_from=a.saved)
    out = a.out or str(ROOT / "results" / f"eval-{Path(a.folder).name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}")
    js, tx = _write(out, report, meta, a.force)
    print(tx.read_text())
    print(f"saved {js} and {tx}")


if __name__ == "__main__":
    main()
