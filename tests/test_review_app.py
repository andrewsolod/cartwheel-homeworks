"""HW4 review app: conversation joins, label files, and the JSON API (offline)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from analysis.helpers import tools
from analysis.helpers.selection import load_traces
from analysis.review_app import data, server

REPO = Path(__file__).resolve().parent.parent
EXPORT = REPO / "traces/support_traces.json"
SCENARIOS = REPO / "scenarios/support_scenarios.jsonl"


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CARTWHEEL_ANALYSIS_STATE", str(tmp_path))
    return tmp_path


@pytest.fixture(scope="module")
def conversations():
    scen = data.load_scenarios(SCENARIOS)
    return data.build_conversations(data.traces_from_export(EXPORT), scen)


def test_conversations_group_turns_and_join_with_the_course_loader(conversations):
    assert len(conversations) == 250
    assert sum(len(c["turns"]) for c in conversations) == 322
    assert len({c["scenario_id"] for c in conversations}) == 250
    # HW5 helpers key conversations by load_traces ids; labels must join.
    assert {c["id"] for c in conversations} == {t["id"] for t in load_traces(EXPORT)}
    multi = [c for c in conversations if len(c["turns"]) > 1]
    assert multi
    for c in multi:
        stamps = [t["timestamp"] for t in c["turns"]]
        assert stamps == sorted(stamps)
        assert c["id"] == c["turns"][0]["trace_id"]
        assert all(t["url"].endswith(t["trace_id"]) for t in c["turns"])


def test_conversations_carry_scenario_and_tool_status(conversations):
    c = next(c for c in conversations if c["scenario_id"] == "support-0143")
    assert c["role"] == "support" and c["expected"]["outcome"]
    calls = [m for t in c["turns"] for m in t["messages"] if m["role"] == "tool_call"]
    results = [m for t in c["turns"] for m in t["messages"] if m["role"] == "tool_result"]
    assert calls[0]["name"] == "search_products"
    assert results[0]["status"]["ok"] is True


def test_conversations_carry_writer_brief_and_agent_system_prompt(conversations):
    assert all(c["writer_brief"] for c in conversations)
    assert all(c["system_prompt"] and c["system_prompt"].startswith("You are Cartwheel") for c in conversations)
    c = next(c for c in conversations if c["scenario_id"] == "support-0006")
    assert "- User role: merchant" in c["system_prompt"]
    # One template; only the injected session lines differ between users.
    assert len({c["prompt_template_id"] for c in conversations}) == 1
    assert c["first_message"].startswith("Customer requests refund on order 2259")


def test_record_facts_come_from_the_seed_copy(world):
    scen = data.load_scenarios(SCENARIOS)
    with_order = next(s for s in scen.values() if s["tuple"].get("order_id"))
    text = data.facts_reader(world["db"])(with_order)
    assert text.startswith(f"order {with_order['tuple']['order_id']}:")


def test_label_flip_keeps_history_and_matches_the_hw5_reader(state_dir):
    first = server.save_label("demo_mode", "t1", 1, "reply claims refund done")
    assert server.save_label("demo_mode", "t1", 1, "reply claims refund done") == first
    second = server.save_label("demo_mode", "t1", 0, None)
    server.save_label("demo_mode", "t2", 0, None)
    rows = [json.loads(l) for l in (state_dir / "labels/demo_mode.jsonl").read_text().splitlines()]
    assert len(rows) == 3
    assert rows[0]["superseded_by"] == second["label_id"]
    live = {r["trace_id"]: r["label"] for r in tools._load_labels("demo_mode")}
    assert live == {"t1": 0, "t2": 0}
    assert server.live_labels()["demo_mode"]["t1"]["label"] == 0
    with pytest.raises(ValueError):
        server.save_label("demo_mode", "t1", 2, None)


def test_batch1_is_deterministic_distinct_and_nearest_to_centroid(conversations, state_dir):
    from analysis.review_app import batches

    a = batches.batch1(conversations, seed=1)
    b = batches.batch1(conversations, seed=1)
    assert [p["trace_id"] for p in a["picks"]] == [p["trace_id"] for p in b["picks"]]
    ids = [p["trace_id"] for p in a["picks"]]
    assert len(ids) == len(set(ids)) == 30
    assert sum(p["method"] == "cluster" for p in a["picks"]) == 15
    # Each cluster's first pick is its nearest member.
    firsts = [p for p in a["picks"] if p.get("reason", "").endswith(")") and "#1" in p["reason"]]
    assert len(firsts) == len(a["clusters"])
    batches.add_batch(a)
    with pytest.raises(SystemExit):
        batches.add_batch(a)


def test_batch2_takes_ten_per_role_and_skips_batch1(conversations, state_dir):
    from analysis.review_app import batches

    batches.add_batch(batches.batch1(conversations, seed=1))
    b2 = batches.by_role(conversations, 10, seed=1, exclude=batches.used_ids())
    roles = {c["id"]: c["role"] for c in conversations}
    assert sorted(roles[p["trace_id"]] for p in b2["picks"]) == ["merchant"] * 10 + ["shopper"] * 10 + ["support"] * 10
    batches.add_batch(b2)  # refuses on any overlap with batch 1
    assert len(batches.used_ids()) == 60


def test_batch3_depth_search_fills_quotas_without_reuse(conversations, state_dir):
    from analysis.review_app import batches

    batches.add_batch(batches.batch1(conversations, seed=1))
    b3 = batches.depth(conversations, seed=1, exclude=batches.used_ids())
    assert len(b3["picks"]) == sum(batches.DEPTH_QUOTA.values()) == 25
    by_id = {c["id"]: c for c in conversations}
    for p in b3["picks"]:
        assert batches.DEPTH_FILTERS[p["mode"]][1](by_id[p["trace_id"]])
    batches.add_batch(b3)


def test_wrong_policy_search_flags_a_policy_other_than_the_applicable_one(conversations):
    from analysis.review_app import search

    by_sid = {c["scenario_id"]: c for c in conversations}
    assert "cw-disputes" in search.wrong_policy(by_sid["support-0072"])  # dispute answered with cw-returns
    assert search.wrong_policy(by_sid["support-0057"]) is None  # cites cw-disputes as required


def test_proposals_cover_every_reviewed_cell_and_keep_coded_evidence(conversations):
    from analysis.review_app import proposals

    convs = conversations[:12]
    pats = {"modes": [{"name": n, "positives": [convs[0]["id"]], "close_negatives": [convs[1]["id"]]}
                      for n in proposals.RULES]}
    import analysis.review_app.search as search
    orig = search.reviewed
    search.reviewed = lambda cs: convs
    try:
        props = proposals.propose(conversations, pats)
    finally:
        search.reviewed = orig
    assert len(props) == len(convs) * len(proposals.RULES)
    first = [p for p in props if p["trace_id"] == convs[0]["id"]]
    assert all(p["basis"] == "coded_positive" and p["proposed"] == 1 for p in first)
    assert all(p["basis"] == "close_negative" for p in props if p["trace_id"] == convs[1]["id"])


@pytest.fixture()
def http(state_dir, monkeypatch):
    monkeypatch.setitem(server.POOL, "conversations", [{"id": "c1", "scenario_id": "s1"}])
    monkeypatch.setitem(server.POOL, "info", {"source": "test"})
    monkeypatch.setitem(server.POOL, "langfuse", False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.ReviewHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _call(url, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def test_api_defaults_labels_and_rejected_suggestions(http, state_dir):
    assert _call(http + "/api/annotations") == {"annotations": []}
    assert _call(http + "/api/manifest") == {"batches": []}
    assert _call(http + "/api/conversations")[0]["id"] == "c1"
    out = _call(http + "/api/labels", {"mode": "m", "trace_id": "c1", "label": 1, "evidence": "x"})
    assert out["ok"] and out["langfuse_score"] is False
    assert _call(http + "/api/labels")["m"]["c1"]["label"] == 1
    rejected = [{"id": "s1", "trace_id": "c1", "mode": "m", "status": "rejected", "decision_reason": "tool did confirm"}]
    _call(http + "/api/suggestions", rejected)
    assert json.loads((state_dir / "suggestions.json").read_text()) == rejected
    with pytest.raises(urllib.error.HTTPError) as err:
        _call(http + "/api/labels", {"mode": "m", "trace_id": "c1", "label": 3})
    assert err.value.code == 400
    out = _call(http + "/api/labels/bulk", {"labels": [{"mode": "m", "trace_id": "c1", "label": 0, "evidence": "absent"},
                                                     {"mode": "n", "trace_id": "c1", "label": 1}]})
    assert out["saved"] == 2 and out["langfuse_scores"] == 0
    live = _call(http + "/api/labels")
    assert live["m"]["c1"]["label"] == 0 and live["n"]["c1"]["label"] == 1
    with pytest.raises(urllib.error.HTTPError) as err:
        _call(http + "/api/labels/bulk", {"labels": [{"mode": "m", "trace_id": "c1", "label": 2}]})
    assert err.value.code == 400
    assert _call(http + "/api/proposals") == []
