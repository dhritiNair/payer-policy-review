"""UI: policy header, policy-named banner, policy selector, summary rows."""
import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import ROOT
from tests.helpers import check
from uhc_monitor import testserver
from uhc_monitor.check import run_check
from uhc_monitor.sources import Source
from uhc_monitor.store import Store

APP = str(ROOT / "app.py")


def page(at):
    return "\n".join(m.value for m in at.markdown), [t.value for t in at.title], [e.value for e in at.error], [w.value for w in at.warning]


@pytest.mark.ui
def test_header_banner_selector_and_summary(app_env):
    at = AppTest.from_file(APP, default_timeout=60).run(); assert not at.exception
    check("no data: heading falls back to the source name", page(at)[1] == ["Beds and Mattresses"])
    at.button[0].click().run(); assert not at.exception
    md, titles, errs, warns = page(at)
    check("large heading is the policy title", titles == ["Beds and Mattresses"])
    check("product line, number, effective date, versions", all(x in md for x in (
        "**UnitedHealthcare Commercial and Individual Exchange**", "**Policy number**  \nMP.028.14",
        "**Effective date (current)**  \n2026-01-01", "**Version compared**  \nv1 SIMULATED → v2 real")))
    check("last checked shown", "**Last checked**  \n20" in md)
    check("banner names the policy", any(w.startswith("**Beds and Mattresses: updated**") for w in warns))
    check("old caption gone", "UnitedHealthcare · Beds and Mattresses" not in [c.value for c in at.caption])
    check("two labelled rows of four numbers", [m.label for m in at.metric] == [
        "Substantive", "Ambiguous — Needs human review", "Cosmetic", "Resolved by reviewer",
        "Open", "Reviewed", "Dismissed", "Total findings"] and "**By type**" in md and "**Review progress**" in md)

    server, base = testserver.start()                  # a failed check: banner names the policy, versions stay
    store = Store(); run_check(store, Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", f"{base}/html-error")); store.db.close()
    server.shutdown()
    md, titles, errs, warns = page(AppTest.from_file(APP, default_timeout=60).run())
    check("failed banner names the policy and says not unchanged", any(e.startswith("**Beds and Mattresses: failed**") and "not 'unchanged'" in e for e in errs))
    check("header still shows the last good versions", "v1 SIMULATED → v2 real" in md)

    at = AppTest.from_file(APP, default_timeout=60).run(); assert not at.exception   # the registry now lists two policies
    sel = [s for s in at.selectbox if s.label == "Policy"]
    check("selector shown with both policies", len(sel) == 1 and sel[0].options == ["Beds and Mattresses", "Surgery of the Knee"])
    sel[0].select_index(1).run()
    check("switching policy changes the heading", page(at)[1] == ["Surgery of the Knee"])
