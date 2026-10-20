# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeletionGuard（Pipeline D）判定单元测试。"""

from __future__ import annotations

from openjiuwen.harness.security.permission_engine.delguard.deletion_guard import (
    DeletionGuard,
    build_deletion_guard,
)
from openjiuwen.harness.security.permission_engine.models import PermissionLevel


def _cfg(soft_on: bool, threshold: int) -> dict:
    return {
        "permissions": {
            "soft_delete": {
                "enabled": soft_on,
                "batch_delete_threshold": threshold,
            },
        },
    }


def test_soft_delete_off_returns_none(tmp_path) -> None:
    guard = build_deletion_guard(_cfg(False, 2))
    assert guard is None
    # 即便直接构造，关闭也应返回 None
    g = DeletionGuard(permission_config=_cfg(False, 2))
    assert g.evaluate("bash", {"command": "rm -rf ."}) is None


def test_below_threshold_returns_none(tmp_path) -> None:
    a = tmp_path / "a"
    a.write_bytes(b"x")
    guard = build_deletion_guard(_cfg(True, 10))
    assert guard is not None
    res = guard.evaluate("bash", {"command": f"rm {a}", "workdir": str(tmp_path)})
    assert res is None


def test_at_threshold_asks(tmp_path) -> None:
    files = []
    for i in range(5):
        p = tmp_path / f"f{i}"
        p.write_bytes(b"x")
        files.append(str(p))
    guard = build_deletion_guard(_cfg(True, 5))
    res = guard.evaluate(
        "bash",
        {"command": f"rm {' '.join(files)}", "workdir": str(tmp_path)},
    )
    assert res is not None
    assert res.permission == PermissionLevel.ASK
    assert res.matched_rule == "del_guard:batch_delete"


def test_non_shell_tool_returns_none(tmp_path) -> None:
    guard = build_deletion_guard(_cfg(True, 1))
    res = guard.evaluate("read_file", {"file_path": "a", "command": "rm -rf /"})
    assert res is None


def test_threshold_zero_disables(tmp_path) -> None:
    guard = build_deletion_guard(_cfg(True, 0))
    assert guard is None


def test_missing_threshold_defaults_to_100() -> None:
    cfg = {"soft_delete": {"enabled": True}}
    guard = build_deletion_guard(cfg)
    assert guard is not None
    assert guard.threshold() == 100


def test_non_delete_command_returns_none(tmp_path) -> None:
    guard = build_deletion_guard(_cfg(True, 1))
    res = guard.evaluate("bash", {"command": "echo hello", "workdir": str(tmp_path)})
    assert res is None


def test_bool_provider_overrides_config(tmp_path) -> None:
    a = tmp_path / "a"
    a.write_bytes(b"x")
    # 配置里关闭，但外部 provider 强制开启
    g = DeletionGuard(soft_delete_enabled=True, permission_config=_cfg(False, 1))
    assert g.enabled() is True
    res = g.evaluate("bash", {"command": f"rm {a}", "workdir": str(tmp_path)})
    assert res is not None
    assert res.permission == PermissionLevel.ASK