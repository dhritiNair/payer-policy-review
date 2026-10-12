"""The evaluation: scoring rules, regression numbers for the three edit sets, and running it on a new folder."""
import json
import shutil

import pytest

from tests.conftest import ROOT
from uhc_monitor import evaluate
from uhc_monitor.edit_probe import probe_edits
from uhc_monitor.score import outcome
from uhc_monitor.simulate import load_edits

SIMS = ROOT / "config" / "simulations"


@pytest.mark.parametrize("truth, predicted, expected", [
    ("substantive", "substantive", "correct_detection"),    # automatic detection
    ("substantive", "ambiguous", "human_review"),           # abstention: not counted as a detection
    ("substantive", "cosmetic", "miss"), ("substantive", "none", "miss"),
    ("cosmetic", "cosmetic", "correct"), ("cosmetic", "none", "correct"),
    ("cosmetic", "ambiguous", "human_review"), ("cosmetic", "substantive", "false_positive"),
    ("ambiguous", "ambiguous", "correct"), ("ambiguous", "substantive", "false_positive"),
    ("ambiguous", "cosmetic", "miss"), ("ambiguous", "none", "miss"),
    ("excluded", "none", "correct"), ("excluded", "substantive", "false_positive"),
])
def test_outcome_table(truth, predicted, expected):
    assert outcome(truth, predicted) == expected


def test_abstention_is_counted_apart_from_automatic_detection():
    results = [{"edit": {"id": "x", "kind": "substantive", "description": "d"}, "notes": [],
                "changes": [{"label": "ambiguous", "rule_id": "AMB-MODAL", "evidence": "e", "change_type": "modified", "path": "p",
                             "before_text": "a", "after_text": "b", "before_page": 1, "after_page": 1}]}]
    s = evaluate.summarize(evaluate.score_results(results, {}))
    assert s["correct_substantive_detections"] == 0 and s["human_review_for_true_substantive"] == 1 and s["misses"] == 0


def summaries():
    out = {}
    for name, edits_path, labels_path in evaluate.find_sets(SIMS):
        data = json.loads(edits_path.read_text())
        _, results = probe_edits(data["edits"], data.get("source", "beds-mattresses"))
        labels = json.loads(labels_path.read_text())["labels"] if labels_path else {}
        out[name] = evaluate.summarize(evaluate.score_results(results, labels))
    return out


def test_regression_numbers_for_the_four_edit_sets():
    """Pins today's behaviour. If a rule change moves these on purpose, update them in the same commit and say why."""
    s = summaries()
    pick = lambda m: {k: m[k] for k in ("edits", "true_substantive", "correct_substantive_detections", "misses", "false_positives",
                                         "human_review_as_intended", "human_review_for_true_substantive",
                                         "human_review_for_true_cosmetic", "correct_without_review")}
    assert pick(s["beds-mattresses"]) == dict(edits=11, true_substantive=4, correct_substantive_detections=4, misses=0, false_positives=0,
                                              human_review_as_intended=2, human_review_for_true_substantive=0,
                                              human_review_for_true_cosmetic=0, correct_without_review=5)
    assert pick(s["beds-mattresses-blind"]) == dict(edits=7, true_substantive=3, correct_substantive_detections=3, misses=0, false_positives=0,
                                                    human_review_as_intended=1, human_review_for_true_substantive=0,
                                                    human_review_for_true_cosmetic=2, correct_without_review=1)
    assert pick(s["beds-mattresses-independent"]) == dict(edits=12, true_substantive=6, correct_substantive_detections=5, misses=0, false_positives=0,
                                                          human_review_as_intended=3, human_review_for_true_substantive=1,
                                                          human_review_for_true_cosmetic=0, correct_without_review=3)
    assert pick(s["surgery-knee-independent"]) == dict(edits=12, true_substantive=6, correct_substantive_detections=5, misses=0, false_positives=0,
                                                       human_review_as_intended=2, human_review_for_true_substantive=1,
                                                       human_review_for_true_cosmetic=0, correct_without_review=4)


def test_the_official_before_tuning_result_is_saved_and_matches():
    saved = json.loads((ROOT / "results" / "independent-initial-score.json").read_text())
    assert saved["meta"]["code"]["commit"].startswith("7c2cdba")             # the code that produced the run
    assert saved["meta"]["scored_saved_run"] == "results/independent-initial-run.json"
    t = saved["total"]
    assert (t["correct_substantive_detections"], t["misses"], t["false_positives"], t["human_review_for_true_substantive"]) == (5, 0, 0, 1)
    assert saved["meta"]["claude"]["used_in_this_run"] is False and "diagnostic only" in saved["meta"]["disclaimer"]


def make_folder(tmp_path, with_labels=True, kind="unlabeled"):
    folder = tmp_path / "new-examples"
    folder.mkdir()
    data = json.loads((SIMS / "beds-mattresses-independent.json").read_text())
    data["edits"] = [dict(e, kind=kind) for e in data["edits"]][:4]            # N1..N4
    (folder / "examples.json").write_text(json.dumps(data))
    if with_labels:
        shutil.copy(SIMS / "beds-mattresses-independent.labels.json", folder / "examples.labels.json")
    return folder


def test_eval_runs_on_any_new_folder_and_logs_how_to_reproduce_it(tmp_path):
    folder, out = make_folder(tmp_path), tmp_path / "out" / "result"
    evaluate.main([str(folder), "--out", str(out)])
    saved = json.loads(out.with_suffix(".json").read_text())
    assert out.with_suffix(".txt").exists()
    meta = saved["meta"]
    assert meta["code"]["commit"] and meta["classifier"]["rules"] and meta["claude"]["prompt_version"] == "v2"
    assert meta["claude"]["model"] and meta["real_policies_sha256"]["beds-mattresses"] and meta["run_at"]
    s = saved["sets"][0]
    assert s["name"] == "examples" and s["files"]["edits_sha256"] and s["files"]["labels_sha256"]
    assert [r["outcome"] for r in s["rows"]] == ["correct", "correct_detection", "correct", "correct_detection"]   # N1-N4 vs the key


def test_an_existing_result_is_never_overwritten_without_force(tmp_path):
    folder, out = make_folder(tmp_path), tmp_path / "result"
    evaluate.main([str(folder), "--out", str(out)])
    first = out.with_suffix(".json").read_text()
    with pytest.raises(SystemExit):
        evaluate.main([str(folder), "--out", str(out)])
    assert out.with_suffix(".json").read_text() == first
    evaluate.main([str(folder), "--out", str(out), "--force"])


def test_unlabeled_examples_are_classified_but_not_scored(tmp_path):
    folder = make_folder(tmp_path, with_labels=False)
    out = tmp_path / "result"
    evaluate.main([str(folder), "--out", str(out)])
    s = json.loads(out.with_suffix(".json").read_text())["sets"][0]
    assert s["summary"]["scored"] == 0 and all(r["outcome"] == "unscored" for r in s["rows"])
    assert all(r["predicted"] for r in s["rows"])             # still classified, so they can be scored later


def test_errors_show_source_evidence_and_a_likely_cause():
    saved = json.loads((ROOT / "results" / "independent-initial-score.json").read_text())
    n9 = next(r for r in saved["sets"][0]["rows"] if r["id"] == "N9")
    assert n9["outcome"] == "human_review" and n9["likely_cause"]
    c = n9["changes"][0]
    assert c["before_text"] and c["after_text"] and c["before_page"] == 2 and c["rule_id"] == "AMB-MODAL"


def fake_assessor(tmp_path):
    from types import SimpleNamespace
    from uhc_monitor.llm import ClaudeAssessor

    def answer(label, conf):
        return json.dumps({"suggested_label": label, "confidence": conf, "what_changed": "quotes", "interpretation": "reading",
                           "uncertainty": "unknown", "suggested_next_step": "check"})

    def create(**kw):
        text = answer("substantive", "medium") if "may be" in kw["messages"][0]["content"] else answer("cosmetic", "high")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", model="served-by-fake",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=50), _request_id="req_T")
    return ClaudeAssessor(client=SimpleNamespace(messages=SimpleNamespace(create=create)), log_path=tmp_path / "calls.jsonl")


def test_rules_plus_claude_reports_escalations_apart_from_rule_detections(tmp_path):
    a = fake_assessor(tmp_path)
    _, results = probe_edits(load_edits(SIMS / "beds-mattresses-independent.json"), assessor=a)
    labels = json.loads((SIMS / "beds-mattresses-independent.labels.json").read_text())["labels"]
    sets = [("independent", results, labels, {})]
    report = evaluate.evaluate_sets(sets)
    m = report["total"]
    assert (m["detected_by_rules"], m["claude_escalations"], m["claude_escalations_correct"]) == (5, 1, 1)   # N9 raised by Claude
    assert m["correct_substantive_detections"] == 6 and m["misses"] == 0 and m["false_positives"] == 0
    n9 = next(r for r in report["sets"][0]["rows"] if r["id"] == "N9")
    assert (n9["rules_predicted"], n9["predicted"], n9["via"]) == ("ambiguous", "substantive", "claude")
    # Claude never dismisses: cosmetic advice on genuinely ambiguous edits leaves them ambiguous
    assert all(r["predicted"] == "ambiguous" for r in report["sets"][0]["rows"] if r["id"] in ("N3", "N8", "N12"))
    meta = evaluate.metadata(assessor=a, sets=sets)
    assert meta["claude"]["used_in_this_run"] and meta["claude"]["prompt_version"] == "v2" and meta["claude"]["calls"] == 4
    text = evaluate.render(report, meta)
    for needle in ("rules + Claude", "Claude escalations", "detected by the RULES alone", "ESCALATED to substantive",
                   "self-reported confidence", "interpretation", "effort medium", "served by served-by-fake"):
        assert needle in text


def test_claude_mode_without_a_key_refuses_to_run(tmp_path):
    with pytest.raises(SystemExit) as e:
        evaluate.main([str(SIMS), "--claude", "--set", "beds-mattresses-independent", "--out", str(tmp_path / "r")])
    assert "needs an API key" in str(e.value)


def test_a_set_with_no_key_attributes_decisions_without_scoring_or_listing_errors(tmp_path):
    a = fake_assessor(tmp_path)
    data = json.loads((SIMS / "beds-mattresses-independent.json").read_text())
    _, results = probe_edits(data["edits"], assessor=a)                         # kind is 'unlabeled', no labels given
    sets = [("unlabeled", results, {}, {})]
    report = evaluate.evaluate_sets(sets)
    m = report["total"]
    assert m["scored"] == 0 and m["rules_flagged_substantive"] == 5 and m["claude_raised_substantive"] == 1   # N9 raised by Claude
    assert m["left_ambiguous"] == 3 and m["judged_cosmetic_or_quiet"] == 3
    assert not any("likely_cause" in r for r in report["sets"][0]["rows"])         # nothing to call an error without a key
    text = evaluate.render(report, evaluate.metadata(assessor=a, sets=sets))
    for needle in ("NO KEY YET", "flagged substantive by the RULES alone : 5", "raised to substantive by CLAUDE        : 1", "key says: no key yet"):
        assert needle in text
    assert "of 0 true-substantive" not in text and "Errors and avoidable review" not in text


def test_knee_answer_key_is_a_labels_file_apart_from_the_unlabeled_edit_file():
    labels = json.loads((SIMS / "surgery-knee-independent.labels.json").read_text())
    edits = json.loads((SIMS / "surgery-knee-independent.json").read_text())["edits"]
    assert sorted(labels["labels"], key=lambda k: int(k[1:])) == [e["id"] for e in edits] == [f"K{i}" for i in range(1, 13)]
    assert {e["kind"] for e in edits} == {"unlabeled"}                                  # the edit file never carries the answers
    from collections import Counter
    assert Counter(labels["labels"].values()) == {"substantive": 6, "cosmetic": 4, "ambiguous": 2}
    assert set(labels["reasons"]) == set(labels["labels"])


def test_the_knee_scored_results_are_saved_before_and_after_the_definitions_change():
    """The initial run (code a041b9d) predates the 'Definitions' rule change; the second is the current rules. Rules only."""
    before = json.loads((ROOT / "results" / "knee-independent-initial-scored.json").read_text())
    after = json.loads((ROOT / "results" / "knee-independent-after-definitions-scored.json").read_text())
    assert before["meta"]["code"]["commit"].startswith("a041b9d") and after["meta"]["code"]["commit"].startswith("761caec")
    pick = lambda t: (t["correct_substantive_detections"], t["true_substantive"], t["misses"], t["false_positives"],
                      t["human_review_as_intended"], t["human_review_for_true_substantive"], t["human_review_for_true_cosmetic"],
                      t["correct_without_review"])
    assert pick(before["total"]) == (4, 6, 0, 0, 2, 2, 0, 4)
    assert pick(after["total"]) == (5, 6, 0, 0, 2, 1, 0, 4)
    moved = [(b["id"], b["outcome"], a["outcome"]) for b, a in zip(before["sets"][0]["rows"], after["sets"][0]["rows"]) if b["outcome"] != a["outcome"]]
    assert moved == [("K6", "human_review", "correct_detection")]                       # the one edit the Definitions change moved
    assert after["meta"]["claude"]["used_in_this_run"] is False and before["meta"]["real_policies_sha256"].get("surgery-knee")
