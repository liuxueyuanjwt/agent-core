# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""FileBackupManager 单元测试。"""

from __future__ import annotations

import asyncio
import os

import pytest

from openjiuwen.core.sys_operation.local.file_backup import (
    FileBackupManager,
    set_backup_manager,
    set_round_provider,
)


@pytest.fixture
def manager(tmp_path):
    set_backup_manager(None)
    set_round_provider(None)
    return FileBackupManager(
        backup_dir=tmp_path / "backup",
        max_total_bytes=10 * 1024 * 1024,
    )


def _run(coro):
    return asyncio.run(coro)


def test_backup_creates_snapshot(manager, tmp_path) -> None:
    src = tmp_path / "proj" / "a.txt"
    src.parent.mkdir(parents=True)
    src.write_text("hello")
    _run(manager.backup_before_write(str(src)))
    backups = [p for p in manager.backup_dir.rglob("*.bak")]
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"hello"


def test_new_file_skipped(manager, tmp_path) -> None:
    missing = tmp_path / "not_exists.txt"
    _run(manager.backup_before_write(str(missing)))
    assert not manager.backup_dir.exists()


def test_dedup_within_round(manager, tmp_path) -> None:
    src = tmp_path / "proj" / "a.txt"
    src.parent.mkdir(parents=True)
    src.write_text("v1")
    _run(manager.backup_before_write(str(src)))
    src.write_text("v2")
    _run(manager.backup_before_write(str(src)))
    backups = [p for p in manager.backup_dir.rglob("*.bak")]
    assert len(backups) == 1


def test_cross_round_backs_up_again(manager, tmp_path) -> None:
    src = tmp_path / "proj" / "a.txt"
    src.parent.mkdir(parents=True)
    src.write_text("v1")
    _run(manager.backup_before_write(str(src)))
    manager.mark_turn("round2")
    src.write_text("v2")
    _run(manager.backup_before_write(str(src)))
    backups = [p for p in manager.backup_dir.rglob("*.bak")]
    assert len(backups) == 2


def test_disabled_when_zero_quota(tmp_path) -> None:
    m = FileBackupManager(backup_dir=tmp_path / "b", max_total_bytes=0)
    assert m.enabled is False
    src = tmp_path / "a.txt"
    src.write_text("x")
    _run(m.backup_before_write(str(src)))
    assert not (tmp_path / "b").exists()


def test_quota_evicts_oldest(tmp_path) -> None:
    m = FileBackupManager(backup_dir=tmp_path / "b", max_total_bytes=6)
    src = tmp_path / "a.txt"
    src.write_bytes(b"aaa")  # 3 bytes
    _run(m.backup_before_write(str(src)))
    m.mark_turn("r2")
    src.write_bytes(b"bbb")  # 3 bytes
    _run(m.backup_before_write(str(src)))
    # 6 bytes 未超
    assert m.total_backup_bytes() == 6
    m.mark_turn("r3")
    src.write_bytes(b"cccc")  # 4 bytes → 超 6
    _run(m.backup_before_write(str(src)))
    # 淘汰最旧后应 <= 6
    assert m.total_backup_bytes() <= 6


def test_backup_failure_does_not_raise(manager, tmp_path) -> None:
    # 不可读路径（目录）应静默跳过，不抛异常
    d = tmp_path / "dir"
    d.mkdir()
    _run(manager.backup_before_write(str(d)))
    assert True