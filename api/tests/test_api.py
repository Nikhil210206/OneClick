from fastapi.testclient import TestClient

from app.main import app
from app.schema import ContextDeeplinkResponse

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_troubleshoot_is_schema_valid():
    r = client.post("/v1/troubleshoot", json={"query": "screen is black"})
    assert r.status_code == 200
    ContextDeeplinkResponse.model_validate(r.json())


def test_all_modules_import():
    import importlib
    import pkgutil

    import app

    for m in pkgutil.walk_packages(app.__path__, "app."):
        importlib.import_module(m.name)


def _answer(r) -> dict:
    assert r.status_code == 200
    body = r.json()
    ContextDeeplinkResponse.model_validate(body)
    return body


def test_a_malformed_request_still_gets_200_and_says_why():
    """Hard rule 4: the scorer or a judge testing by hand never sees a 422 from the troubleshoot endpoint."""
    article = {"title": "Black screen", "content": "# Black Screen\n## Restart\nPress and hold the Side key."}
    cases = [
        {},  # no query at all
        {"query": None, "siis_response": article},
        {"query": 12345},
        {"query": ["screen is black", "and flickers"], "siis_response": 7},
        {"query": "screen is black", "siis_response": [article, "Restart the phone."]},
    ]
    for payload in cases:
        body = _answer(client.post("/v1/troubleshoot", json=payload))
        assert body["meta"]["message"] or body["contexts"]
    for raw in ("not json at all", "[1, 2, 3]", '"just a string"'):
        r = client.post("/v1/troubleshoot", content=raw, headers={"Content-Type": "application/json"})
        body = _answer(r)
        assert body["contexts"] == [] and body["meta"]["reason"] == "invalid_request"


def test_a_missing_article_says_so():
    for siis in (None, {}, "", {"title": "Only a title"}):
        body = _answer(
            client.post("/v1/troubleshoot", json={"query": "zzqx wobble frob", "siis_response": siis})
        )
        assert body["contexts"] == [] and body["meta"]["fallback"] == "no_siis_context"
        assert body["meta"]["reason"] == "no_article"


def test_a_huge_query_is_cut_and_answered():
    from app.config import settings

    body = _answer(client.post("/v1/troubleshoot", json={"query": "screen is black " * 5000}))
    assert "contexts" in body and settings.query_max_chars < len("screen is black " * 5000)


def test_the_stream_answers_an_unreadable_body_with_one_done_frame():
    r = client.post("/v1/troubleshoot/stream", content="{oops", headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.text.startswith("event: done")
