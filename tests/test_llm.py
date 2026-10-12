"""Claude fallback: advisory and escalate-only, only for ambiguous changes, fully logged, never leaks the key."""
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from uhc_monitor.classify import AMBIGUOUS, COSMETIC, SUBSTANTIVE, compare
from uhc_monitor.llm import ANSWER_SCHEMA, SYSTEM_PROMPT, ClaudeAssessor, apply_assessments, load_config
from uhc_monitor.simulate import make_simulated_older


def answer(label="cosmetic", confidence="medium"):
    return json.dumps({"suggested_label": label, "confidence": confidence, "what_changed": "w", "interpretation": "i",
                       "uncertainty": "u", "suggested_next_step": "n"})


def response(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, model="claude-sonnet-5-5",
                           usage=SimpleNamespace(input_tokens=11, output_tokens=7), _request_id="req_TEST")


def fake(handler):
    calls = []
    def create(**kw):
        calls.append(kw)
        return handler(kw)
    return SimpleNamespace(messages=SimpleNamespace(create=create)), calls


@pytest.fixture
def changes(real_doc):
    """One ambiguous change (wording outside the coverage sections), one substantive, one cosmetic."""
    edits = [
        {"id": "a", "kind": "t", "op": "replace_text", "section": "Description of Services", "find": "are often incorporated",
         "replace": "are generally incorporated", "description": "t"},
        {"id": "s", "kind": "t", "op": "replace_text", "section": "Safety Enclosure with Beds", "find": "when all of",
         "replace": "when any of", "description": "t"},
        {"id": "c", "kind": "t", "op": "replace_text", "section": "Safety Enclosure with Beds", "find": "Medical helmet",
         "replace": "Medical Helmet", "description": "t"}]
    return compare(make_simulated_older(real_doc, edits), real_doc)


def test_only_ambiguous_changes_are_sent_to_claude(changes, real_doc):
    client, calls = fake(lambda kw: response(answer("cosmetic")))
    out = apply_assessments(changes, ClaudeAssessor(client=client), real_doc)
    assert len(calls) == 1
    assert {c["label"]: c["llm"] is not None for c in out} == {AMBIGUOUS: True, SUBSTANTIVE: False, COSMETIC: False}


def test_claude_can_escalate_to_substantive_but_never_dismiss(changes, real_doc):
    def final(label, confidence="medium"):
        client, _ = fake(lambda kw: response(answer(label, confidence)))
        out = apply_assessments(changes, ClaudeAssessor(client=client), real_doc)
        return next(c for c in out if c["label"] == AMBIGUOUS)
    esc = final("substantive")
    assert esc["final_label"] == SUBSTANTIVE and esc["final_source"].startswith("claude") and esc["label"] == AMBIGUOUS
    for label in ("cosmetic", "abstain"):
        for confidence in ("low", "high"):
            c = final(label, confidence)
            assert c["final_label"] == AMBIGUOUS and c["final_source"] == "rules"     # advice shown, never auto-dismissed


@pytest.mark.parametrize("handler, status", [
    (lambda kw: response("", stop="refusal"), "refused"),
    (lambda kw: response("this is not json"), "malformed"),
    (lambda kw: response(json.dumps({"suggested_label": "maybe"})), "malformed"),
    (lambda kw: (_ for _ in ()).throw(anthropic.APIConnectionError(request=httpx2.Request("POST", "http://x"))), "error"),
])
def test_refusal_error_or_bad_answer_leaves_the_change_ambiguous(changes, real_doc, handler, status):
    client, _ = fake(handler)
    c = next(c for c in apply_assessments(changes, ClaudeAssessor(client=client), real_doc) if c["label"] == AMBIGUOUS)
    assert c["llm"]["status"] == status and c["final_label"] == AMBIGUOUS


def test_without_a_key_it_runs_rules_only_and_says_so(changes, real_doc):
    a = ClaudeAssessor(client=None)           # conftest sets an empty ANTHROPIC_API_KEY
    assert not a.available
    c = next(c for c in apply_assessments(changes, a, real_doc) if c["label"] == AMBIGUOUS)
    assert c["llm"]["status"] == "skipped_no_key" and c["final_label"] == AMBIGUOUS


def test_every_call_is_logged_with_model_config_and_prompt_version_and_never_the_key(changes, real_doc, monkeypatch, tmp_path):
    secret = "sk-ant-TEST-SECRET-1234567890"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    log = tmp_path / "calls.jsonl"
    client, _ = fake(lambda kw: response(answer("cosmetic", "high")))
    apply_assessments(changes, ClaudeAssessor(client=client, log_path=log), real_doc)
    text = log.read_text()
    rec = json.loads(text.splitlines()[0])
    cfg = load_config()
    assert (rec["model"], rec["effort"], rec["prompt_version"]) == (cfg["model"], cfg["effort"], cfg["prompt_version"])
    assert len(rec["prompt_sha256"]) == 64 and rec["request_id"] == "req_TEST" and rec["usage"]["output_tokens"] == 7
    assert secret not in text


def test_request_shape_and_prompt(changes, real_doc):
    client, calls = fake(lambda kw: response(answer()))
    apply_assessments(changes, ClaudeAssessor(client=client), real_doc)
    kw = calls[0]
    assert kw["model"] == load_config()["model"] and kw["output_config"]["format"]["schema"] == ANSWER_SCHEMA
    assert "temperature" not in kw and "thinking" not in kw and "tool_choice" not in kw
    assert "<before>" in kw["messages"][0]["content"] and "generally" in kw["messages"][0]["content"]
    for level in ("high:", "medium:", "low:"):                       # prompt v2 defines the confidence levels
        assert level in SYSTEM_PROMPT
    assert load_config()["prompt_version"] == "v2"
