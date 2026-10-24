"""Task-scoped expert planning without a persisted Skill graph."""

import json

import pytest

from openjiuwen.symphony import ExpertCandidate, OrchestrationPlan, plan_experts


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
            "nodes": [
                {"id": "a", "task": "整理资料", "summary": "梳理报告所需资料与关键依据"},
                {"id": "b", "task": "根据资料审阅", "summary": "核对资料并审阅报告结论"},
            ],
            "edges": [{"source": "a", "target": "b"}],
        }
    )

    plan = await plan_experts("完成报告", _candidates("a", "b"), model)

    assert len(model.calls) == 1
    assert model.calls[0]["timeout"] == 60
    assert "用日常中文说清这位专家要做什么" in model.calls[0]["messages"][0]["content"]
    assert "避免使用 SWOT、GTM 等未解释的缩写或专业术语" in model.calls[0]["messages"][0]["content"]
    assert json.loads(model.calls[0]["messages"][1]["content"])["query"] == "完成报告"
    assert plan["status"] == "ready"
    graph = plan["graph"]
    assert graph["type"] == "planned_graph" and graph["directed"] is True
    assert graph["metadata"]["leader"]["id"] == "xiaoyiwork"
    assert graph["nodes"]["a"]["task"] == "整理资料"
    assert "summary" not in graph["nodes"]["a"]
    assert plan["display_summaries"] == {
        "a": "梳理报告所需资料与关键依据",
        "b": "核对资料并审阅报告结论",
    }
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
    assert "display_summaries" not in plan


@pytest.mark.asyncio
async def test_invalid_display_summaries_do_not_change_executable_plan():
    model = FakeModel(
        {
            "status": "ready",
            "nodes": [
                {"id": "a", "task": "完整任务甲", "summary": "  分析  市场机会  "},
                {"id": "b", "task": "完整任务乙", "summary": "乙" * 81},
                {"id": "c", "task": "完整任务丙", "summary": 42},
            ],
            "edges": [],
        }
    )

    plan = await plan_experts("完成报告", _candidates("a", "b", "c"), model)

    assert len(model.calls) == 1
    assert plan["display_summaries"] == {"a": "分析 市场机会"}
    assert [plan["graph"]["nodes"][key]["task"] for key in ("a", "b", "c")] == [
        "完整任务甲",
        "完整任务乙",
        "完整任务丙",
    ]
    assert all("summary" not in node for node in plan["graph"]["nodes"].values())


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


@pytest.mark.asyncio
async def test_seven_experts_and_duplicate_edges_preserve_mapping_contract():
    candidate_ids = [str(index) for index in range(7)]
    model = FakeModel(
        {
            "status": "ready",
            "nodes": [{"id": expert_id, "task": " 整理资料 "} for expert_id in candidate_ids],
            "edges": [{"source": "0", "target": "1"}, {"source": "0", "target": "1"}],
        }
    )

    plan = await plan_experts(" 完成报告 ", _candidates(*candidate_ids), model, timeout=12.5)

    assert isinstance(plan, OrchestrationPlan)
    assert isinstance(plan, dict)
    result = plan.to_dict()
    assert result == plan and result is not plan
    graph = result["graph"]
    assert len(graph["id"]) == 32
    assert len(graph["nodes"]) == 7
    assert all(node["task"] == "整理资料" for node in graph["nodes"].values())
    assert graph["edges"] == [{"source": "0", "target": "1", "relation": "can_feed"}]
    assert graph["metadata"] == {
        "status": "ready",
        "missing_inputs": [],
        "leader": {"id": "xiaoyiwork", "label": "小艺Work", "role": "lead"},
    }
    assert len(model.calls) == 1
    assert model.calls[0]["timeout"] == 12.5
    content = json.loads(model.calls[0]["messages"][1]["content"])
    assert content["query"] == "完成报告"
    assert all(expert["skills"] == [] for expert in content["experts"])


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [None, "", " \t"])
async def test_invalid_query_is_rejected_before_model_call(query):
    model = FakeModel({"status": "no_plan"})
    with pytest.raises(ValueError, match="non-empty query"):
        await plan_experts(query, _candidates("a"), model)
    assert model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidates",
    [[{"id": "a"}], [ExpertCandidate(" ", "专家", "职责")], [ExpertCandidate("a", " ", "职责")]],
)
async def test_invalid_candidate_is_rejected_before_model_call(candidates):
    model = FakeModel({"status": "no_plan"})
    with pytest.raises(ValueError, match="expert candidates"):
        await plan_experts("整理资料", candidates, model)
    assert model.calls == []


@pytest.mark.asyncio
async def test_model_is_required_only_when_candidates_are_available():
    assert await plan_experts("整理资料", [], None) == {"status": "no_plan"}
    with pytest.raises(ValueError, match="current request model"):
        await plan_experts("整理资料", _candidates("a"), None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"status": "ready", "nodes": [{"id": "a", "task": " "}], "edges": []},
        {"status": "ready", "nodes": [{"id": "a", "task": "A"}], "edges": None},
        {"status": "ready", "nodes": [{"id": "a", "task": "A"}], "edges": [{"source": "a", "target": "b"}]},
    ],
)
async def test_invalid_plan_structure_is_rejected(payload):
    with pytest.raises(ValueError):
        await plan_experts("整理资料", _candidates("a", "b"), FakeModel(payload))
