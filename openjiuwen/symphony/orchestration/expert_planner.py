"""Task-scoped planning for a caller-supplied set of expert capabilities.

The planner knows nothing about expert packages or agent execution. Integrators
discover candidates, execute the returned graph, and own its lifetime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence
from uuid import uuid4

from openjiuwen.core.foundation.llm import Model
from openjiuwen.symphony.orchestration.contracts import OrchestrationPlan
from openjiuwen.symphony.orchestration.model import invoke_json

MAX_EXPERTS = 7

_PLAN_PROMPT = """你为主助手规划本轮专家协作；主助手负责执行、协调和最终答复。仅返回 JSON 对象。
依据用户任务与候选专家的职责和技能，判断是否适合交给专业专家处理。
普通问答、无需专家或没有合适候选时返回 no_plan；不要为了组团增加专家。
信息不足且无法合理规划时返回 no_plan，由主助手在普通对话中决定是否追问用户。
需要专家时返回 ready，选择完成任务所需的最少专家（1 至 7 位），为每位写具体任务。
一位专家足够时只选一位，edges 返回空数组；主助手是主理人，不计入所选专家人数。
多位专家时用有向边表达前置专家结果流向后续专家。只能使用给定 id，不得形成环。
格式：{"status":"ready|no_plan","nodes":[{"id":"专家id","task":"具体任务"}],
"edges":[{"source":"专家id","target":"专家id"}]}。"""


@dataclass(frozen=True)
class ExpertCandidate:
    """An expert's model-visible capability, without a package or runtime handle."""

    id: str
    name: str
    description: str
    skills: tuple[str, ...] = ()


def _validate_candidates(candidates: Sequence[ExpertCandidate]) -> dict[str, ExpertCandidate]:
    indexed: dict[str, ExpertCandidate] = {}
    for candidate in candidates:
        if not isinstance(candidate, ExpertCandidate):
            raise ValueError("expert candidates must be ExpertCandidate objects")
        if not candidate.id.strip() or not candidate.name.strip() or candidate.id in indexed:
            raise ValueError("expert candidates need unique, non-empty ids and names")
        indexed[candidate.id] = candidate
    return indexed


# Keep candidate, edge, and DAG validation together before constructing the public graph.
# pylint: disable-next=too-many-locals
def _validate_plan(raw: Any, candidates: dict[str, ExpertCandidate]) -> OrchestrationPlan:
    if not isinstance(raw, dict):
        raise ValueError("expert plan must be a JSON object")
    status = raw.get("status")
    # Older models may still produce the previous status; never expose it to callers.
    if status in {"no_plan", "needs_input"}:
        return OrchestrationPlan({"status": "no_plan"})
    if status != "ready":
        raise ValueError("invalid expert plan status")

    raw_nodes, raw_edges = raw.get("nodes"), raw.get("edges")
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= MAX_EXPERTS:
        raise ValueError("expert plan must select between one and seven experts")
    if not isinstance(raw_edges, list):
        raise ValueError("expert plan edges must be a list")

    nodes: dict[str, dict[str, str]] = {}
    for item in raw_nodes:
        if not isinstance(item, dict):
            raise ValueError("invalid expert plan node")
        expert_id, task = item.get("id"), item.get("task")
        if (
            not isinstance(expert_id, str)
            or expert_id not in candidates
            or expert_id in nodes
            or not isinstance(task, str)
            or not task.strip()
        ):
            raise ValueError("expert plan contains an unknown, duplicate, or empty-task expert")
        candidate = candidates[expert_id]
        nodes[expert_id] = {
            "label": candidate.name,
            "description": candidate.description,
            "task": task.strip(),
        }

    edges: list[dict[str, str]] = []
    predecessors: dict[str, set[str]] = {expert_id: set() for expert_id in nodes}
    for item in raw_edges:
        if not isinstance(item, dict):
            raise ValueError("invalid expert plan edge")
        source, target = item.get("source"), item.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("expert plan edge ids must be strings")
        if source not in nodes or target not in nodes or source == target:
            raise ValueError("expert plan edge refers to an unknown or identical node")
        if source not in predecessors[target]:
            predecessors[target].add(source)
            edges.append({"source": source, "target": target, "relation": "can_feed"})

    remaining = {expert_id: set(dependencies) for expert_id, dependencies in predecessors.items()}
    while remaining:
        ready = [expert_id for expert_id, dependencies in remaining.items() if not dependencies]
        if not ready:
            raise ValueError("expert plan contains a dependency cycle")
        for expert_id in ready:
            del remaining[expert_id]
        for dependencies in remaining.values():
            dependencies.difference_update(ready)

    return OrchestrationPlan(
        {
            "status": "ready",
            "graph": {
                "id": uuid4().hex,
                "type": "planned_graph",
                "directed": True,
                "metadata": {
                    "status": "ready",
                    "missing_inputs": [],
                    "leader": {"id": "xiaoyiwork", "label": "小艺Work", "role": "lead"},
                },
                "nodes": nodes,
                "edges": edges,
            },
        }
    )


async def plan_experts(
    query: str,
    candidates: Sequence[ExpertCandidate],
    model: Model,
    *,
    timeout: int | float = 60,
) -> OrchestrationPlan:
    """Plan a minimal, acyclic expert group for one task with one model call.

    No available candidates returns ``no_plan`` without a model request. A
    single selected expert produces a ready plan with no dependency edges.
    Invalid model output raises ``ValueError``; the caller decides how to report
    or recover from that failure.
    """

    if not isinstance(query, str) or not query.strip():
        raise ValueError("expert planning requires a non-empty query")
    indexed = _validate_candidates(candidates)
    if not indexed:
        return OrchestrationPlan({"status": "no_plan"})
    if model is None:
        raise ValueError("expert planning requires the current request model")

    response = await invoke_json(
        model,
        system_prompt=_PLAN_PROMPT,
        user_content=json.dumps(
            {
                "query": query.strip(),
                "experts": [
                    {
                        "id": candidate.id,
                        "name": candidate.name,
                        "description": candidate.description,
                        "skills": list(candidate.skills),
                    }
                    for candidate in indexed.values()
                ],
            },
            ensure_ascii=False,
        ),
        timeout=timeout,
        error_context="Symphony expert planning",
    )
    return _validate_plan(json.loads(response), indexed)


__all__ = ["ExpertCandidate", "MAX_EXPERTS", "plan_experts"]
