"""Saved reviews: one row per finding key, persistent, and keyed so a new revision opens for review again."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from uhc_monitor.demo_data import seed_demo
from uhc_monitor.findings import finding_key, get_findings, latest_update
from uhc_monitor.llm import ClaudeAssessor
from uhc_monitor.store import Store


def test_saving_twice_keeps_one_row_and_the_latest_decision():
    s = Store()
    s.save_review("k1", "src", 1, 2, "reviewed", "first", "cosmetic", "ambiguous")
    s.save_review("k1", "src", 1, 2, "dismissed", "second", "unsure", "ambiguous")
    rows = s.db.execute("SELECT * FROM reviews").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "dismissed" and rows[0]["note"] == "second"
    assert rows[0]["tool_label"] == "ambiguous"
    assert rows[0]["created_at"] <= rows[0]["updated_at"]


def test_reviews_survive_a_restart():
    s = Store(); s.save_review("k1", "src", 1, 2, "reviewed", "kept", "substantive", "ambiguous"); s.db.close()
    again = Store()
    r = again.get_reviews(["k1", "missing"])
    assert set(r) == {"k1"} and r["k1"]["note"] == "kept" and r["k1"]["reviewer_label"] == "substantive"


@pytest.mark.parametrize("status, label", [("approved", None), ("reviewed", "maybe")])
def test_invalid_status_or_label_is_rejected(status, label):
    with pytest.raises(sqlite3.IntegrityError):
        Store().save_review("k", "src", 1, 2, status, "", label, None)


def test_finding_keys_are_stable_and_change_with_the_versions():
    c = {"change_type": "modified", "path": "A > B", "before_text": "x", "after_text": "y"}
    assert finding_key("s", 1, 2, c) == finding_key("s", 1, 2, dict(c))
    assert finding_key("s", 2, 3, c) != finding_key("s", 1, 2, c)          # a new revision is a new finding
    assert finding_key("s", 1, 2, c) != finding_key("s", 1, 2, {**c, "after_text": "z"})


def test_findings_have_unique_keys_and_are_built_once_without_asking_claude_again():
    s = Store(); seed_demo(s)
    u = latest_update(s, "beds-mattresses")
    calls = []

    def create(**kw):
        calls.append(1)
        answer = {"suggested_label": "abstain", "confidence": "low", "what_changed": "w", "interpretation": "i",
                  "uncertainty": "u", "suggested_next_step": "n"}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(answer))], stop_reason="end_turn",
                               model="fake", usage=SimpleNamespace(input_tokens=0, output_tokens=0), _request_id="r")
    assessor = ClaudeAssessor(client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    first = get_findings(s, "beds-mattresses", u["before_version_id"], u["version_id"], assessor)
    keys = [f["key"] for f in first["findings"]]
    assert len(keys) == len(set(keys)) == 10
    n = len(calls)
    assert n == first["ambiguous_count"] > 0          # only ambiguous changes were sent
    second = get_findings(s, "beds-mattresses", u["before_version_id"], u["version_id"], assessor)
    assert second == first and len(calls) == n          # cached: reopening the page does not call Claude again
