import pytest

from simple_sync import normalize_courses


def test_courses_array_shape():
    cfg = {"courses": [
        {"course_code": "CS61A", "gradescope_course_id": "1370542", "spreadsheet_id": "sheetA"},
        {"course_code": "CS10", "gradescope_course_id": "1371156", "spreadsheet_id": "sheetB"},
    ]}
    got = normalize_courses(cfg)
    assert [c["course_code"] for c in got] == ["CS61A", "CS10"]
    assert got[0]["gradescope_course_id"] == "1370542"
    assert got[1]["spreadsheet_id"] == "sheetB"


def test_legacy_flat_shape_becomes_one_course():
    cfg = {"GRADESCOPE_COURSE_ID": "1324284", "SPREADSHEET_ID": "sheetLegacy"}
    got = normalize_courses(cfg)
    assert len(got) == 1
    assert got[0]["gradescope_course_id"] == "1324284"
    assert got[0]["spreadsheet_id"] == "sheetLegacy"
    assert got[0]["course_code"]


def test_ids_coerced_to_str():
    cfg = {"courses": [{"course_code": "CS10", "gradescope_course_id": 1371156,
                        "spreadsheet_id": "s"}]}
    assert normalize_courses(cfg)[0]["gradescope_course_id"] == "1371156"


def test_missing_spreadsheet_id_raises():
    cfg = {"courses": [{"course_code": "CS10", "gradescope_course_id": "1371156"}]}
    with pytest.raises(ValueError, match="CS10"):
        normalize_courses(cfg)


def test_empty_config_raises():
    with pytest.raises(ValueError):
        normalize_courses({})
