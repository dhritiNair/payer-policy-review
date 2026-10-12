"""Classifier and diff: the rules, and the fixes made after probing (autojunk, moves, modal tiers)."""
import copy

import pytest

from uhc_monitor.classify import AMBIGUOUS, AMBIGUOUS_SUBTITLE, COSMETIC, SUBSTANTIVE, compare, compare_with_notes
from uhc_monitor.simulate import make_simulated_older


def rep(section, find, replace, **kw):
    return {"id": "t", "kind": "t", "op": "replace_text", "section": section, "find": find, "replace": replace,
            "description": "test", **kw}


def run(real, *edits):
    """Classify what changes from a simulated older version (the edits applied) to the real text."""
    return compare_with_notes(make_simulated_older(real, list(edits)), real)


def only(real, edit):
    changes, _ = run(real, edit)
    assert len(changes) == 1, [(c["change_type"], c["path"]) for c in changes]
    return changes[0]


# --- the fixes -------------------------------------------------------------------------------------------------

def test_one_word_edit_in_a_long_paragraph_is_one_modification(real_doc):
    # regression: difflib's autojunk made long paragraphs look unrelated, giving a removed + added pair
    c = only(real_doc, rep("Medical Records Documentation Used for Reviews", "laws that may require", "laws that can require"))
    assert c["change_type"] == "modified"
    assert len(c["before_text"]) > 200


def test_reorder_within_a_section_is_ignored_with_a_quiet_note(real_doc):
    older = copy.deepcopy(real_doc)
    next(s for s in older["sections"] if s["heading"] == "Description of Services")["paragraphs"].reverse()
    changes, notes = compare_with_notes(older, real_doc)
    assert changes == []
    assert any("reordered within 'Description of Services'" in n for n in notes)


def test_move_to_another_section_is_one_ambiguous_change(real_doc):
    older = copy.deepcopy(real_doc)
    sec = lambda h: next(s for s in older["sections"] if s["heading"] == h)
    para = sec("Benefit Considerations")["paragraphs"].pop(1)
    sec("Medical Records Documentation Used for Reviews")["paragraphs"].append(para)
    changes = compare(older, real_doc)
    assert len(changes) == 1 and changes[0]["change_type"] == "moved"
    assert changes[0]["rule_id"] == "AMB-MOVED" and changes[0]["label"] == AMBIGUOUS
    assert "Medical Records" in changes[0]["from_path"] and changes[0]["path"] == "Benefit Considerations"


@pytest.mark.parametrize("section, find, replace", [
    ("Benefit Considerations", "exclusions may apply", "exclusions might apply"),
    ("Benefit Considerations", "exclusions may apply", "exclusions will apply"),
    ("Safety Enclosure with Beds", "should be coded", "must be coded"),
    ("Safety Enclosure with Beds", "should be coded", "is to be coded"),
    ("Medical Records Documentation Used for Reviews", "documentation may be required", "documentation is required"),
])
def test_modal_word_changes_go_to_human_review_never_dismissed(real_doc, section, find, replace):
    c = only(real_doc, rep(section, find, replace))
    assert c["rule_id"] == "AMB-MODAL"
    assert c["label"] == AMBIGUOUS          # not substantive by rule, and never cosmetic


@pytest.mark.parametrize("section, find, replace, words", [
    ("Safety Enclosure with Beds", "when all of the following", "when any of the following", {"all", "any"}),
    ("Benefit Considerations", "does not include certain", "includes certain", {"not"}),
    ("Indications for Coverage", "are proven and medically necessary in certain circumstances.", "are unproven.", {"unproven"}),
])
def test_strong_coverage_words_stay_substantive(real_doc, section, find, replace, words):
    c = only(real_doc, rep(section, find, replace))
    assert c["rule_id"] == "SUB-KEYWORD" and c["label"] == SUBSTANTIVE
    assert words & set(c["evidence"].replace("[", " ").replace("]", " ").replace("'", " ").replace(",", " ").split())


# --- the rules --------------------------------------------------------------------------------------------------

def test_table_of_contents_only_change_is_cosmetic(real_doc):
    c = only(real_doc, rep("(Front matter)", "Clinical Evidence ...  3", "Clinical Evidence ...  4"))
    assert c["rule_id"] == "COS-TOC" and c["label"] == COSMETIC


@pytest.mark.parametrize("section, find, replace", [
    ("Safety Enclosure with Beds", "Medical helmet", "Medical Helmet"),                       # case
    ("Medical Records Documentation Used for Reviews", "member specific", "member-specific"),  # hyphen
    ("References", "2007 Nov;2(6):385-93", "2007 Nov; 2(6): 385-93"),                          # spacing
])
def test_format_only_changes_are_cosmetic(real_doc, section, find, replace):
    c = only(real_doc, rep(section, find, replace))
    assert c["rule_id"] == "COS-FORMAT" and c["label"] == COSMETIC


def test_policy_history_table_is_excluded_from_the_diff(real_doc):
    older = copy.deepcopy(real_doc)
    next(s for s in older["sections"] if s.get("excluded_from_diff"))["paragraphs"][0]["text"] += " (edited)"
    assert compare(older, real_doc) == []


def test_identical_documents_have_no_changes(real_doc):
    assert compare(real_doc, copy.deepcopy(real_doc)) == []


def test_code_added_is_substantive(real_doc):
    c = only(real_doc, {"id": "t", "kind": "t", "op": "delete_code", "code": "E0911", "description": "t"})
    assert c["rule_id"] == "SUB-CODE-LIST" and c["label"] == SUBSTANTIVE and c["change_type"] == "code_added"


def test_number_change_in_a_code_row_is_substantive(real_doc):
    c = only(real_doc, rep("Applicable Codes", "greater than 350 pounds", "greater than 300 pounds", code="E0301"))
    assert c["rule_id"] == "SUB-NUMBER" and c["label"] == SUBSTANTIVE


def test_paragraph_removed_in_a_coverage_section_is_substantive(real_doc):
    c = only(real_doc, {"id": "t", "kind": "t", "op": "delete_paragraph", "section": "Benefit Considerations",
                        "find": "Coverage does not include certain support services", "description": "t"})
    assert c["rule_id"] == "SUB-PARAGRAPH" and c["label"] == SUBSTANTIVE


def test_changes_outside_coverage_sections_are_ambiguous_not_substantive(real_doc):
    c = only(real_doc, {"id": "t", "kind": "t", "op": "delete_paragraph", "section": "Instructions for Use",
                        "find": "This Medical Policy may also be applied to Medicare Advantage", "description": "t"})
    assert c["rule_id"] == "AMB-DEFAULT" and c["label"] == AMBIGUOUS
    c = only(real_doc, rep("Description of Services", "are often incorporated", "are generally incorporated"))
    assert c["rule_id"] == "AMB-DEFAULT" and c["label"] == AMBIGUOUS


def test_every_decision_names_its_rule_and_evidence(real_doc):
    changes, _ = run(real_doc, rep("Benefit Considerations", "Most benefit plans", "Many benefit plans"),
                     rep("Safety Enclosure with Beds", "Medical helmet", "Medical Helmet"),
                     rep("Safety Enclosure with Beds", "when all of", "when any of"))
    assert changes
    for c in changes:
        assert c["rule_id"] and c["rule"] and c["evidence"] and c["label"] in (SUBSTANTIVE, COSMETIC, AMBIGUOUS)


def test_ambiguous_subtitle_wording():
    assert AMBIGUOUS_SUBTITLE == "Needs human review"


# --- Definitions is a coverage section (so the number and keyword rules apply there) -------------------------------

def test_a_threshold_changed_in_a_definitions_paragraph_is_substantive_by_number(knee_doc):
    """K6's pattern: the WOMAC pain-domain threshold in the Disabling Pain definition changed from > 30 to > 40."""
    c = only(knee_doc, rep("Definitions", "pain domain of > 40", "pain domain of > 30"))
    assert c["h1"] == "Definitions" and c["rule_id"] == "SUB-NUMBER" and c["label"] == SUBSTANTIVE
    assert "30" in c["evidence"] and "40" in c["evidence"]


def test_the_keyword_rule_applies_in_definitions_too(knee_doc):
    c = only(knee_doc, rep("Definitions", "Implant that functions as a scaffold", "Implant that does not function as a scaffold"))
    assert c["rule_id"] == "SUB-KEYWORD" and c["label"] == SUBSTANTIVE


def test_a_verb_swap_in_definitions_is_still_left_to_a_human(knee_doc):
    """K7's pattern: 'consider' -> 'accept' contains no coverage keyword, number or modal word, so it stays Ambiguous."""
    c = only(knee_doc, rep("Definitions", "UHC will accept three or more", "UHC will consider three or more"))
    assert c["rule_id"] == "AMB-DEFAULT" and c["label"] == AMBIGUOUS


def test_clinical_evidence_and_fda_are_deliberately_not_coverage_sections(knee_doc):
    from uhc_monitor.classify import COVERAGE_SECTIONS
    assert "Clinical Evidence" not in COVERAGE_SECTIONS and "U.S. Food and Drug Administration (FDA)" not in COVERAGE_SECTIONS
    k10 = only(knee_doc, rep("Xenografts", "existing clinical evidence does not support the use of bone xenografts in orthopedic surgery.",
                             "existing clinical evidence supports the use of bone xenografts in selected orthopedic procedures."))
    k11 = only(knee_doc, rep("U.S. Food and Drug Administration (FDA)", "used during the surgery require FDA approval",
                             "used during the surgery may require FDA approval"))
    assert (k10["rule_id"], k10["label"]) == ("AMB-DEFAULT", AMBIGUOUS) and (k11["rule_id"], k11["label"]) == ("AMB-DEFAULT", AMBIGUOUS)


def test_beds_and_mattresses_has_no_definitions_section(real_doc):
    assert "Definitions" not in [s["heading"] for s in real_doc["sections"]]      # so this change cannot move any Beds decision
