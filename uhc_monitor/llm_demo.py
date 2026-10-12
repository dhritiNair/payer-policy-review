"""Show the Claude fallback: rules-only without a key, scripted answers with a fake client, live if a key is set."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2

from .classify import AMBIGUOUS, compare
from .extract import extract_policy
from .llm import ClaudeAssessor, KEY_ENV_VAR, apply_assessments, load_config
from .simulate import load_edits, make_simulated_older

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "demo-llm-calls.jsonl"


def scripted_response(content):
    """A canned Claude-shaped response, chosen by what the request text contains. Not a real model."""
    def reply(text, stop="end_turn"):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, model="fake",
                               usage=SimpleNamespace(input_tokens=0, output_tokens=0), _request_id="req_FAKE")
    answer = lambda label, conf: json.dumps({
        "suggested_label": label, "confidence": conf, "what_changed": "(scripted) quotes the two texts",
        "interpretation": "(scripted) possible reading", "uncertainty": "(scripted) not knowable from this text",
        "suggested_next_step": "(scripted) check the policy owner's notice"})
    if "Many benefit plans" in content:
        return reply(answer("cosmetic", "medium"))
    if "Some manufacturers" in content:
        return reply(answer("abstain", "low"))
    if "exclusions apply" in content:
        return reply(answer("substantive", "medium"))
    if "individuals who" in content:
        return reply("this is not json")
    if "Accessed December" in content:
        return reply("", stop="refusal")
    raise anthropic.APIConnectionError(request=httpx2.Request("POST", "http://fake"))


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls += 1
        return scripted_response(kw["messages"][0]["content"])


def show(title, changes):
    print(f"\n=== {title}")
    for c in changes:
        if c["label"] != AMBIGUOUS:
            continue
        llm = c["llm"]
        advice = ""
        if llm["status"] == "ok":
            a = llm["answer"]
            advice = f" Claude suggests {a['suggested_label']} ({a['confidence']})"
        print(f"  {c['path'][:40]:40} rules=ambiguous -> final={c['final_label']:11} [{c['final_source']}] "
              f"llm={llm['status']}{advice}" + (f" ({llm['detail']})" if llm.get("detail") else ""))


def main():
    LOG.unlink(missing_ok=True)
    policy_real = extract_policy(ROOT / "sources" / "original" / "beds-mattresses.pdf")
    by_id = {e["id"]: e for f in ("beds-mattresses.json", "beds-mattresses-blind.json")
             for e in load_edits(ROOT / "config" / "simulations" / f)}
    demo_only = {"id": "D1", "kind": "demo", "op": "replace_text", "section": "Description of Services",
                 "find": "Many manufacturers offer beds", "replace": "Some manufacturers offer beds",
                 "description": "inline demo edit, not saved to any edit file"}
    # edits that touch the same paragraph merge into one change, so B3 runs on its own
    runs = [[by_id[i] for i in ("A1", "A2", "B5", "B6", "C1")] + [demo_only], [by_id["B3"]]]
    changes = [c for edits in runs for c in compare(make_simulated_older(policy_real, edits), policy_real)]
    n_amb = sum(c["label"] == AMBIGUOUS for c in changes)
    print(f"{len(changes)} changes from 7 edits (A1 A2 B3 B5 B6 D1 ambiguous-ish; C1 cosmetic); "
          f"{n_amb} are ambiguous and eligible for Claude, the rest are never sent.\nconfig: {load_config()}")

    no_key = ClaudeAssessor(client=None, log_path=LOG)
    no_key.client = None
    show(f"1. rules only ({KEY_ENV_VAR} {'IS' if os.environ.get(KEY_ENV_VAR) else 'is NOT'} set in this shell; client forced off)",
         apply_assessments(changes, no_key, policy_real))

    fake = FakeClient()
    show("2. FAKE CLIENT with scripted answers (tests the plumbing, not Claude)",
         apply_assessments(changes, ClaudeAssessor(client=fake, log_path=LOG), policy_real))
    print(f"  calls made to the fake client: {fake.calls} (ambiguous changes: {n_amb})")

    if os.environ.get(KEY_ENV_VAR):
        show("3. LIVE Claude", apply_assessments(changes, ClaudeAssessor(log_path=LOG), policy_real))
    else:
        print(f"\n=== 3. LIVE Claude: skipped, {KEY_ENV_VAR} is not set")

    print(f"\nlog: {LOG.relative_to(ROOT)} ({sum(1 for _ in open(LOG))} records). Fields of the last record:")
    last = json.loads(open(LOG).read().splitlines()[-1])
    print("  " + ", ".join(sorted(last)))
    print("  key present in log:", os.environ.get(KEY_ENV_VAR, "\0") in open(LOG).read())


if __name__ == "__main__":
    main()
