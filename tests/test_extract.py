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


BEDS_CONTENT_HASH = "9e72894d9eb4d59ad731d1d1ad3894d287a9e4eee37ae3104ebe8d3fbcc3788c"


def test_beds_and_mattresses_extraction_is_unchanged_by_the_knee_fixes(real_doc):
    """The content hash is the version identity: if this moves, every stored Beds version would look 'updated'."""
    from uhc_monitor.store import content_hash
    assert content_hash(real_doc) == BEDS_CONTENT_HASH
    assert sum(len(s["paragraphs"]) for s in real_doc["sections"]) == 53
    assert sum(len(s.get("code_table", [])) for s in real_doc["sections"]) == 33


def para(text, page=1, end=None):
    return {"page": page, "page_end": end or page, "text": text}


def test_same_page_continuation_is_joined_only_when_it_is_really_one_sentence():
    from uhc_monitor.extract import _join_split_paragraphs as join
    assert [p["text"] for p in join([para("studies are needed before"), para("scaffolds can be used.")])] == \
        ["studies are needed before scaffolds can be used."]
    # a previous paragraph that ended a sentence, or a lowercase list marker, is a new paragraph
    assert len(join([para("This ends here."), para("and this starts lowercase")])) == 2
    assert len(join([para("• Medical helmet"), para("o Side rails")])) == 2
    assert len(join([para("Mattress placed on the floor"), para("o Medical helmet")])) == 2
    # across a page break a lowercase start is still always a continuation (unchanged behaviour)
    assert len(join([para("carries on", page=1), para("onto the next page", page=2)])) == 1


def test_cpt_rows_are_only_recognised_inside_applicable_codes():
    from uhc_monitor.extract import _split_code_table as split
    rows = [para("CPT Code Description"), para("27412 Autologous chondrocyte implantation, knee"),
            para("0737T Xenograft implantation"), para("E0193 Powered air flotation bed"), para("Not a row")]
    codes = {"heading": "Applicable Codes", "paragraphs": [dict(r) for r in rows]}
    split(codes)
    assert [r["code"] for r in codes["code_table"]] == ["27412", "0737T", "E0193"] and [p["text"] for p in codes["paragraphs"]] == ["Not a row"]
    other = {"heading": "Clinical Evidence", "paragraphs": [dict(r) for r in rows]}
    split(other)
    assert [r["code"] for r in other["code_table"]] == ["E0193"]            # numbers elsewhere are never read as codes
    assert "27412 Autologous chondrocyte implantation, knee" in [p["text"] for p in other["paragraphs"]]
