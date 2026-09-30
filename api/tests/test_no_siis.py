"""Requests without an article (cache/no_siis.py): never answered from nothing.

Order: the same question answered before -> a close cached plan (kit table or any solved plan, strict
threshold, slot guard) -> the pipeline over a remembered article (strict threshold + clear winner)
-> empty with fallback no_siis_context. Runs keyless (rules-only), like the rest of the suite.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cache
from app.cache import no_siis
from app.main import app
from app.pipeline.normalize import clean_siis
from app.schema import ContextDeeplinkResponse

ROOT = Path(__file__).resolve().parents[2]
KIT = json.loads((ROOT / "data/kit/siis_responses.json").read_text(encoding="utf-8"))["responses"]
RESULTS = [
    json.loads(x) for x in (ROOT / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()
]
UNRELATED = "my washing machine drum makes a loud grinding noise during the spin cycle"

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def kit_memory():
    """Load the kit table and articles once for the module (the load embeds ~230 texts)."""
    no_siis.forget_all()
    no_siis.prewarm()
    yield


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


def ask(query, siis=None, **extra):
    body = {"query": query, **extra}
    if siis is not None or "siis_response" in extra:
        body["siis_response"] = siis
    r = client.post("/v1/troubleshoot", json=body)
    assert r.status_code == 200
    ContextDeeplinkResponse.model_validate(r.json())
    return r.json()


@pytest.mark.parametrize(
    "siis",
    [None, "", "   \n\t ", {}, {"title": "Black screen"}, {"title": "x", "content": ""}, {"content": None}],
    ids=["null", "empty-string", "whitespace", "empty-object", "title-only", "empty-content", "null-content"],
)
def test_every_kind_of_missing_article_is_one_case(siis):
    assert clean_siis(siis) == ("", None)
    body = ask(UNRELATED, siis)
    assert body["contexts"] == [] and body["meta"]["fallback"] == "no_siis_context"
    assert body["meta"]["source"] is None


def test_missing_key_is_the_same_as_null():
    body = ask(UNRELATED)
    assert body["contexts"] == [] and body["meta"]["fallback"] == "no_siis_context"


def test_both_article_formats_are_accepted():
    row = KIT[0]
    as_object = ask(row["original_query"], row["siis_response"])
    cache.clear()
    as_string = ask(row["original_query"], row["siis_response"]["content"])
    assert as_object["contexts"] and as_object["contexts"] == as_string["contexts"]
    assert as_object["meta"]["fallback"] is None


def test_a_kit_question_without_its_article_gets_its_real_plan():
    line = RESULTS[0]
    body = ask(line["query"])
    assert body["meta"]["cache_hit"] and body["meta"]["source"] == "cached_plan"
    assert body["meta"]["fallback"] == "no_siis_context"
    assert body["contexts"] == line["response"]["contexts"]
    assert body["meta"]["reason"] == "no_article_cached_plan"
    assert body["meta"]["message"].startswith("No support article was provided.")


def test_a_stored_variation_without_an_article_hits_by_meaning():
    line = RESULTS[1]
    body = ask(line["query_variations"][0])
    assert body["meta"]["source"] == "cached_plan" and body["contexts"] == line["response"]["contexts"]


def test_a_plan_solved_with_an_article_answers_the_same_question_without_one():
    row = next(r for r in KIT if r["id"] == "row_21")
    solved = ask("my phone screen keeps lagging when I type, touch feels delayed", row["siis_response"])
    assert solved["meta"]["cache_hit"] is False
    again = ask("my phone screen keeps lagging when I type, touch feels delayed")
    assert again["meta"]["cache_hit"] and again["meta"]["source"] == "cached_plan"
    assert again["contexts"] == solved["contexts"]


def test_a_question_close_to_a_remembered_article_runs_the_pipeline_on_it():
    kids = next(r for r in KIT if "Samsung Kids PIN" in r["siis_response"]["content"])
    body = ask("How to reset the Samsung Kids PIN")
    assert body["meta"]["source"] == "retrieved_article" and body["meta"]["fallback"] == "no_siis_context"
    assert body["contexts"] and all(g["score"] < 1.0 for g in body["contexts"])
    clean, _ = clean_siis(kids["siis_response"])
    words = set(clean.lower().split())
    for goal in body["contexts"]:
        for action in goal["actions"]:
            for step in action["stepGroups"][0]["steps"]:
                assert set(step.lower().rstrip(".").split()) & words, step  # every step from that article
    repeat = ask("How to reset the Samsung Kids PIN")
    assert repeat["meta"]["cache_hit"] and repeat["contexts"] == body["contexts"]


def test_nothing_close_means_empty_never_generated():
    for query in (UNRELATED, "my galaxy camera app crashes when I switch to night mode", ""):
        body = ask(query)
        assert body["contexts"] == [] and body["meta"]["fallback"] == "no_siis_context"
        assert (
            body["meta"]["reason"] == "no_article" and "Attach the support article" in body["meta"]["message"]
        )


def test_an_article_the_api_received_is_remembered_for_later():
    article = {
        "title": "Resetting the refrigerator water filter indicator",
        "content": "# Water Filter Indicator\n## Reset the Indicator\nPress and hold the Filter Reset button "
        "for three seconds until the water filter indicator light turns off.\n",
    }
    ask("the water filter light on my fridge will not turn off", article)
    no_siis._background.submit(lambda: None).result()  # the memory write runs off the request path
    found = no_siis.find_article("reset the refrigerator water filter indicator light")
    assert found is not None and found.title == article["title"]
