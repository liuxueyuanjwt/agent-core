"""Task-scoped expert planning without a persisted Skill graph."""

import json

import pytest

from openjiuwen.symphony import ExpertCandidate, plan_experts


def _candidates(*ids: str) -> list[ExpertCandidate]:
    return [ExpertCandidate(id=expert_id, name=f"专家{expert_id}", description=f"职责{expert_id}") for expert_id in ids]


class FakeModel:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return {"content": json.dumps(self.payload, ensure_ascii=False)}


@pytest.mark.asyncio
async def test_ready_plan_uses_selected_model_and_builds_dependency_graph():
    model = FakeModel(
        {
            "status": "ready",
            "nodes": [{"id": "a", "task": "整理资料"}, {"id": "b", "task": "根据资料审阅"}],
            "edges": [{"source": "a", "target": "b"}],
        }
    )

    plan = await plan_experts("完成报告", _candidates("a", "b"), model)

    assert len(model.calls) == 1
    assert model.calls[0]["timeout"] == 60
    assert json.loads(model.calls[0]["messages"][1]["content"])["query"] == "完成报告"
    assert plan["status"] == "ready"
    graph = plan["graph"]
    assert graph["type"] == "planned_graph" and graph["directed"] is True
    assert graph["metadata"]["leader"]["id"] == "xiaoyiwork"
    assert graph["nodes"]["a"]["task"] == "整理资料"
    assert graph["edges"] == [{"source": "a", "target": "b", "relation": "can_feed"}]


@pytest.mark.asyncio
async def test_simple_task_does_not_create_team():
    model = FakeModel({"status": "no_plan"})
    assert await plan_experts("你好", _candidates("a", "b"), model) == {"status": "no_plan"}
    assert await plan_experts("你好", _candidates("a"), model) == {"status": "no_plan"}
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_no_candidates_skips_model_call():
    model = FakeModel({"status": "ready"})
    assert await plan_experts("整理资料", [], model) == {"status": "no_plan"}
    assert model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_ids", [("a",), ("a", "b")])
async def test_one_selected_expert_produces_ready_plan(candidate_ids):
    model = FakeModel({"status": "ready", "nodes": [{"id": "a", "task": "整理资料"}], "edges": []})

    plan = await plan_experts("整理资料", _candidates(*candidate_ids), model)

    assert len(model.calls) == 1
    assert plan["status"] == "ready"
    graph = plan["graph"]
    assert graph["type"] == "planned_graph" and graph["directed"] is True
    assert graph["metadata"]["leader"]["id"] == "xiaoyiwork"
    assert graph["nodes"] == {"a": {"label": "专家a", "description": "职责a", "task": "整理资料"}}
    assert graph["edges"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("node_count", [0, 8])
async def test_empty_or_oversized_ready_plan_is_rejected(node_count):
    candidate_ids = [str(index) for index in range(8)]
    model = FakeModel(
        {
            "status": "ready",
            "nodes": [{"id": expert_id, "task": "整理资料"} for expert_id in candidate_ids[:node_count]],
            "edges": [],
        }
    )
    with pytest.raises(ValueError, match="between one and seven"):
        await plan_experts("整理资料", _candidates(*candidate_ids), model)


@pytest.mark.asyncio
async def test_missing_information_and_legacy_status_return_no_plan():
    model = FakeModel({"status": "no_plan"})
    assert await plan_experts("安排行程", _candidates("a", "b"), model) == {"status": "no_plan"}
    model.payload = {"status": "needs_input", "question": "请提供预算"}
    assert await plan_experts("安排行程", _candidates("a", "b"), model) == {"status": "no_plan"}
    model.payload = {"status": "unexpected"}
    with pytest.raises(ValueError, match="status"):
        await plan_experts("安排行程", _candidates("a", "b"), model)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ready", "nodes": [{"id": "a", "task": "A"}, {"id": "missing", "task": "B"}], "edges": []},
        {"status": "ready", "nodes": [{"id": "a", "task": "A"}, {"id": "a", "task": "B"}], "edges": []},
        {
            "status": "ready",
            "nodes": [{"id": "a", "task": "A"}],
            "edges": [{"source": "a", "target": "a"}],
        },
        {
            "status": "ready",
            "nodes": [{"id": "a", "task": "A"}, {"id": "b", "task": "B"}],
            "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
        },
    ],
)
async def test_invalid_nodes_or_cycles_are_rejected(payload):
    with pytest.raises(ValueError):
        await plan_experts("跨领域任务", _candidates("a", "b"), FakeModel(payload))


@pytest.mark.asyncio
async def test_duplicate_candidate_ids_are_rejected_before_model_call():
    model = FakeModel({"status": "no_plan"})
    with pytest.raises(ValueError, match="unique"):
        await plan_experts("任务", _candidates("a", "a"), model)
    assert model.calls == []
