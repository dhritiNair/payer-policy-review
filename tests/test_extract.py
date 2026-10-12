"""Text and structure extraction from the source PDF."""
import re

import pymupdf

from tests.conftest import ROOT


def test_policy_identity_is_read_from_the_text(real_doc):
    assert real_doc["policy_number"] == "MP.028.14"
    assert real_doc["effective_date"] == "2026-01-01"
    assert real_doc["title"] == "Beds and Mattresses" and real_doc["page_count"] == 4


def test_code_table_matches_the_codes_listed_in_the_pdf_metadata(real_doc):
    meta = pymupdf.open(ROOT / "sources/original/beds-mattresses.pdf").metadata["subject"]
    expected = set(re.findall(r"[A-Z]\d{4}", meta))
    rows = [r for s in real_doc["sections"] for r in s.get("code_table", [])]
    assert {r["code"] for r in rows} == expected and len(rows) == 33
    assert all(r["description"] and r["page"] in (2, 3) for r in rows)


def test_history_table_is_kept_as_raw_text_and_excluded(real_doc):
    hist = [s for s in real_doc["sections"] if s["heading"].startswith("Policy History")]
    assert len(hist) == 1 and hist[0]["excluded_from_diff"] and hist[0]["paragraphs"]


def test_arrow_markers_are_stripped_and_page_break_paragraphs_joined(real_doc):
    paras = [p for s in real_doc["sections"] for p in s["paragraphs"]]
    assert not any("" in p["text"] or "→" in p["text"] for p in paras)
    nawaz = next(p for p in paras if p["text"].startswith("Nawaz et al. (2007)"))
    assert (nawaz["page"], nawaz["page_end"]) == (3, 4)
