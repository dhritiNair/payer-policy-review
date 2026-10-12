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


ORDER = {"none": 0, "cosmetic": 1, "ambiguous": 2, "substantive": 3}


def strongest(changes, key):
    return max((c.get(key) or c["label"] for c in changes), key=ORDER.get, default="none")


def claude_stance(row):
    """Claude's answers on this edit's ambiguous changes, as short text."""
    out = []
    for c in row["changes"]:
        llm = c.get("llm")
        if llm and llm["status"] == "ok":
            a = llm["answer"]
            out.append(f"Claude suggested {a['suggested_label']} (confidence {a['confidence']})")
        elif llm:
            out.append(f"Claude gave no usable answer ({llm['status']})")
    return "; ".join(out)


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
    text = base + "." + tail if not base.endswith(".") else base + tail
    stance = claude_stance(row)
    if row.get("via") == "claude":
        text += f" The rules left it Ambiguous; Claude raised it to substantive ({stance})."
    elif stance:
        text += f" {stance}, which is advice only: it stays Ambiguous."
    return text


def score_results(results, labels):
    """Score each edit's decision. If the changes carry Claude's assessment (final_label), the final label is scored,
    and a substantive result is attributed to the rules ('rules') or to Claude's escalation ('claude')."""
    rows = []
    for r in results:
        e = r["edit"]
        truth = truth_for(e, labels)
        rules_pred = strongest(r["changes"], "label")
        pred = strongest(r["changes"], "final_label")
        via = None
        if pred == "substantive":
            via = "rules" if rules_pred == "substantive" else "claude"
        row = {"id": e["id"], "description": e["description"], "truth": truth, "predicted": pred, "rules_predicted": rules_pred,
               "via": via, "rule_ids": sorted({c["rule_id"] for c in r["changes"]}), "notes": r.get("notes", []),
               "changes": [{k: c.get(k) for k in ("label", "rule_id", "evidence", "change_type", "path", "before_text",
                                                  "after_text", "before_page", "after_page", "final_label", "final_source", "llm")}
                           for c in r["changes"]],
               "outcome": outcome(truth, pred) if truth else "unscored"}
        if row["outcome"] != "unscored" and (row["outcome"] not in ("correct_detection", "correct") or
                                             (row["via"] == "claude" and row["outcome"] != "correct_detection")):
            row["likely_cause"] = likely_cause(row)
        rows.append(row)
    return rows


def summarize(rows):
    scored = [r for r in rows if r["outcome"] != "unscored"]
    count = lambda f: sum(1 for r in scored if f(r))
    llms = [c["llm"] for r in rows for c in r["changes"] if c.get("llm")]
    asked = [l for l in llms if l["status"] != "skipped_no_key"]
    return {
        "edits": len(rows), "scored": len(scored),
        "rules_flagged_substantive": sum(1 for r in rows if r["via"] == "rules"),
        "claude_raised_substantive": sum(1 for r in rows if r["via"] == "claude"),
        "left_ambiguous": sum(1 for r in rows if r["predicted"] == "ambiguous"),
        "judged_cosmetic_or_quiet": sum(1 for r in rows if r["predicted"] in ("cosmetic", "none")),
        "true_substantive": count(lambda r: r["truth"] == "substantive"),
        "correct_substantive_detections": count(lambda r: r["outcome"] == "correct_detection"),
        "detected_by_rules": count(lambda r: r["outcome"] == "correct_detection" and r["via"] == "rules"),
        "claude_escalations": count(lambda r: r["via"] == "claude"),
        "claude_escalations_correct": count(lambda r: r["via"] == "claude" and r["truth"] == "substantive"),
        "claude_escalations_not_substantive_per_key": count(lambda r: r["via"] == "claude" and r["truth"] != "substantive"),
        "misses": count(lambda r: r["outcome"] == "miss"),
        "false_positives": count(lambda r: r["outcome"] == "false_positive"),
        "false_positives_from_rules": count(lambda r: r["outcome"] == "false_positive" and r["via"] == "rules"),
        "false_positives_from_claude": count(lambda r: r["outcome"] == "false_positive" and r["via"] == "claude"),
        "human_review_as_intended": count(lambda r: r["outcome"] == "correct" and r["truth"] == "ambiguous"),
        "human_review_for_true_substantive": count(lambda r: r["outcome"] == "human_review" and r["truth"] == "substantive"),
        "human_review_for_true_cosmetic": count(lambda r: r["outcome"] == "human_review" and r["truth"] == "cosmetic"),
        "correct_without_review": count(lambda r: r["outcome"] == "correct" and r["truth"] != "ambiguous"),
        "claude_calls": len(asked), "claude_answers_ok": sum(1 for l in asked if l["status"] == "ok"),
        "claude_suggested": {lab: sum(1 for l in asked if l["status"] == "ok" and l["answer"]["suggested_label"] == lab)
                             for lab in ("substantive", "cosmetic", "abstain")},
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


def claude_meta(assessor, sets):
    llms = [c["llm"] for _, results, _, _ in sets for r in results for c in r["changes"] if c.get("llm")]
    ok = [l for l in llms if l["status"] == "ok"]
    return {"used_in_this_run": True, "model": assessor.config["model"], "prompt_version": assessor.config["prompt_version"],
            "effort": assessor.config["effort"], "max_tokens": assessor.config["max_tokens"],
            "prompt_sha256": assessor.prompt_sha256, "calls": len([l for l in llms if l["status"] != "skipped_no_key"]),
            "answers_ok": len(ok), "response_models": sorted({l.get("response_model") for l in ok if l.get("response_model")}),
            "input_tokens": sum((l.get("usage") or {}).get("input_tokens") or 0 for l in ok),
            "output_tokens": sum((l.get("usage") or {}).get("output_tokens") or 0 for l in ok),
            "request_ids": [l.get("request_id") for l in ok],
            "note": "Claude is advisory and can only escalate an ambiguous change to substantive; it never dismisses one."}


def metadata(saved_from=None, saved=None, assessor=None, sets=None, policies=None):
    llm = load_llm_config()
    git = _git()
    if saved:   # describe the run that is being scored, not today's code
        classifier = saved.get("config") or {"classifier": "rules-only (config not recorded by that run)"}
        code = {"commit": saved.get("code_commit", "unknown"), "uncommitted_changes": False,
                "scored_with_commit": git["commit"]}
        llm = {"model": "not recorded", "prompt_version": "not recorded", "effort": "not recorded"}
    else:
        classifier, code = rules_config(), git
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code": code, "scored_saved_run": saved_from,
        "classifier": classifier,
        "claude": claude_meta(assessor, sets) if assessor else {"used_in_this_run": False, "model": llm["model"], "prompt_version": llm["prompt_version"],
                   "effort": llm["effort"], "note": "The probe and the evaluation never call Claude (rules only). For a fresh run the configured "
                   "model and prompt version are logged so a run that does use Claude can be reproduced; for a saved "
                   "run they are 'not recorded' because that run did not log them."},
        "real_policies_sha256": policies or "not recorded",
        "disclaimer": DISCLAIMER,
    }


def render(report, meta):
    claude = meta["claude"]["used_in_this_run"]
    out = ["UHC policy monitor: evaluation" + (" (rules + Claude)" if claude else ""), "",
           f"run at {meta['run_at']} | code commit {meta['code']['commit'][:7]}"
           f"{' (+ uncommitted changes)' if meta['code']['uncommitted_changes'] else ''}"
           + (f" | scoring saved run {meta['scored_saved_run']}" if meta["scored_saved_run"] else "")]
    if claude:
        c = meta["claude"]
        out += [f"classifier: rules, then Claude on ambiguous changes only (advisory; it can escalate to substantive, never dismiss)",
                f"Claude: model {c['model']} (served by {', '.join(c['response_models']) or 'n/a'}) | prompt {c['prompt_version']} "
                f"(sha256 {c['prompt_sha256'][:12]}) | effort {c['effort']} | max_tokens {c['max_tokens']} | no temperature/thinking set",
                f"calls: {c['calls']} ({c['answers_ok']} usable answers) | tokens: {c['input_tokens']} in, {c['output_tokens']} out"]
    else:
        out += [f"classifier: {meta['classifier']['classifier']} | Claude used in this run: no (configured: {meta['claude']['model']}, "
                f"prompt {meta['claude']['prompt_version']}, effort {meta['claude']['effort']})"]
    out.append("")
    for s in report["sets"]:
        out += [f"=== {s['name']}  ({s['summary']['edits']} edits, {s['summary']['scored']} scored)"]
        head = f"{'edit':5} {'truth':12} {'rules said':12} {'final':12} {'rule(s)':22} outcome" if claude else \
               f"{'edit':5} {'truth':12} {'predicted':12} {'rule(s)':26} outcome"
        out.append(head)
        for r in s["rows"]:
            via = f" [{'Claude escalation' if r['via'] == 'claude' else 'rules'}]" if r["via"] else ""
            if claude:
                out.append(f"{r['id']:5} {str(r['truth']):12} {r['rules_predicted']:12} {r['predicted']:12} "
                           f"{','.join(r['rule_ids']) or '-':22} {r['outcome']}{via}")
            else:
                out.append(f"{r['id']:5} {str(r['truth']):12} {r['predicted']:12} {','.join(r['rule_ids']) or '-':26} {r['outcome']}")
        out += ["", _summary_lines(s["summary"], claude), ""]
        if claude:
            out += _claude_section(s["rows"])
        errs = [r for r in s["rows"] if "likely_cause" in r]
        if errs:
            out.append("Errors and avoidable review (source evidence and likely cause):")
        for r in errs:
            out.append(f"\n  {r['id']} [{r['outcome']}] truth={r['truth']} final={r['predicted']}: {r['description']}")
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
    out += ["=== ALL SETS", _summary_lines(report["total"], claude), "", meta["disclaimer"]]
    return "\n".join(out)


def _summary_lines(m, claude=False):
    if m["scored"] == 0:      # no ground truth yet: attribute the decisions, score nothing
        lines = [f"  NO KEY YET: {m['edits']} edits classified, none scored",
                 f"  flagged substantive by the RULES alone : {m['rules_flagged_substantive']}",
                 f"  raised to substantive by CLAUDE        : {m['claude_raised_substantive']} (rules said Ambiguous)" if claude
                 else "  (Claude not used)",
                 f"  left Ambiguous (needs human review)    : {m['left_ambiguous']}",
                 f"  cosmetic or quietly ignored            : {m['judged_cosmetic_or_quiet']}"]
        if claude:
            sg = m["claude_suggested"]
            lines.append(f"  Claude's suggestions: {m['claude_calls']} calls, {m['claude_answers_ok']} usable: "
                         f"{sg['substantive']} substantive, {sg['cosmetic']} cosmetic, {sg['abstain']} abstain")
        return "\n".join(lines)
    review = m["human_review_as_intended"] + m["human_review_for_true_substantive"] + m["human_review_for_true_cosmetic"]
    lines = []
    if claude:
        lines += [f"  detected by the RULES alone : {m['detected_by_rules']} of {m['true_substantive']} true-substantive edits",
                  f"  Claude escalations          : {m['claude_escalations']} (ambiguous -> substantive by Claude): "
                  f"{m['claude_escalations_correct']} correct per the key, {m['claude_escalations_not_substantive_per_key']} not substantive per the key",
                  f"  total detected as substantive: {m['correct_substantive_detections']} of {m['true_substantive']} "
                  f"({m['detected_by_rules']} rules + {m['claude_escalations_correct']} Claude)"]
    else:
        lines += [f"  automatic detection : {m['correct_substantive_detections']} of {m['true_substantive']} true-substantive "
                  f"edits detected as substantive"]
    lines += [f"  misses              : {m['misses']}",
              f"  false positives     : {m['false_positives']}" + (f" ({m['false_positives_from_rules']} from rules, "
                                                                   f"{m['false_positives_from_claude']} from Claude escalations)" if claude else ""),
              f"  human review        : {review} left for a human ({m['human_review_as_intended']} as intended for genuinely ambiguous edits, "
              f"{m['human_review_for_true_substantive']} true-substantive not auto-detected, "
              f"{m['human_review_for_true_cosmetic']} true-cosmetic avoidable)",
              f"  correct, no review  : {m['correct_without_review']}"]
    if claude:
        sg = m["claude_suggested"]
        lines.append(f"  Claude's suggestions: {m['claude_calls']} calls, {m['claude_answers_ok']} usable: "
                     f"{sg['substantive']} substantive, {sg['cosmetic']} cosmetic, {sg['abstain']} abstain")
    return "\n".join(lines)


def _claude_section(rows):
    out = ["Claude on the ambiguous items (reasoning and confidence):"]
    shown = False
    for r in rows:
        for c in r["changes"]:
            llm = c.get("llm")
            if not llm:
                continue
            shown = True
            fate = ("ESCALATED to substantive" if c.get("final_label") == "substantive" else "stays Ambiguous (advice only)")
            out.append(f"\n  {r['id']}  key says: {r['truth'] or 'no key yet'} | rules: {c['label']} ({c['rule_id']}) | final: {r['predicted']} | {r['outcome']}")
            if llm["status"] != "ok":
                out.append(f"     Claude: no usable answer ({llm['status']}{': ' + llm['detail'] if llm.get('detail') else ''}) -> stays Ambiguous")
                continue
            a = llm["answer"]
            out += [f"     Claude suggests: {a['suggested_label'].upper()} | Claude's self-reported confidence: {a['confidence']} -> {fate}",
                    f"     what the texts say : {a['what_changed']}",
                    f"     interpretation     : {a['interpretation']}",
                    f"     uncertainty        : {a['uncertainty']}",
                    f"     suggested next step: {a['suggested_next_step']}"]
    if not shown:
        out.append("  (no ambiguous items in this set)")
    out.append("")
    return out


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
    ap.add_argument("--claude", action="store_true",
                    help="also ask Claude about each ambiguous change (live API call; key from ANTHROPIC_API_KEY or .env)")
    ap.add_argument("--set", dest="only", help="only the edit set with this file name (without .json)")
    a = ap.parse_args(argv)

    sets, files_meta, assessor, policies = [], {}, None, {}
    if a.saved:
        saved = json.loads(Path(a.saved).read_text())
        labels = json.loads(Path(a.labels).read_text())["labels"] if a.labels else {}
        sets.append((Path(a.saved).stem, saved["results"], labels,
                     {"results": a.saved, "results_sha256": _sha(a.saved), "labels": a.labels,
                      "labels_sha256": _sha(a.labels) if a.labels else None}))
    else:
        assessor = None
        if a.claude:
            from .llm import ClaudeAssessor
            out_base = Path(a.out) if a.out else ROOT / "results" / f"eval-{Path(a.folder).name}-claude"
            assessor = ClaudeAssessor(log_path=out_base.with_name(out_base.name + "-claude-calls.jsonl"))
            if not assessor.available:
                sys.exit("--claude needs an API key: set ANTHROPIC_API_KEY or put it in .env (nothing was run)")
        for name, edits_path, labels_path in find_sets(a.folder):
            if a.only and name != a.only:
                continue
            data = json.loads(edits_path.read_text())
            source = data.get("source") or data.get("policy") or "beds-mattresses"
            _, results = probe_edits(data["edits"], source, assessor)
            policies[source] = _sha(ROOT / "sources" / "original" / f"{source}.pdf")
            labels = json.loads(labels_path.read_text())["labels"] if labels_path else {}
            sets.append((name, results, labels, {"edits": str(edits_path), "edits_sha256": _sha(edits_path),
                                                 "labels": str(labels_path) if labels_path else None,
                                                 "labels_sha256": _sha(labels_path) if labels_path else None}))
    if not sets:
        sys.exit(f"no edit sets (*.json) found in {a.folder}")
    report = evaluate_sets(sets)
    if a.saved and saved.get("real_pdf_sha256"):
        policies = {saved.get("source", "beds-mattresses"): saved["real_pdf_sha256"]}
    meta = metadata(saved_from=a.saved, saved=saved if a.saved else None, assessor=assessor if not a.saved else None, sets=sets,
                    policies=policies)
    if meta["claude"]["used_in_this_run"]:
        meta["classifier"] = {**meta["classifier"], "classifier": "rules, then Claude fallback on ambiguous changes (advisory, escalate-only)"}
    out = a.out or str(ROOT / "results" / f"eval-{Path(a.folder).name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}")
    js, tx = _write(out, report, meta, a.force)
    print(tx.read_text())
    print(f"saved {js} and {tx}")


if __name__ == "__main__":
    main()
