"""Score saved edit-probe results against ground-truth labels, and compare two runs.

Usage: python -m uhc_monitor.score before.json after.json [labels.json]
Labels come from the edit's own 'kind', or from an optional labels file (for 'unlabeled' edits).

Per edit, the prediction is the strongest label among the changes raised (substantive > ambiguous >
cosmetic), or 'none' if no change was raised.
  truth substantive : substantive = correct detection | ambiguous = human review (abstention) | else miss
  truth cosmetic    : cosmetic/none = correct | ambiguous = human review (needless) | substantive = false positive
  truth ambiguous   : ambiguous = correct | substantive = false positive (over-confident) | else miss
  truth excluded    : none = correct | anything raised = false positive
"""
import json
import sys

ORDER = {"none": 0, "cosmetic": 1, "ambiguous": 2, "substantive": 3}

CAUSE = {
    "AMB-DEFAULT": "no rule recognises this kind of wording change, so it falls through to human review",
    "AMB-MODAL": "modal-word rule sends any may/should/must/will/can change to review, harmless or not",
    "AMB-MOVED": "paragraph moved between sections; rules cannot judge the change in context",
    "SUB-KEYWORD": "keyword rule fires on the word itself, not on whether the meaning changed",
    "SUB-NUMBER": "number rule fires on any digit token (including code numbers and dates)",
    "SUB-PARAGRAPH": "any added or removed paragraph in a coverage section counts as substantive",
    "SUB-CODE-LIST": "any added or removed code counts as substantive",
}


def predict(changes):
    return max((c["label"] for c in changes), key=ORDER.get, default="none")


def outcome(truth, pred):
    if truth == "substantive":
        return {"substantive": "correct_detection", "ambiguous": "human_review"}.get(pred, "miss")
    if truth == "cosmetic":
        return "correct" if pred in ("cosmetic", "none") else ("human_review" if pred == "ambiguous" else "false_positive")
    if truth == "ambiguous":
        return "correct" if pred == "ambiguous" else ("false_positive" if pred == "substantive" else "miss")
    if truth == "excluded":
        return "correct" if pred == "none" else "false_positive"
    raise ValueError(f"unknown truth label {truth!r}")


def load(path, labels):
    rows = {}
    for r in json.load(open(path))["results"]:
        e = r["edit"]
        truth = labels.get(e["id"], e["kind"])
        pred = predict(r["changes"])
        rows[e["id"]] = {"id": e["id"], "truth": truth, "pred": pred, "outcome": outcome(truth, pred),
                         "description": e["description"], "changes": r["changes"], "notes": r.get("notes", [])}
    return rows


def tally(rows):
    t = {k: 0 for k in ("correct_detection", "correct", "human_review", "miss", "false_positive")}
    for r in rows.values():
        t[r["outcome"]] += 1
    return t


def show_run(title, rows):
    t = tally(rows)
    print(f"{title}: {len(rows)} edits | correct substantive detections {t['correct_detection']} | other correct {t['correct']} | "
          f"human review {t['human_review']} | misses {t['miss']} | false positives {t['false_positive']}")


def main(before_path, after_path, labels_path=None):
    labels = json.load(open(labels_path))["labels"] if labels_path else {}
    before, after = load(before_path, labels), load(after_path, labels)
    print(f"before: {before_path}\nafter:  {after_path}\n")
    print(f"{'edit':5} {'truth':12} {'before':12} {'after':12} outcome before -> after")
    for i in after:
        b, a = before[i], after[i]
        flag = "" if b["outcome"] == a["outcome"] else "   <- changed"
        print(f"{i:5} {a['truth']:12} {b['pred']:12} {a['pred']:12} {b['outcome']} -> {a['outcome']}{flag}")
    print()
    show_run("before", before)
    show_run("after ", after)
    print("\nErrors and review cases after the fixes (automatic detection is counted apart from human review):")
    for r in after.values():
        if r["outcome"] in ("correct_detection", "correct"):
            continue
        print(f"\n{r['id']} [{r['outcome']}] truth={r['truth']} predicted={r['pred']}: {r['description']}")
        for c in r["changes"]:
            print(f"   source evidence ({c['change_type']}, {c['path']}):")
            print(f"      before: {(c['before_text'] or '-')[:150]}")
            print(f"      after:  {(c['after_text'] or '-')[:150]}")
            print(f"      rule {c['rule_id']}: {c['evidence']}")
            print(f"      likely cause: {CAUSE.get(c['rule_id'], c['rule'])}")
        if not r["changes"]:
            print("   nothing raised" + (f"; note: {'; '.join(r['notes'])}" if r["notes"] else ""))
    print("\nNote: 7 blind + 11 original edits is a small sample. Results are diagnostic only, not a measure of accuracy.")


if __name__ == "__main__":
    main(*sys.argv[1:4])
