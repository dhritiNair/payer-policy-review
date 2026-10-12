"""Fetch validation, statuses and version identity: a failed fetch is never 'unchanged' and never loses the last good copy."""
import copy

import pytest

from uhc_monitor import testserver
from uhc_monitor.check import run_check
from uhc_monitor.extract import extract_policy
from uhc_monitor.fetch import fetch_source, validate_pdf
from uhc_monitor.simulate import load_edits, make_simulated_older
from uhc_monitor.sources import FAILED, INITIALIZED, UNCHANGED, UPDATED, Source
from uhc_monitor.store import Store
from tests.conftest import ROOT


@pytest.fixture
def server():
    srv, base = testserver.start()
    yield base
    srv.shutdown()


def src(base, path):
    return Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", f"{base}{path}")


@pytest.mark.parametrize("path, reason", [
    ("/html-error", "not a PDF"),           # HTTP 200 but an HTML error page
    ("/html-as-pdf", "not a PDF"),          # HTML that claims to be application/pdf
    ("/empty", "empty"),                    # HTTP 200, empty body
    ("/truncated.pdf", "truncated"),
    ("/missing", "HTTP 404"),
    ("/server-error", "HTTP 500"),
])
def test_bad_responses_are_failed_with_a_reason(server, path, reason):
    r = fetch_source(src(server, path))
    assert not r.ok and r.status == FAILED and reason in r.reason and r.data is None


def test_unreachable_server_and_missing_url_are_failed():
    r = fetch_source(Source("x", "x", "UnitedHealthcare", "http://127.0.0.1:1/ok.pdf"))
    assert r.status == FAILED and "network error" in r.reason
    r = fetch_source(Source("x", "x", "UnitedHealthcare", None))
    assert r.status == FAILED and r.reason == "no URL configured"


def test_a_real_pdf_is_fetched_with_hash_and_page_count(server):
    r = fetch_source(src(server, "/ok.pdf"))
    assert r.ok and r.page_count == 4 and len(r.sha256) == 64


def test_validate_pdf(real_pdf_bytes):
    assert validate_pdf(real_pdf_bytes) == (4, None)
    assert validate_pdf(b"")[1] == "empty response"
    assert "not a PDF" in validate_pdf(b"<html>error</html>")[1]
    assert "truncated" in validate_pdf(real_pdf_bytes[:2000])[1]


def test_first_check_that_fails_has_no_baseline(server, tmp_path):
    store = Store()
    o = run_check(store, src(server, "/html-error"))
    assert o.status == FAILED and o.baseline_version_id is None
    assert store.get_state("beds-mattresses")["baseline_version_id"] is None


def test_failure_keeps_the_last_good_version_and_is_never_unchanged(server):
    store = Store()
    assert run_check(store, src(server, "/ok.pdf")).status == INITIALIZED
    assert run_check(store, src(server, "/ok.pdf")).status == UNCHANGED
    good = store.get_state("beds-mattresses")["baseline_version_id"]
    pdf = next((store.root / "snapshots" / "beds-mattresses").glob("*.pdf"))
    saved = pdf.read_bytes()
    for path in ("/html-error", "/empty", "/missing", "/server-error", "/truncated.pdf"):
        o = run_check(store, src(server, path))
        assert o.status == FAILED and o.baseline_version_id == good
        state = store.get_state("beds-mattresses")
        assert state["last_status"] == FAILED and state["baseline_version_id"] == good
    assert pdf.read_bytes() == saved
    assert run_check(store, src(server, "/ok.pdf")).status == UNCHANGED   # recovers, still the same version


def seed_simulated_baseline(store, real):
    older = make_simulated_older(real, load_edits(ROOT / "config/simulations/beds-mattresses.json"))
    vid = store.save_version("beds-mattresses", older, simulated=True)
    store.record_check("beds-mattresses", INITIALIZED, reason="SIMULATED baseline seeded", version_id=vid, new_baseline_id=vid)
    return vid


def test_updated_against_a_simulated_baseline_then_unchanged_with_no_second_alert(server, real_doc):
    store = Store()
    old = seed_simulated_baseline(store, real_doc)
    o = run_check(store, src(server, "/ok.pdf"))
    assert o.status == UPDATED and o.before_version_id == old and "SIMULATED" in o.reason
    assert store.get_version(old)["simulated"] == 1 and store.get_version(o.version_id)["simulated"] == 0
    for _ in range(3):
        assert run_check(store, src(server, "/ok.pdf")).status == UNCHANGED
    assert store.db.execute("SELECT COUNT(*) FROM checks WHERE status='updated'").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 2


def test_history_table_difference_alone_is_unchanged_with_a_note(server):
    store = Store()
    run_check(store, src(server, "/ok.pdf"))

    def edit_history(data):
        d = copy.deepcopy(extract_policy(data))
        next(s for s in d["sections"] if s.get("excluded_from_diff"))["paragraphs"][0]["text"] += " (edited)"
        return d
    o = run_check(store, src(server, "/ok.pdf"), extract=edit_history)
    assert o.status == UNCHANGED and "history table differs" in o.reason
    assert store.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 1


def test_changed_text_is_a_new_version_identified_by_number_date_and_content_hash(server, real_doc):
    store = Store()
    run_check(store, src(server, "/ok.pdf"))

    def change_text(data):
        d = copy.deepcopy(extract_policy(data))
        sec = next(s for s in d["sections"] if s["heading"] == "Benefit Considerations")
        sec["paragraphs"][0]["text"] = sec["paragraphs"][0]["text"].replace("Most", "Several")
        return d
    o = run_check(store, src(server, "/ok.pdf"), extract=change_text)
    assert o.status == UPDATED
    a, b = (store.get_version(i) for i in (o.before_version_id, o.version_id))
    assert (a["policy_number"], a["effective_date"]) == (b["policy_number"], b["effective_date"])
    assert a["content_hash"] != b["content_hash"]
    again = store.save_version("beds-mattresses", change_text(testserver.PDF_PATH.read_bytes()))
    assert again == o.version_id          # same identity, same version: no duplicate


def test_a_pdf_with_no_text_is_failed(server):
    store = Store()
    blank = lambda data: {"sections": [], "policy_number": "Unknown", "effective_date": "Unknown", "title": None}
    assert run_check(store, src(server, "/ok.pdf"), extract=blank).status == FAILED
