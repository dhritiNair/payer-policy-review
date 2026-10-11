"""Run an update check for one source and decide its status."""
from dataclasses import dataclass

from .extract import extract_policy
from .fetch import fetch_source
from .sources import FAILED, INITIALIZED, UNCHANGED, UPDATED, Source
from .store import Store, content_hash, history_text


@dataclass
class CheckOutcome:
    source_id: str
    status: str
    reason: str | None
    version_id: int | None            # version this check looked at (None when failed)
    baseline_version_id: int | None   # baseline after this check (the old one after a failure)
    before_version_id: int | None = None   # for 'updated': the version we compared against


def run_check(store: Store, source: Source, fetch=fetch_source, extract=extract_policy):
    state = store.get_state(source.id)
    baseline_id = state["baseline_version_id"] if state else None

    def failed(reason):
        # a failure never moves the baseline and is never reported as unchanged
        store.record_check(source.id, FAILED, reason=reason)
        return CheckOutcome(source.id, FAILED, reason, None, baseline_id)

    result = fetch(source)
    if not result.ok:
        return failed(result.reason)
    try:
        extracted = extract(result.data)
    except Exception as e:
        return failed(f"could not read PDF text: {e}")
    if not any(s["paragraphs"] or s.get("code_table") for s in extracted["sections"]):
        return failed("PDF has no extractable text")

    if baseline_id is None:
        version_id = store.save_version(source.id, extracted, result.data, result.sha256)
        store.record_check(source.id, INITIALIZED, version_id=version_id, new_baseline_id=version_id)
        return CheckOutcome(source.id, INITIALIZED, None, version_id, version_id)

    baseline = store.get_version(baseline_id)
    note = "compared against a SIMULATED baseline" if baseline["simulated"] else None
    same = (baseline["policy_number"] == extracted["policy_number"]
            and baseline["effective_date"] == extracted["effective_date"]
            and baseline["content_hash"] == content_hash(extracted))
    if same:
        # the History table is outside the hash; if it is the only difference, say so on the record
        if history_text(store.load_extracted(baseline_id)) != history_text(extracted):
            note = "; ".join(filter(None, [note, "history table differs (excluded from comparison)"]))
        store.record_check(source.id, UNCHANGED, reason=note, version_id=baseline_id, new_baseline_id=baseline_id)
        return CheckOutcome(source.id, UNCHANGED, note, baseline_id, baseline_id)

    version_id = store.save_version(source.id, extracted, result.data, result.sha256)
    store.record_check(source.id, UPDATED, reason=note, version_id=version_id, new_baseline_id=version_id,
                       before_version_id=baseline_id)
    return CheckOutcome(source.id, UPDATED, note, version_id, version_id, before_version_id=baseline_id)
