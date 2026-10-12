"""The user-facing title is 'Payer Policy Review'; the package name is internal and stays uhc_monitor."""
from streamlit.testing.v1 import AppTest

from tests.conftest import ROOT

TITLE = "Payer Policy Review"


def test_page_title_top_label_and_readme_heading():
    assert f'page_title="{TITLE}"' in (ROOT / "app.py").read_text()                  # the browser tab / page config title
    assert (ROOT / "README.md").read_text().splitlines()[0] == f"# {TITLE}"
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert TITLE in [c.value for c in at.caption]                                      # the label above the policy heading
    assert not any("UHC policy change monitor" in c.value or "UHC Policy Monitor" in c.value for c in at.caption)


def test_the_package_was_not_renamed():
    assert (ROOT / "uhc_monitor" / "__init__.py").exists() and not (ROOT / "payer_policy_review").exists()
    assert "uhc_monitor" in (ROOT / "README.md").read_text()                           # commands still say python -m uhc_monitor...
