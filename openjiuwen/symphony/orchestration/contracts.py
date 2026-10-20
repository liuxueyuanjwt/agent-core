"""Public result contract for task-scoped expert planning."""

from __future__ import annotations

from typing import Any, Mapping


class OrchestrationPlan(dict[str, Any]):
    """Public, mapping-compatible online orchestration result."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        super().__init__(payload)

    def to_dict(self) -> dict[str, Any]:
        """Return a shallow copy of the plan as a plain dictionary."""
        return dict(self)
