"""Engine accuracy rules (C1, C4-C6, C9, C10): each test pins a failure seen in a real kit run.

The judge's notes on the 20 kit plans (eval/results/judge.json, 2026-09-27) named the problems: an
article whose fixes came before its first heading answered with one glued, unreadable step (rows 3,
11, 17); chosen steps lost because "Please remove..." or "You may need to check..." did not read as
instructions; Exit Safe mode ordered before entering it; half a procedure. No LLM is called here.
"""

import json
from pathlib import Path

from app.compiler.trimmer import trim_description
from app.models import DraftAction, DraftStep, Intent, SiisSentence
from app.pipeline.categorize import categorize
from app.pipeline.deglue import deglue, is_glued
from app.pipeline.extract import actions_from_selection, extract_rules, sentence_steps
from app.pipeline.ground import ground_with_report
from app.pipeline.normalize import clean_siis
from app.pipeline.order import order
from app.pipeline.segment import segment_with_sections, split_sections

ROOT = Path(__file__).resolve().parents[2]
KIT = json.loads((ROOT / "data/kit/siis_responses.json").read_text(encoding="utf-8"))["responses"]
ROW = {r["id"]: r for r in KIT}


def _sentences(*texts: str, section: str = "Steps") -> list[SiisSentence]:
    return [SiisSentence(id=f"S{n + 1}", section=section, text=t) for n, t in enumerate(texts)]


def _answer(*actions: dict) -> dict:
    return {
        "goals": [
            {"problem": "p", "title": "a b", "topic": "T", "domain": "Display", "actions": list(actions)}
        ]
    }


def _action(ids, name="Do It", path="", verb="none"):
    return {
        "src_ids": ids,
        "name": name,
        "description": "It will do it",
        "screen_path": path,
        "intent_verb": verb,
    }


# ---- C1: lost spaces ------------------------------------------------------------------------------
def test_a_glued_article_gets_its_spaces_back_and_keeps_its_words():
    clean, _ = clean_siis(ROW["row_3"]["siis_response"])
    # a lowercase word after a glued full stop is the writer's typo for a comma: the purpose stays on
    assert "To unlock your device locked due to security reasons. restart your device and ensure" in clean
    assert "Booting to Safe Mode will set the samsung keyboard as default" in clean
    assert "restartyourdevice" not in clean and "kidshome" not in clean
    # only spaces were added: the letters are the article's, in its order
    raw = ROW["row_3"]["siis_response"]["content"]
    squash = "".join(c for c in clean if c.isalnum()).lower()
    assert squash in "".join(c for c in raw if c.isalnum()).lower().replace("kidshomepinsamsungcom", "")


def test_a_clean_article_is_left_exactly_as_written():
    for row in KIT:
        text = row["siis_response"]["content"]
        if not is_glued(text):
            fixed = deglue(text)
            # at most a lost sentence break ("view.Once") gets its space; rare words are never split
            assert fixed.replace(". ", ".") == text.replace(". ", ".")
    assert (
        deglue("Use it on your foldable phone with SmartThings.")
        == "Use it on your foldable phone with SmartThings."
    )


def test_deglue_is_linear_on_a_hostile_run():
    import time

    started = time.perf_counter()
    deglue("x" * 20000 + " " + "restartyourdevice" * 300)
    assert time.perf_counter() - started < 2.0


# ---- C4: the article before its first heading --------------------------------------------------------
def test_text_between_the_breadcrumb_and_the_first_heading_is_a_section():
    clean, _ = clean_siis(ROW["row_3"]["siis_response"])
    first = split_sections(clean)[0]
    assert first["heading"] == "Some things to check first"
    assert any("USB mouse and keyboard" in s for s in first["sentences"])
    assert not any("Smartphone" in s for s in first["sentences"])  # the breadcrumb itself is gone


def test_an_article_with_only_a_breadcrumb_before_its_heading_is_unchanged():
    clean, _ = clean_siis(ROW["row_21"]["siis_response"])
    sections = split_sections(clean)
    assert not sections[0]["heading"].startswith("Touchscreen issues")  # no breadcrumb section
    assert all("Smartphone" not in s for sec in sections for s in sec["sentences"])


# ---- C5: what counts as an instruction ---------------------------------------------------------------
def test_polite_modal_and_conditional_instructions_are_steps():
    cases = {
        "First, please carefully inspect your phone for damage.": "Carefully inspect your phone for damage.",
        "Now, please connect your phone to its charger.": "Connect your phone to its charger.",
        "Shine a flashlight into the SIM/MicroSD slot.": "Shine a flashlight into the SIM/MicroSD slot.",
        "You can also try forcing a restart by pressing and holding the Power button.": (
            "Try forcing a restart by pressing and holding the Power button."
        ),
        "If your device displays a black screen, you may need to check the charger for damage.": (
            "If your device displays a black screen, check the charger for damage."
        ),
        "For fast, quality repairs you can trust, visit a local Samsung Authorized Service Center.": (
            "For fast, quality repairs you can trust, visit a local Samsung Authorized Service Center."
        ),
        "Alternatively, swipe down from the top of the screen.": "Alternatively, swipe down from the top of the screen.",
    }
    for text, step in cases.items():
        assert sentence_steps(text) == [step], text


def test_explanations_are_never_steps():
    for text in (
        "It's important to note that the charging indicator might take 10 minutes to appear.",
        "Using Safe mode helps identify if a third-party app is causing conflicts.",
        "If you have a Samsung Premium Care contract, it covers accidental damage.",
        "Your phone will restart, and Safe mode will be displayed.",
    ):
        assert sentence_steps(text) == [], text


def test_a_selection_of_explanations_keeps_only_a_question():
    sentences = _sentences("Terms and Conditions apply.", "Did you drop your phone and smash the screen?")
    actions, _, _ = actions_from_selection(
        _answer(_action(["S1"]), _action(["S1", "S2"], "Check")), sentences
    )
    assert [[s.text for s in a.steps] for a in actions] == [["Did you drop your phone and smash the screen?"]]


# ---- C5: completing what the model chose -----------------------------------------------------------
def test_the_start_of_an_introduced_procedure_is_added():
    sentences = _sentences(
        "A reset erases your data.",
        "To perform a factory data reset:",
        "Navigate to and open Settings.",
        "Tap General management.",
        "Tap Factory data reset.",
        "You will need to enter your Samsung account credentials to complete the reset.",
    )
    actions, _, _ = actions_from_selection(_answer(_action(["S6"], "Factory Data Reset")), sentences)
    assert [s.src_ids[0] for s in actions[0].steps] == ["S3", "S4", "S5", "S6"]


def test_a_procedure_that_begins_on_its_own_takes_nothing_before_it():
    sentences = _sentences(
        "The process for restarting varies by model:",
        "Press and hold the Power button, then tap Restart.",
        "If the touchscreen is unresponsive, force a restart by holding Volume down and Power.",
        "Once the device turns off, press the Power button again.",
    )
    actions, _, _ = actions_from_selection(_answer(_action(["S3", "S4"], "Force Restart Device")), sentences)
    assert [s.src_ids[0] for s in actions[0].steps] == ["S3", "S4"]


def test_an_action_that_stops_short_of_its_setting_takes_the_step_that_reaches_it():
    sentences = _sentences(
        "Navigate to Settings.", "Tap Apps.", "Tap Storage.", "Tap Clear cache.", "Tap OK."
    )
    answer = _answer(_action(["S1", "S2", "S3"], "Clear Cache", "Settings > Apps > Storage > Clear cache"))
    actions, _, _ = actions_from_selection(answer, sentences)
    assert [s.text for s in actions[0].steps][-1] == "Tap Clear cache."


def test_repeated_selections_are_merged_and_a_second_procedure_is_split_off():
    sentences = _sentences(
        "Press and hold the Power button.",
        "Touch and hold Power off.",
        "Tap Safe mode.",
        "Your phone will restart in Safe mode.",
        "While in Safe mode, check if the app still fails.",
        "If it works, another app is the cause.",
        "To exit Safe mode, restart your phone normally.",
    )
    answer = _answer(
        _action(["S1", "S2", "S3", "S7"], "Enter Safe Mode"),
        _action(["S5"], "Check The App In Safe Mode"),
        _action(["S1", "S2"], "Exit Safe Mode"),  # the entering steps again: nothing new
    )
    actions, _, _ = actions_from_selection(answer, sentences)
    assert [a.name for a in actions] == ["Enter Safe Mode", "Exit Safe Mode", "Check The App In Safe Mode"]


# ---- C9: Safe mode in, work in it, Safe mode out ------------------------------------------------------
def test_safe_mode_is_entered_before_its_checks_and_left_after_them():
    def action(name, text):
        return DraftAction(name=name, steps=[DraftStep(text=text, src_ids=["S1"])])

    actions = categorize(
        [
            action("Restart Phone Normally", "To exit Safe mode, restart your phone normally."),
            action("Check Gmail", "While in Safe mode, check if your email app still fails."),
            action("Clear Cache", "Tap Clear cache."),
            action("Enter Safe Mode", "Tap Safe mode."),
        ]
    )
    assert [a.name for a in order(actions)] == [
        "Clear Cache",
        "Enter Safe Mode",
        "Check Gmail",
        "Restart Phone Normally",
    ]


# ---- C6: the article's own words are grounded ----------------------------------------------------------
def test_a_clause_of_the_cited_sentence_is_grounded():
    sentences = _sentences("Tap Clear data, and then tap OK.")
    action = DraftAction(
        name="Clear Data",
        steps=[DraftStep(text="Tap Clear data.", src_ids=["S1"]), DraftStep(text="Tap OK.", src_ids=["S1"])],
    )
    kept, report = ground_with_report([action], sentences)
    assert [s.text for s in kept[0].steps] == ["Tap Clear data.", "Tap OK."] and not report["dropped_steps"]


# ---- rules-only on a loosely matching article -----------------------------------------------------------
def test_rules_only_keeps_the_most_relevant_sections_only():
    clean, _ = clean_siis(ROW["row_7"]["siis_response"])
    intents = [Intent(text=ROW["row_7"]["original_query"], title="Dark screen")]
    sentences, sections = segment_with_sections(clean, intents)
    actions, _ = extract_rules(intents, sentences, sections)
    assert 0 < len(actions) <= 4


# ---- C10: descriptions ------------------------------------------------------------------------------
def test_a_phrasal_verb_keeps_its_particle():
    assert trim_description("It will check if the device turns on") == "It will check if device turns on"
    assert trim_description("It will clear the data of") == "It will clear the data"


def test_a_chosen_introducing_line_brings_the_steps_under_it():
    sentences = _sentences(
        "To enter Safe mode:",
        "Press and hold the Power button.",
        "Alternatively, swipe down and tap the Power icon.",
        "Touch and hold Power off, then tap Safe mode.",
        "Once in Safe mode, try removing recently installed apps.",
    )
    actions, _, _ = actions_from_selection(_answer(_action(["S1", "S4"], "Enter Safe Mode")), sentences)
    assert [s.src_ids[0] for s in actions[0].steps] == ["S2", "S3", "S4", "S4"]


def test_every_method_listed_for_a_step_is_kept():
    sentences = _sentences(
        "There are two methods for this:",
        "Press and hold the Power and Volume down buttons for 20 seconds.",
        "If your device has a removable battery, remove the battery for 60 seconds, and then reinsert it.",
    )
    for ids in (["S2"], ["S1", "S2"]):
        actions, _, _ = actions_from_selection(_answer(_action(ids, "Force Restart Device")), sentences)
        assert [s.src_ids[0] for s in actions[0].steps] == ["S2", "S3"], ids


def test_the_skipped_steps_of_a_numbered_procedure_are_completed():
    from app.pipeline.extract import complete_procedure

    headings = ["Step 1: Check for Damage", "Step 2: Force a Restart", "Step 3: Charge the Device", "About"]
    texts = [
        "Inspect the charger for damage.",
        "Press and hold Power and Volume down.",
        "Charge it for 1 hour.",
        "It helps.",
    ]
    sentences = [
        SiisSentence(id=f"S{n + 1}", section=h, text=t) for n, (h, t) in enumerate(zip(headings, texts))
    ]
    sections = [
        {"heading": h, "sentence_ids": [f"S{n + 1}"], "relevance": [0.6], "relevant": True}
        for n, h in enumerate(headings)
    ]
    chosen = [
        DraftAction(name="Force Restart", steps=[DraftStep(text=texts[1], src_ids=["S2"])]),
        DraftAction(name="Charge", steps=[DraftStep(text=texts[2], src_ids=["S3"])]),
    ]
    actions, added = complete_procedure(chosen, sentences, sections)
    assert added == ["Step 1: Check for Damage"] and actions[-1].steps[0].src_ids == ["S1"]
    # one numbered section followed is not a procedure being followed: nothing is added
    assert complete_procedure(chosen[:1], sentences, sections)[1] == []


def test_a_plain_restart_is_not_mistaken_for_leaving_safe_mode():
    def action(name, text):
        return DraftAction(name=name, steps=[DraftStep(text=text, src_ids=["S1"])])

    actions = categorize(
        [
            action("Enter Safe Mode", "Touch and hold Power off, then tap Safe mode."),
            action("Restart Device Normally", "Press and hold the Power button, then tap Restart."),
            action("Factory Data Reset", "Tap Factory data reset."),
        ]
    )
    assert [a.name for a in order(actions)] == [
        "Restart Device Normally",
        "Enter Safe Mode",
        "Factory Data Reset",
    ]


def test_a_section_heading_makes_a_readable_action():
    from app.compiler.trimmer import trim_description
    from app.pipeline.extract import rules_description, rules_name

    made = {
        h: (rules_name(h), trim_description(rules_description(rules_name(h))))
        for h in (
            "3. Charger Issues",
            "8. Factory Data Reset",
            "9. Further Assistance",
            "Step 1: Check Email Access on a PC",
        )
    }
    assert made == {
        "3. Charger Issues": ("Check Charger Issues", "It will check charger issues"),
        "8. Factory Data Reset": ("Factory Data Reset", "It will restore the factory settings"),
        "9. Further Assistance": ("Get Further Assistance", "It will get help from Samsung Support"),
        "Step 1: Check Email Access on a PC": (
            "Check Email Access on a PC",
            "It will check email access on PC",
        ),
    }


# ---- call C (coverage) and what is done with its answer ------------------------------------------------
def _kit_units(row_id: str):
    from app.pipeline.extract import paragraph_units

    clean, _ = clean_siis(ROW[row_id]["siis_response"])
    sentences, sections = segment_with_sections(clean, [Intent(text="x")])
    return sentences, sections, paragraph_units(sentences, sections)


def test_paragraphs_are_the_articles_lines_and_a_step_heading_can_be_one():
    _, _, units = _kit_units("row_16")
    ldi = next(
        u
        for u in units
        if any("Shine a flashlight" in x for x in [u["heading"]])
        or u["sentence_ids"] == ["S10", "S11", "S12"]
    )
    assert ldi["heading"].startswith("Step 1") and ldi["instruction_ids"] == ["S10", "S11", "S12"]
    _, _, units = _kit_units("row_20")
    update = next(u for u in units if u["heading"].startswith("3."))
    assert update["heading_step"] == "Update device software" and update["instruction_ids"] == ["S15"]


def test_general_fix_steps_exclude_the_articles_own_feature():
    from app.pipeline.extract import general_step_paragraphs

    _, sections, units = _kit_units("row_20")
    headings = {u["heading"] for u in units if u["id"] in general_step_paragraphs(units, sections)}
    assert "2. Adjust Screen Orientation Settings" not in headings and "5. Test App Rotation" not in headings
    assert {"1. Check for Physical Damage", "3. Update Device Software", "4. Restart Your Device"} <= headings


def test_added_paragraphs_merge_skip_repeats_and_continue_only_what_follows():
    from app.pipeline.extract import add_paragraphs

    sentences = _sentences(
        "Connect a USB mouse to your phone.",
        "You can also back up your data by connecting it to a monitor.",
        "To clean your device, gently wipe the screen.",
        "Visit a Samsung service center.",
        "Charge the phone for an hour.",
        section="Some things to check first",
    )
    units = [
        {"id": "P1", "heading": "Some things to check first", "sentence_ids": ["S1", "S2", "S3"],
         "instruction_ids": ["S1", "S2", "S3"], "heading_step": None},
        {"id": "P2", "heading": "Some things to check first", "sentence_ids": ["S4"],
         "instruction_ids": ["S4"], "heading_step": None},
        {"id": "P3", "heading": "Some things to check first", "sentence_ids": ["S5"],
         "instruction_ids": ["S5"], "heading_step": None},
    ]  # fmt: skip
    mouse = DraftAction(
        name="Use A USB Mouse", steps=[DraftStep(text="Connect a USB mouse to your phone.", src_ids=["S1"])]
    )
    repair = DraftAction(
        name="Visit Service", steps=[DraftStep(text="Visit a Samsung service center.", src_ids=["S9"])]
    )
    actions, added = add_paragraphs(
        [mouse, repair],
        sentences,
        units,
        ["P1", "P2", "P3"],
        {"P1": "Use A USB Mouse", "P2": "Visit Service Center", "P3": "Charge The Phone"},
    )
    steps = [s.text for s in actions[0].steps]
    assert steps == [
        "Connect a USB mouse to your phone.",
        "Back up your data by connecting it to a monitor.",
    ]  # stops at "To clean..."
    assert added == ["Charge The Phone"]  # P2 repeats a step already in the plan


def test_an_action_holding_two_fixes_is_split_at_its_setting():
    sentences = _sentences(
        "If your screen protector is peeling, remove it.",
        "If you wish to keep your screen protector on, you can try enabling Touch sensitivity.",
        "To do this, go to Settings, tap Display, and then tap the switch next to Touch sensitivity.",
    )
    answer = _answer(
        _action(
            ["S1", "S3"],
            "Remove Damaged Screen Protector",
            "Settings > Display > Touch sensitivity",
            "enable",
        )
    )
    actions, _, _ = actions_from_selection(answer, sentences)
    assert [(a.name, a.screen_path) for a in actions] == [
        ("Remove Damaged Screen Protector", None),
        ("Enable Touch sensitivity", "Settings > Display > Touch sensitivity"),
    ]
    assert actions[1].steps[0].src_ids == ["S2"]  # "To do this" brought the sentence it refers to


def test_coverage_leaves_the_primary_model_to_extraction_near_its_limit(monkeypatch):
    from app.config import settings
    from app.llm import router

    router.reset_cooldowns()
    assert router._Stage("coverage").model == settings.coverage_model
    for _ in range(
        settings.llm_requests_per_minute[settings.coverage_model] - settings.coverage_leave_primary
    ):
        router._count_call(settings.coverage_model)
    assert router._Stage("coverage").model == settings.coverage_fallback_model
    router.reset_cooldowns()


def test_heading_names_an_added_step_without_repeating_another_action():
    from app.pipeline.extract import _imperative_name

    assert (
        _imperative_name("Before proceeding, back up your personal data to avoid losing it.")
        == "Back Up Your Personal Data"
    )
    assert sentence_steps("Consider the following.") == []
    assert sentence_steps("Before proceeding, it is crucial to back up your personal data.") == [
        "Before proceeding, back up your personal data."
    ]


def test_goals_the_complaint_does_not_state_fold_into_the_one_it_does():
    from app.pipeline.extract import merge_unstated_goals

    query = (
        "My Samsung A115G tablet screen flashes and then goes completely blank whenever I tap to open an "
        "email in Gmail, and after it works for a short time it goes blank again."
    )
    intents = [
        Intent(
            text="The email account may need its settings reviewed or re-added", title="Email account sync"
        ),
        Intent(text="The phone may not be connected to Wi-Fi or mobile data", title="Wi-Fi mobile data"),
        Intent(
            text="The tablet screen flashes and goes blank when opening an email in Gmail",
            title="Screen blanking",
        ),
    ]
    actions = [
        DraftAction(name=f"A{n}", steps=[DraftStep(text="Tap OK.", src_ids=["S1"])], intent_index=n)
        for n in range(3)
    ]
    moved, topics, kept, merged = merge_unstated_goals(query, actions, ["a", "b", "c"], intents)
    assert [i.title for i in kept] == ["Screen blanking"] and sorted(merged) == [
        "Email account sync",
        "Wi-Fi mobile data",
    ]
    assert {a.intent_index for a in moved} == {0} and topics == ["c"]
    # two problems the complaint names both stay
    two = "My Galaxy S22 touchscreen lags behind my taps, and I also want to switch the navigation back to buttons."
    both = [
        Intent(text="Touchscreen responds slowly to taps"),
        Intent(text="Switch navigation from swipe gestures to buttons"),
    ]
    assert len(merge_unstated_goals(two, actions[:2], ["a", "b"], both)[2]) == 2


def test_a_preparation_step_opens_the_action_it_prepares():
    from app.pipeline.extract import add_paragraphs

    heading = "6. Perform a Factory Data Reset"
    sentences = _sentences(
        "It is important to back up your data before proceeding with this step.",
        "Navigate to Settings, search for and select Factory data reset.",
        section=heading,
    )
    units = [
        {
            "id": "P1",
            "heading": heading,
            "sentence_ids": ["S1"],
            "instruction_ids": ["S1"],
            "heading_step": None,
        },
        {
            "id": "P2",
            "heading": heading,
            "sentence_ids": ["S2"],
            "instruction_ids": ["S2"],
            "heading_step": None,
        },
    ]
    reset = DraftAction(
        name="Perform Factory Data Reset", steps=[DraftStep(text="Navigate to Settings.", src_ids=["S2"])]
    )
    actions, added = add_paragraphs([reset], sentences, units, ["P1"])
    assert added == [] and len(actions) == 1
    assert [s.src_ids[0] for s in actions[0].steps] == ["S1", "S2"]


def test_the_steps_between_an_actions_own_join_it():
    from app.pipeline.extract import add_paragraphs

    heading = "8. Factory Data Reset"
    sentences = _sentences(
        "Navigate to and open Settings.",
        "Tap General management.",
        "Tap Reset.",
        "Tap Delete all.",
        section=heading,
    )
    units = [
        {
            "id": f"P{n + 1}",
            "heading": heading,
            "sentence_ids": [f"S{n + 1}"],
            "instruction_ids": [f"S{n + 1}"],
            "heading_step": None,
        }
        for n in range(4)
    ]
    reset = DraftAction(
        name="Factory Data Reset",
        steps=[
            DraftStep(text="Navigate to and open Settings.", src_ids=["S1"]),
            DraftStep(text="Tap Delete all.", src_ids=["S4"]),
        ],
    )
    actions, added = add_paragraphs([reset], sentences, units, ["P2", "P3"])
    assert added == [] and [s.src_ids[0] for s in actions[0].steps] == ["S1", "S2", "S3", "S4"]


def test_folding_a_repeated_goal_leaves_no_duplicate_action():
    from app.pipeline.extract import merge_unstated_goals

    query = "My tablet's screen stays dark and only three app icons are lit, and the rest won't open."
    intents = [
        Intent(text="The tablet's screen remains completely dark except for three app icons that are lit."),
        Intent(text="The tablet's display is dark except for a few app icons, and no apps can be opened."),
    ]
    charge = DraftStep(text="Charge the tablet for an hour.", src_ids=["S2"])
    actions = [
        DraftAction(name="Charge Device For One Hour", steps=[charge], intent_index=0),
        DraftAction(name="Charge Device For Recovery", steps=[charge], intent_index=1),
    ]
    moved, _, kept, _ = merge_unstated_goals(query, actions, ["a", "b"], intents, _sentences("x", "y"))
    assert len(kept) == 1 and [a.name for a in moved] == ["Charge Device For One Hour"]
