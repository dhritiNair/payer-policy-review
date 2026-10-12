"""Reviewer label flow check (script, not yet pytest): a reviewer's Substantive/Cosmetic label takes effect on the page.
Run: PYTHONPATH=. .venv/bin/python tests/flow_reviewer_label.py"""
import os, tempfile
from pathlib import Path

os.environ["ANTHROPIC_API_KEY"] = ""
root = tempfile.mkdtemp(); os.environ["UHC_STORE_ROOT"] = root
from streamlit.testing.v1 import AppTest
from tests.seed_rich import seed
from uhc_monitor.store import Store

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")


def check(name, ok): print(("PASS " if ok else "FAIL ") + name); assert ok, name
def open_app():
    at = AppTest.from_file(APP, default_timeout=60).run(); assert not at.exception, at.exception; return at
def metrics(at): return {m.label: m.value for m in at.metric}
def row(at, key): return next(b for b in at.button if b.key in (f"row-{key}", f"rowsel-{key}"))
def rows(at, word): return [b for b in at.button if b.key and b.key.startswith(("row-", "rowsel-")) and word in b.label]
def review(at, key, label, status="Mark reviewed", note="reviewer note"):
    if row(at, key).key.startswith("row-"):
        row(at, key).click().run()
    at.text_area(key=f"note-{key}").input(note)
    at.selectbox(key=f"label-{key}").select(label)
    [b for b in at.button if b.label == status][0].click().run(); assert not at.exception, at.exception
def db_row(key):
    s = Store(root); r = s.db.execute("SELECT * FROM reviews WHERE finding_key=?", (key,)).fetchone(); s.db.close(); return r
def cos_expander(at): return next(e for e in at.expander if e.label.startswith("Cosmetic changes"))

keys = seed(root)
at = open_app(); m = metrics(at)
check("start: 5 substantive, 3 ambiguous, 4 cosmetic, 0 resolved", (m["Substantive"], m["Ambiguous — Needs human review"], m["Cosmetic"], m["Resolved by reviewer"]) == ("5", "3", "4", "0"))
sub_prios = [b.label.split("**")[1] for b in rows(at, "SUBSTANTIVE")]

# 1. Ambiguous -> Cosmetic
review(at, keys["A2"], "Cosmetic")
m = metrics(at)
check("A2 (Ambiguous) set to Cosmetic: moves to the Cosmetic list", (m["Substantive"], m["Ambiguous — Needs human review"], m["Cosmetic"]) == ("5", "2", "5"))
check("'Resolved by reviewer' count is 1", m["Resolved by reviewer"] == "1")
r = row(at, keys["A2"]).label
check("row reads 'AMBIGUOUS → COSMETIC (reviewer)' and is tagged Reviewer-resolved", "AMBIGUOUS" in r and "→" in r and "COSMETIC" in r and "(reviewer)" in r and "Reviewer-resolved" in r)
inside = [b.key for b in cos_expander(at).button]
check("the row is inside the Cosmetic list (expander), and not in the Ambiguous column",
      row(at, keys["A2"]).key in inside and len(inside) == 1)
check("the Cosmetic list label counts it", cos_expander(at).label.startswith("Cosmetic changes (5)") and "1 reviewer-resolved" in cos_expander(at).label)
check("tool label stays stored", db_row(keys["A2"])["tool_label"] == "ambiguous" and db_row(keys["A2"])["reviewer_label"] == "cosmetic")
md = "\n".join(x.value for x in at.markdown)
check("detail shows the Reviewer-resolved line and the tool's classification",
      "Reviewer-resolved: :orange[**AMBIGUOUS**] → :gray[**COSMETIC**] (reviewer)" in md and "Matched rule **AMB-DEFAULT**" in md)

# 2. Ambiguous -> Substantive; existing priority numbers do not shift
review(at, keys["B5"], "Substantive")
m = metrics(at)
check("B5 set to Substantive: moves to the Substantive column", (m["Substantive"], m["Ambiguous — Needs human review"], m["Cosmetic"], m["Resolved by reviewer"]) == ("6", "1", "5", "2"))
subs = rows(at, "SUBSTANTIVE")
check("existing Substantive numbers unchanged; the moved one is numbered after them (P6)",
      [b.label.split("**")[1] for b in subs[:5]] == sub_prios and "P6" in subs[5].label and "(reviewer)" in subs[5].label and "Reviewer-resolved" in subs[5].label)
check("moved row is decided, so it is listed after the open ones", all("OPEN" in b.label for b in subs[:5]) and "REVIEWED" in subs[5].label)

# 3. 'Still unsure' keeps it Ambiguous (D1 was pre-dismissed with no label)
review(at, keys["D1"], "Still unsure", status="Dismiss")
m = metrics(at)
check("Still unsure keeps the finding Ambiguous (no move)", m["Ambiguous — Needs human review"] == "1" and m["Resolved by reviewer"] == "2")
unsure = row(at, keys["D1"]).label
check("that row still says Needs human review and DISMISSED, with no reviewer tag",
      "Needs human review" in unsure and "DISMISSED" in unsure and "→" not in unsure.split("\n")[0] and "(reviewer)" not in unsure)

# 4. a Claude-raised finding the reviewer overrules to Cosmetic leaves the Substantive column
review(at, keys["A1"], "Cosmetic")
m = metrics(at)
check("Claude-raised A1 set to Cosmetic: leaves Substantive, joins Cosmetic", (m["Substantive"], m["Cosmetic"], m["Resolved by reviewer"]) == ("5", "6", "3"))
check("its tool label (raised by Claude) is still shown in the detail", any("escalated by Claude" in x.value for x in at.markdown))

# 5. persistence across a restart
at = open_app(); m = metrics(at)
check("after restart: same effective counts", (m["Substantive"], m["Ambiguous — Needs human review"], m["Cosmetic"], m["Resolved by reviewer"]) == ("5", "1", "6", "3"))
check("after restart: Open/Reviewed/Dismissed unaffected by moves", (m["Open"], m["Reviewed"], m["Dismissed"]) == ("4", "3", "1"))

# 6. reversible: set the label back to Still unsure
review(at, keys["A2"], "Still unsure")
m = metrics(at)
check("A2 back to Still unsure: returns to the Ambiguous column", (m["Ambiguous — Needs human review"], m["Cosmetic"], m["Resolved by reviewer"]) == ("2", "5", "2"))
back = row(at, keys["A2"]).label
check("returned row is Ambiguous again with 'Needs human review' and its original priority (P2)", "Needs human review" in back and "P2" in back and "(reviewer)" not in back)
check("reviewer label cleared to 'unsure' in the database, tool label untouched", db_row(keys["A2"])["reviewer_label"] == "unsure" and db_row(keys["A2"])["tool_label"] == "ambiguous")
review(at, keys["B5"], "Still unsure")
m = metrics(at)
check("B5 reversed too: leaves Substantive (4 by rules; A1 is still reviewer-resolved to Cosmetic), Ambiguous back to 3",
      (m["Substantive"], m["Ambiguous — Needs human review"], m["Resolved by reviewer"]) == ("4", "3", "1"))
print("\nall reviewer-label checks passed")
