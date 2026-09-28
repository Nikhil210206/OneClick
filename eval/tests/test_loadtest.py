"""loadtest.py without an engine or network: hashing, summaries, target verdicts, and API mode against a
faked client, including how near-miss hits are classified."""

import loadtest


def test_siis_hash_is_stable_and_order_independent():
    assert loadtest.siis_hash({"a": 1, "b": 2}) == loadtest.siis_hash({"b": 2, "a": 1})
    assert loadtest.siis_hash({"a": 1}) != loadtest.siis_hash({"a": 2})
    assert loadtest.siis_hash(None) is None


def test_summarise_flags_small_samples():
    s = loadtest.summarise([1.0, 2.0, 3.0], hits=2, n=3)
    assert s["hit_rate"] == round(2 / 3, 3)
    assert s["p50_ms"] == 2.0
    assert not s["enough_samples"]
    assert loadtest.summarise([], 0, 0)["hit_rate"] is None


def test_verdicts_against_the_spec_targets():
    section = {
        "repeat": {"p95_ms": 0.3},
        "paraphrase": {"p95_ms": 12.0, "hit_rate": 0.805},
        "near_miss": {"false_hit_rate": 0.3},
    }
    v = loadtest.verdicts(section)
    assert v["repeat_p95"] and v["paraphrase_p95"] and v["paraphrase_hit"]
    assert v["false_hits"] is False
    assert v["cold_p95"] is None  # not measured is not a pass


def test_api_mode_records_cost_and_models(monkeypatch):
    from evalkit import client as client_mod
    from evalkit.client import CallResult
    from evalkit.sets import KitRow

    def fake_call(self, method, path, payload=None):
        if path == "/health":
            return CallResult(200, 1.0, body={"status": "ok"}, pure_json=True)
        meta = {"model": "ministral-14b-latest", "cost_usd": 0.0}
        return CallResult(
            200, 5.0, server_ms=4.0, cache_hit=False, body={"contexts": [], "meta": meta}, pure_json=True
        )

    monkeypatch.setattr(client_mod.ApiClient, "_call", fake_call)
    kit = [KitRow("r1", "q one", {"content": "a"}), KitRow("r2", "q two", {"content": "b"})]
    section = loadtest.run_api("http://x", kit, [], [], [])
    assert section["cold"]["mean_cost_usd"] == 0.0
    assert section["cold"]["models"] == {"ministral-14b-latest": 2}
    assert section["repeat"]["n"] == 4


def test_api_mode_times_cold_on_misses_and_hits_on_hits(monkeypatch):
    from evalkit import client as client_mod
    from evalkit.client import CallResult
    from evalkit.sets import KitRow

    seen: set = set()

    def fake_call(self, method, path, payload=None):
        if path == "/health":
            return CallResult(200, 1.0, body={"status": "ok"}, pure_json=True)
        # The second kit row shares an article and hits the first row's plan: 2 ms instead of 5000.
        hit = payload["query"] in seen or payload["query"] == "q two"
        seen.add(payload["query"])
        ms = 2.0 if hit else 5000.0
        body = {"contexts": [], "meta": {"model": None if hit else "ministral-14b-latest", "cost_usd": 0.0}}
        return CallResult(200, ms, server_ms=ms, cache_hit=hit, body=body, pure_json=True)

    monkeypatch.setattr(client_mod.ApiClient, "_call", fake_call)
    kit = [KitRow("r1", "q one", {"content": "a"}), KitRow("r2", "q two", {"content": "a"})]
    section = loadtest.run_api("http://x", kit, [], [], [])
    cold = section["cold"]
    assert cold["timed_n"] == 1 and cold["p95_ms"] == 5000.0
    assert cold["cold_pass"]["n"] == 2 and cold["cold_pass"]["timed_n"] == 1
    assert cold["models"] == {"ministral-14b-latest": 1}
    assert section["repeat"]["p95_ms"] == 2.0
    assert any("answered from the cache" in n for n in section["notes"])


def test_api_mode_pools_every_miss_checks_plans_and_reads_tokens(monkeypatch):
    from evalkit import client as client_mod
    from evalkit.client import CallResult
    from evalkit.sets import KitRow

    plans = {"q one": [{"title": "one"}], "q two": [{"title": "two"}]}
    served = {"para right": "q one", "para wrong": "q two"}  # paraphrases of r1 that hit a plan
    traces = []

    def fake_call(self, method, path, payload=None):
        if path == "/health":
            return CallResult(200, 1.0, body={"status": "ok"}, pure_json=True)
        if path.startswith("/v1/trace/"):
            traces.append(path)
            return CallResult(200, 1.0, body={"tokens_in": 900, "tokens_out": 100}, pure_json=True)
        q = payload["query"]
        hit = q in served
        plan = plans.get(served.get(q, q), [{"title": q}])
        body = {
            "contexts": plan,
            "meta": {"model": None if hit else "m", "cost_usd": 0.0, "trace_id": f"t_{q}"},
        }
        return CallResult(200, 5.0, server_ms=5.0, cache_hit=hit, body=body, pure_json=True)

    monkeypatch.setattr(client_mod.ApiClient, "_call", fake_call)
    kit = [KitRow("r1", "q one", {"content": "a"}), KitRow("r2", "q two", {"content": "a"})]
    para = [
        {"row_id": "r1", "query": "para right", "register": "casual"},
        {"row_id": "r1", "query": "para wrong", "register": "casual"},
        {"row_id": "r1", "query": "para miss", "register": "typo"},
    ]
    near = [{"id": "n1", "row_id": "r2", "query": "near miss", "differs_in": "intent"}]
    section = loadtest.run_api("http://x", kit, [], para, near)
    cold, p = section["cold"], section["paraphrase"]
    assert cold["sources"] == {"cold_pass": 2, "paraphrase_misses": 1, "near_miss_misses": 1}
    assert cold["timed_n"] == 4 and cold["tokens_measured"] == 4
    assert cold["mean_tokens_in"] == 900.0 and cold["mean_tokens_out"] == 100.0
    assert len(traces) == 4  # only misses are traced
    assert p["wrong_plan"] == 1 and p["same_plan"] == 1
    assert p["hits_by_source"] == {"own_kit_answer": 1, "other_kit_answer": 1}
    assert p["wrong"] == [{"id": None, "row_id": "r1", "query": "para wrong", "source": "other_kit_answer"}]
    assert p["by_register"] == {"casual": 1.0, "typo": 0.0}


def test_paraphrase_hits_are_classified_by_the_plan_they_served():
    from evalkit.sets import KitRow

    kit = [KitRow("r1", "q1", {"content": "a"}), KitRow("r2", "q2", {"content": "a"})]
    plan_r1, plan_r2 = [{"title": "one"}], [{"title": "two"}]
    kit_cold = [_res(plan_r1, False), _res(plan_r2, False)]
    paraphrases = [{"id": f"p{i}", "row_id": row, "query": f"q{i}"} for i, row in enumerate("1112221", 1)]
    paraphrases = [{**p, "row_id": f"r{p['row_id']}"} for p in paraphrases]
    para = [
        _res(plan_r1, True),  # p1 r1: its own kit answer
        _res([{"title": "p2"}], False),  # p2 r1: misses, runs cold, is stored
        _res([{"title": "p2"}], True),  # p3 r1: hits p2's answer, same complaint
        _res(plan_r1, True),  # p4 r2: r1's kit answer: wrong
        _res([{"title": "p2"}], True),  # p5 r2: r1's paraphrase answer: wrong
        _res([{"title": "?"}], True),  # p6 r2: nothing known served this: wrong
        _res(plan_r1, False),  # p7 r1: missed but recomputed the same plan
    ]
    served, wrong, same = loadtest.classify_paraphrases(paraphrases, para, kit, kit_cold)
    assert served == {
        "own_kit_answer": 1,
        "same_complaint_paraphrase": 1,
        "other_kit_answer": 1,
        "other_complaint_paraphrase": 1,
        "unidentified": 1,
    }
    assert [w["id"] for w in wrong] == ["p4", "p5", "p6"]
    assert same == 2  # p1 and p7


def test_api_mode_lists_leaked_near_misses(monkeypatch):
    from evalkit import client as client_mod
    from evalkit.client import CallResult
    from evalkit.sets import KitRow

    def fake_call(self, method, path, payload=None):
        if path == "/health":
            return CallResult(200, 1.0, body={"status": "ok"}, pure_json=True)
        hit = payload["query"] == "leak"
        return CallResult(
            200, 1.0, cache_hit=hit, cache_tier="semantic" if hit else None, body={}, pure_json=True
        )

    monkeypatch.setattr(client_mod.ApiClient, "_call", fake_call)
    kit = [KitRow("r1", "q", {"content": "a"})]
    near = [
        {"id": "nm_1", "row_id": "r1", "query": "leak", "differs_in": "intent"},
        {"id": "nm_2", "row_id": "r1", "query": "fine", "differs_in": "symptom"},
    ]
    nm = loadtest.run_api("http://x", kit, [], [], near)["near_miss"]
    assert nm["leaked"] == [
        {"id": "nm_1", "query": "leak", "differs_in": "intent", "tier": "semantic", "source": "unidentified"}
    ]
    assert nm["any_hit_rate"] == 0.5
    assert nm["false_hits"] == 0 and nm["false_hit_rate"] == 0.0  # not a kit answer


def _res(plan, hit):
    from evalkit.client import CallResult

    return CallResult(
        200, 1.0, cache_hit=hit, body={"contexts": plan} if plan else {"contexts": []}, pure_json=True
    )


def test_near_miss_hits_are_classified_by_the_plan_they_served():
    from evalkit.sets import KitRow

    kit = [KitRow("r1", "q1", {"content": "a"}), KitRow("r2", "q2", {"content": "a"})]
    plan_r1, plan_r2 = [{"title": "one"}], [{"title": "two"}]
    kit_cold = [_res(plan_r1, False), _res(plan_r2, False)]
    para = [("r1", _res([{"title": "para"}], False))]
    near_misses = [
        {"id": "a", "row_id": "r1", "query": "x", "differs_in": "intent"},  # own kit answer
        {"id": "b", "row_id": "r1", "query": "y", "differs_in": "symptom"},  # misses, runs cold, is cached
        {"id": "c", "row_id": "r2", "query": "z", "differs_in": "symptom"},  # hits b's answer
        {"id": "d", "row_id": "r2", "query": "w", "differs_in": "component"},  # r1's kit answer
        {"id": "e", "row_id": "r1", "query": "v", "differs_in": "intent"},  # a paraphrase's cold answer
        {"id": "f", "row_id": "r1", "query": "u", "differs_in": "intent"},  # hit with an empty plan
    ]
    near = [
        _res(plan_r1, True),
        _res([{"title": "b"}], False),
        _res([{"title": "b"}], True),
        _res(plan_r1, True),
        _res([{"title": "para"}], True),
        _res([], True),
    ]
    leaked, by = loadtest.classify_near_misses(near_misses, near, kit, kit_cold, para)
    assert [x["source"] for x in leaked] == [
        "own_kit_answer",
        "earlier_near_miss",
        "other_kit_answer",
        "paraphrase_answer",
        "unidentified",
    ]
    assert by == {
        "own_kit_answer": 1,
        "earlier_near_miss": 1,
        "other_kit_answer": 1,
        "paraphrase_answer": 1,
        "unidentified": 1,
    }
