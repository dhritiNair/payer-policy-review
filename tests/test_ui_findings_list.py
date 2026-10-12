"""UI flow: two columns, priority order, collapsed rows, one detail at a time."""
import copy
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest
from tests.seed_rich import seed
import pytest
from tests.helpers import check



@pytest.mark.ui
def test_findings_list_order_columns_and_one_detail_at_a_time(app_env):
    root = app_env
    ROOT = Path(__file__).resolve().parent.parent




    keys = seed(root)

    # --- the page
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run(); assert not at.exception, at.exception
    rows = [b for b in at.button if b.key and b.key.startswith(("row-", "rowsel-"))]
    subst = [b for b in rows if "SUBSTANTIVE" in b.label]
    ambig = [b for b in rows if "AMBIGUOUS" in b.label]
    print("substantive rows:", [b.label.split("\n")[0][:70] for b in subst])
    print("ambiguous rows:  ", [b.label.split("\n")[0][:70] + " … " + b.label.split("\n")[-1][-12:] for b in ambig])
    check("5 substantive and 3 ambiguous rows", (len(subst), len(ambig)) == (5, 3))
    check("Substantive: Claude-escalated finding is first (P1)", "P1" in subst[0].label and "raised by Claude" in subst[0].label)
    check("Substantive priorities run P1..P5", [b.label.split("**")[1] for b in subst] == [f"P{i}" for i in range(1, 6)])
    check("Ambiguous: abstained (P2) before high-confidence cosmetic (P3)", "P2" in ambig[0].label and "P3" in ambig[1].label)
    check("Ambiguous: dismissed finding (P1, Claude low confidence) sinks to the bottom, keeps its number",
          "P1" in ambig[2].label and "DISMISSED" in ambig[2].label)
    check("every Ambiguous row carries 'Needs human review'", all("Needs human review" in b.label for b in ambig))
    r = subst[1].label
    check("row shows priority, label, section, page, changed words, status",
          all(x in r for x in ("P2", "SUBSTANTIVE", "Coverage Rationale > Safety Enclosure with Beds", "p.1", "any → all", "OPEN")))
    check("open findings listed before decided ones", [b for b in ambig if "OPEN" in b.label] == ambig[:2])
    check("Cosmetic stays collapsed", any(e.label.startswith("Cosmetic changes (4)") for e in at.expander))
    check("no detail until a row is clicked", not [b for b in at.button if b.label == "Mark reviewed"])
    subst[0].click().run()
    check("clicking a row opens its detail with the review form", len([b for b in at.button if b.label == "Mark reviewed"]) == 1
          and any(s.value == "Finding detail" for s in at.subheader))
    ambig = [b for b in at.button if b.key and "row" in b.key and "AMBIGUOUS" in b.label]
    ambig[0].click().run()
    check("opening another row replaces the detail (one at a time)", len([b for b in at.button if b.label == "Mark reviewed"]) == 1
          and any("▼" in b.label and "AMBIGUOUS" in b.label for b in at.button))
    [b for b in at.button if b.key and b.key.startswith("rowsel-")][0].click().run()
    check("clicking the open row again closes it", not [b for b in at.button if b.label == "Mark reviewed"])
    print("\nall findings-list checks passed")
