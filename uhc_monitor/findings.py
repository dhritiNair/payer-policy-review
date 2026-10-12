"""Build the findings for one update (before version -> after version) and cache them on disk.

Findings are cached because Streamlit reruns the page on every click, and each build may call Claude.
"""
import json
import re
from hashlib import sha256

from .classify import AMBIGUOUS, compare_with_notes
from .llm import ClaudeAssessor, apply_assessments

# What each rule found, in plain words, plus a suggested next step. This is interpretation, not source text.
RULE_GUIDANCE = {
    "COS-TOC": ("Only the table of contents changed; no policy text changed.",
                "No action needed beyond a quick glance."),
    "COS-FORMAT": ("The words are the same; only case, spacing, punctuation or hyphenation differs.",
                   "No action needed."),
    "SUB-CODE-LIST": ("A procedure code was added to or removed from the policy's code list. The policy says a code "
                      "listed there does not by itself mean the service is covered.",
                      "Check whether billing edits, the charge master or claim-scrubber rules reference this code, "
                      "and read the coverage criteria to see when it applies."),
    "SUB-PARAGRAPH": ("A criterion or paragraph was added or removed in a coverage section, which may change the "
                      "conditions for coverage.",
                      "Read it in context and check whether current authorization and documentation workflows "
                      "still match."),
    "SUB-KEYWORD": ("A word that decides whether or when coverage applies (such as all, any, not, only) changed.",
                    "Read both versions in full and check whether any workflow or payer-rule mapping depends on "
                    "the old wording."),
    "SUB-NUMBER": ("A number or code changed in a coverage section.",
                   "Confirm the new value against the policy and update anything that uses the old one."),
    "AMB-MODAL": ("A word that sets how strict a requirement is (may, should, must, will, can) changed. It could "
                  "be a real change in obligation or a harmless rewording.",
                  "Read both versions and decide whether the obligation changed."),
    "AMB-MOVED": ("The same text now sits in a different section. The wording is unchanged but its context is not.",
                  "Check whether the new location changes how the text applies."),
    "AMB-DEFAULT": ("The wording changed in a way the rules cannot judge.",
                    "Read both versions and decide whether the meaning changed."),
}
RULE_UNCERTAINTY = ("Rules match wording patterns, not meaning. They cannot say how this change affects coverage, "
                    "which patients it applies to, or when it takes effect.")


def policy_facts(extracted, simulated):
    """Facts the document itself supports: effective date and which plans it says it applies to."""
    applies = []
    for s in extracted["sections"]:
        if s["level"] == 2 and s["heading"].startswith("UnitedHealthcare"):
            for p in s["paragraphs"]:
                if re.search(r"\bapplies to\b", p["text"]):
                    applies.append({"plan": s["heading"], "text": p["text"], "page": p["page"]})
    date = extracted["effective_date"]
    return {
        "title": extracted["title"], "policy_number": extracted["policy_number"],
        "effective_date": "Unknown (simulated version has no real effective date)" if simulated else date,
        "applies_to": applies,   # empty means the document did not say: shown as Unknown
    }


def finding_key(source_id, before_id, after_id, change):
    """Stable id for a finding: same source, versions and change always give the same key."""
    basis = json.dumps([change["change_type"], change["path"], change["before_text"], change["after_text"]])
    return f"{source_id}:{before_id}->{after_id}:{sha256(basis.encode()).hexdigest()[:12]}"


def latest_update(store, source_id):
    """(check row) of the most recent 'updated' check, or None."""
    return store.db.execute(
        "SELECT * FROM checks WHERE source_id=? AND status='updated' ORDER BY id DESC LIMIT 1", (source_id,)).fetchone()


def all_updates(store, source_id):
    """Every 'updated' check for the source, newest first."""
    return store.db.execute(
        "SELECT * FROM checks WHERE source_id=? AND status='updated' ORDER BY id DESC", (source_id,)).fetchall()


def get_findings(store, source_id, before_id, after_id, assessor=None):
    """Findings for before->after. Built once (calling Claude on ambiguous changes if available), then cached."""
    cache = store.root / "findings" / f"{source_id}_{before_id}_{after_id}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    before, after = store.load_extracted(before_id), store.load_extracted(after_id)
    changes, notes = compare_with_notes(before, after)
    assessor = assessor or ClaudeAssessor()
    changes = apply_assessments(changes, assessor, after)
    seen = {}
    for c in changes:
        c["key"] = finding_key(source_id, before_id, after_id, c)
        seen[c["key"]] = seen.get(c["key"], 0) + 1
        if seen[c["key"]] > 1:   # two identical changes in one update must not share a key
            c["key"] += f"#{seen[c['key']]}"
        c["guidance"] = {"explanation": RULE_GUIDANCE[c["rule_id"]][0], "next_step": RULE_GUIDANCE[c["rule_id"]][1],
                         "uncertainty": RULE_UNCERTAINTY}
    result = {
        "source_id": source_id, "before_version_id": before_id, "after_version_id": after_id,
        "before_facts": policy_facts(before, bool(store.get_version(before_id)["simulated"])),
        "after_facts": policy_facts(after, bool(store.get_version(after_id)["simulated"])),
        "claude": {"model": assessor.config["model"], "prompt_version": assessor.config["prompt_version"],
                   "available": assessor.available,
                   "calls": sum(1 for c in changes if c["llm"] and c["llm"]["status"] != "skipped_no_key")},
        "notes": notes, "findings": changes,
        "ambiguous_count": sum(c["label"] == AMBIGUOUS for c in changes),
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result
