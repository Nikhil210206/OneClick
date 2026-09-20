import copy
import json
from pathlib import Path

import pytest

from evalkit.catalog import Catalog, check_deeplinks
from evalkit.checks import (
    FAIL,
    WARN,
    check_description,
    check_response,
    check_results_line,
    check_title,
    check_url_leaks,
)
from evalkit.paths import SAMPLE_OUTPUT_PATH
from evalkit.stats import jaccard, norm_query, percentile

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load()


@pytest.fixture
def good():
    return json.loads((FIXTURES / "compliant_response.json").read_text())


def codes(findings, severity=None):
    return {x.code for x in findings if severity is None or x.severity == severity}


# --------------------------------------------------------------------------- the organisers' sample


def test_sample_output_goal_needs_period_in_strict_mode_only():
    body = json.loads(SAMPLE_OUTPUT_PATH.read_text())["response"]
    assert "GOAL_PERIOD" in codes(check_response(body, "strict"), FAIL)
    assert "GOAL_PERIOD" in codes(check_response(body, "lenient"), WARN)
    assert "GOAL_FORMAT" not in codes(check_response(body, "lenient"))


def test_sample_output_descriptions_break_the_5_to_7_word_rule():
    # The kit's own sample uses 9- and 12-word descriptions; the FAQ rule is 5-7 incl. "It will".
    body = json.loads(SAMPLE_OUTPUT_PATH.read_text())["response"]
    desc = [x for x in check_response(body) if x.code == "DESC_WORDS"]
    assert len(desc) == 2


def test_sample_output_deeplink_is_catalog_valid(catalog):
    body = json.loads(SAMPLE_OUTPUT_PATH.read_text())["response"]
    findings, stats = check_deeplinks(body, catalog)
    assert findings == []
    assert stats.entry_ids == ["DL-0542"] and stats.validity == 1.0 and stats.auto_link_rate == 1.0


# --------------------------------------------------------------------------- compliant fixture


def test_compliant_fixture_has_no_findings(good, catalog):
    assert check_response(good) == []
    findings, stats = check_deeplinks(good, catalog)
    assert findings == [] and stats.act_catalog == 1 and stats.val_valid == 1


def test_empty_contexts_is_schema_valid():
    assert check_response({"contexts": []}) == []
    assert check_response({"contexts": [], "meta": {"latency_ms": 3, "cache_hit": False}}) == []


# --------------------------------------------------------------------------- schema


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["contexts"][0].pop("title"),
        lambda b: b["contexts"][0]["actions"][0].update(category="automatic"),
        lambda b: b["contexts"][0]["actions"][0]["stepGroups"][0]["validationDeeplink"].update(
            condition="gte"
        ),
        lambda b: b.update(contexts="nope"),
    ],
)
def test_schema_violations(good, mutate):
    mutate(good)
    assert "SCHEMA" in codes(check_response(good), FAIL)


def test_non_object_body_is_schema_fail():
    assert "SCHEMA" in codes(check_response(["contexts"]), FAIL)


# --------------------------------------------------------------------------- format rules


@pytest.mark.parametrize(
    "goal,code",
    [
        ("Follow these steps to perform this Swipe Navigation Configuration.", None),
        ("Follow these steps to perform this Screen Troubleshooting", "GOAL_PERIOD"),
        ("Follow these steps to fix the Screen Troubleshooting.", "GOAL_FORMAT"),
        ("Follow these steps to perform this Screen Repair.", "GOAL_FORMAT"),
        ("Follow these steps to perform this Screen Troubleshooting Troubleshooting.", "GOAL_TOPIC_DUP"),
    ],
)
def test_goal_regex(good, goal, code):
    good["contexts"][0]["goal"] = goal
    found = codes(check_response(good))
    assert (code in found) if code else not ({"GOAL_FORMAT", "GOAL_PERIOD"} & found)


@pytest.mark.parametrize(
    "title,fail",
    [
        ("Screen damage", False),
        ("Battery fast drain", False),
        ("Screen", True),
        ("Black screen on boot", True),
    ],
)
def test_title_word_count(title, fail):
    assert ("TITLE_WORDS" in codes(check_title(title, "t"), FAIL)) is fail


def test_title_sentence_case_is_a_warning_with_proper_nouns_allowed():
    assert codes(check_title("Smart Switch transfer", "t")) == set()
    assert codes(check_title("Screen Display Damage", "t"), WARN) == {"TITLE_CASE"}


@pytest.mark.parametrize(
    "desc,expected",
    [
        ("It will fix the screen", set()),  # 5 words
        ("It will let you choose navigation type", set()),  # 7 words, Appendix B
        ("It will fix it", {"DESC_WORDS"}),  # 4
        ("It will help you locate the nearest center", {"DESC_WORDS"}),  # 8
        ("This will fix the screen now", {"DESC_PREFIX"}),
        ("it will fix the screen", {"DESC_PREFIX"}),
    ],
)
def test_description_rule(desc, expected):
    assert codes(check_description(desc, "d"), FAIL) == expected


@pytest.mark.parametrize("score", [1.2, -0.1, "0.5", None, True, float("nan")])
def test_score_range(good, score):
    good["contexts"][0]["score"] = score
    assert "SCORE_RANGE" in codes(check_response(good), FAIL)


def test_step_rules(good):
    steps = good["contexts"][0]["actions"][0]["stepGroups"][0]["steps"]
    steps[0] = "1. Open Settings."
    steps[1] = "Tap on Accounts and backup"
    found = codes(check_response(good), WARN)
    assert {"STEP_NUMBERED", "STEP_PERIOD"} <= found
    good["contexts"][0]["actions"][1]["stepGroups"][0]["steps"] = []
    assert "STEPS_EMPTY" in codes(check_response(good), FAIL)


def test_duplicate_and_repeated_actions(good):
    ctx = good["contexts"]
    ctx[0]["actions"].append(copy.deepcopy(ctx[0]["actions"][1]))
    assert "ACTION_NAME_DUP" in codes(check_response(good), WARN)
    ctx[0]["actions"].pop()
    ctx.append(copy.deepcopy(ctx[0]))
    assert "ACTION_REPEATED" in codes(check_response(good), WARN)


# --------------------------------------------------------------------------- categories


def test_auto_without_link_fails(good):
    good["contexts"][0]["actions"][0]["stepGroups"][0]["actionableDeeplink"] = None
    assert "AUTO_NO_LINK" in codes(check_response(good), FAIL)


def test_manual_with_link_warns(good):
    link = good["contexts"][0]["actions"][0]["stepGroups"][0]["actionableDeeplink"]
    good["contexts"][0]["actions"][1]["stepGroups"][0]["actionableDeeplink"] = link
    assert "MANUAL_HAS_LINK" in codes(check_response(good), WARN)


def test_critical_must_be_last(good):
    actions = good["contexts"][0]["actions"]
    actions.insert(0, actions.pop())  # restart first
    assert "CRITICAL_NOT_LAST" in codes(check_response(good), WARN)


def test_dependency_exception_does_not_warn(good):
    # design doc: "Critical actions stay last unless a dependency requires otherwise"
    # (data/dependencies.json: safe mode -> uninstall in safe mode). Real shape from
    # data/fixtures/email_not_responding/plan.json.
    good["contexts"][0]["actions"] = [
        {
            "actionName": "Restart In Safe Mode",
            "description": "It will check for app conflicts",
            "category": "critical",
            "stepGroups": [
                {
                    "steps": ["Restart the phone into Safe mode."],
                    "actionableDeeplink": None,
                    "validationDeeplink": None,
                }
            ],
        },
        {
            "actionName": "Uninstall Email App",
            "description": "It will remove the conflicting app",
            "category": "manual",
            "stepGroups": [
                {
                    "steps": ["Uninstall the email app while in Safe mode."],
                    "actionableDeeplink": None,
                    "validationDeeplink": None,
                }
            ],
        },
    ]
    assert "CRITICAL_NOT_LAST" not in codes(check_response(good))


# --------------------------------------------------------------------------- URL leaks (G5)


@pytest.mark.parametrize(
    "text",
    [
        "Visit https://www.samsung.com/support for help.",
        "Go to www.samsung.com.",
        "Open samsung.com/us/support in a browser.",
        "See the guide at help.html.",
        "Email kidshome.pin@samsung.com for a PIN.",
        "Read [the guide](support page).",
        "![screenshot](img)",
        '<a href="x">here</a>',
        "Open bixby://masked/act/aa73a35e8d manually.",
    ],
)
def test_url_leak_detected_in_steps(good, text):
    good["contexts"][0]["actions"][1]["stepGroups"][0]["steps"] = [text]
    assert "URL_LEAK" in codes(check_response(good), FAIL)


@pytest.mark.parametrize(
    "text",
    [
        "Tap Settings, then tap Display.",
        "Charge the phone for 30 minutes, e.g. with the original charger.",
        "Tap Accounts and backup, then Samsung Cloud.",
        "Restart the phone.Then check again.",
    ],
)
def test_ordinary_text_is_not_a_leak(text):
    assert check_url_leaks({"steps": [text]}) == []


def test_catalog_uri_allowed_only_in_deeplink_fields(good):
    assert check_url_leaks(good) == []
    good["contexts"][0]["actions"][0]["stepGroups"][0]["actionableDeeplink"]["deeplink"] = "https://x.io/a"
    assert "URL_LEAK" in codes(check_response(good))


def test_meta_is_scanned_too():
    assert "URL_LEAK" in codes(check_response({"contexts": [], "meta": {"help": "www.example.org"}}))


def test_catalog_text_has_no_false_positive_leaks(catalog):
    for entry in catalog.by_id.values():
        for name in ("description", "message", "qna_description"):
            if isinstance(entry.get(name), str):
                assert check_url_leaks({name: entry[name]}) == [], entry["id"]


# --------------------------------------------------------------------------- catalog validity (A2)


def _group(body):
    return body["contexts"][0]["actions"][0]["stepGroups"][0]


def test_unknown_uri_fails(good, catalog):
    _group(good)["actionableDeeplink"]["deeplink"] = "bixby://masked/act/0000000000"
    assert "DL_UNKNOWN" in codes(check_deeplinks(good, catalog)[0], FAIL)


def test_validation_uri_used_as_actionable(good, catalog):
    _group(good)["actionableDeeplink"]["deeplink"] = "bixby://masked/val/266037d0c5"
    (finding,) = [x for x in check_deeplinks(good, catalog)[0] if x.code == "DL_UNKNOWN"]
    assert "validation URI" in finding.message


def test_twin_entry_validation_is_not_own(good, catalog):
    # DL-0541 (offURL) shares the validation URI with DL-0542 but has no condition/value.
    _group(good)["validationDeeplink"] = {
        "deeplink": "bixby://masked/val/266037d0c5",
        "key": "Back up data (Samsung Cloud)",
    }
    assert "VAL_ALTERED" in codes(check_deeplinks(good, catalog)[0], FAIL)


def test_validation_from_another_entry(good, catalog):
    _group(good)["validationDeeplink"] = {
        "deeplink": "bixby://masked/val/ef6814259a",
        "key": "Use 24-hour format",
    }
    assert "VAL_NOT_OWN" in codes(check_deeplinks(good, catalog)[0], FAIL)


def test_altered_description_warns(good, catalog):
    _group(good)["actionableDeeplink"]["description"] = "Turns on backup."
    assert "DL_ALTERED" in codes(check_deeplinks(good, catalog)[0], WARN)


def test_dummy_positive_rules(good, catalog):
    g = _group(good)
    g["actionableDeeplink"] = {
        "deeplink": "bixby://dummy_positive",
        "description": "Open backup",
        "message": "Back up",
    }
    findings, stats = check_deeplinks(good, catalog)
    assert {"DUMMY_TEXT", "DUMMY_WITH_VALIDATION"} <= codes(findings, WARN)
    assert stats.act_dummy == 1 and stats.dummy_rate == 1.0
    g["actionableDeeplink"] = {
        "deeplink": "bixby://dummy_positive",
        "description": "Open navigation bar settings under Display",
        "message": "Choose navigation type in Display settings",
    }
    g["validationDeeplink"] = None
    assert check_deeplinks(good, catalog)[0] == []


# --------------------------------------------------------------------------- results.jsonl lines (A5)


def _line(variations, query="My Galaxy S22 screen is blank"):
    return {"query": query, "query_variations": variations, "response": {"contexts": []}}


NINE = [
    "Galaxy S22 display shows nothing",
    "why is my s22 screen empty",
    "blank screen galaxy s22",
    "My phone's display went totally dark",
    "S22 screen stays white with no text",
    "screen blank samsung s22 help",
    "The display on my S22 is not showing anything at all",
    "ugh my s22 screen is just blank again",
    "galaxy s22 scren blnk no txt",
]


def test_variation_rules():
    assert codes(check_results_line(_line(NINE)), FAIL) == set()
    assert "VAR_COUNT" in codes(check_results_line(_line(NINE[:7])), FAIL)
    assert "VAR_COUNT" in codes(check_results_line(_line(NINE + NINE[:2])), FAIL)
    assert "VAR_UNIQUE" in codes(check_results_line(_line(NINE[:8] + [NINE[0].upper()])), FAIL)
    assert "VAR_IS_QUERY" in codes(
        check_results_line(_line(NINE[:8] + ["my galaxy s22 screen is blank!"])), WARN
    )
    assert "URL_LEAK" in codes(check_results_line(_line(NINE[:8] + ["see samsung.com"])), FAIL)
    assert "VAR_COUNT" in codes(check_results_line({"query": "q", "response": {}}), FAIL)


# --------------------------------------------------------------------------- stats


def test_norm_query_matches_kit_numbering_and_quotes():
    assert norm_query('1. "My Galaxy S24 screen goes blank."') == norm_query(
        "My Galaxy S24 screen goes blank"
    )


def test_percentile_and_jaccard():
    assert percentile([], 95) is None
    assert percentile(list(range(1, 101)), 95) == 95
    assert percentile([5.0], 50) == 5.0
    assert jaccard("screen is black", "black screen") == pytest.approx(2 / 3)
