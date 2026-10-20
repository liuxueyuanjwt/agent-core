# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""批量删除计数估算器（Pipeline D）单元测试。"""

from __future__ import annotations

import os

from openjiuwen.harness.security.permission_engine.delguard.del_count import (
    _entry_cost,
    count_deletion_targets,
)


def _mk(tmp_path, name: str, content: bytes = b"x") -> str:
    p = tmp_path / name
    p.write_bytes(content)
    return str(p)


def test_entry_cost_file_symlink_dir(tmp_path) -> None:
    # 普通文件 = 1
    f = tmp_path / "a.txt"
    f.write_bytes(b"x")
    assert _entry_cost(f) == 1

    # 符号链接 = 1（指向目录也不递归）
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "inner.txt").write_bytes(b"y")
    link = tmp_path / "link"
    if os.name != "nt" or hasattr(os, "symlink"):
        try:
            os.symlink(str(subdir), str(link))
            assert _entry_cost(link) == 1
        except (OSError, NotImplementedError):
            pass

    # 空目录 = 0
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _entry_cost(empty) == 0

    # 非空目录：递归内部文件+符号链接，不计目录节点自身
    d = tmp_path / "tree"
    d.mkdir()
    (d / "f1").write_bytes(b"1")
    (d / "f2").write_bytes(b"2")
    (d / "sub").mkdir()
    (d / "sub" / "f3").write_bytes(b"3")
    assert _entry_cost(d) == 3


def test_count_basic_files(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    b = _mk(tmp_path, "b")
    count, paths = count_deletion_targets(f"rm {a} {b}", "bash", str(tmp_path))
    assert count == 2
    assert len(paths) == 2


def test_count_recursive_dir(tmp_path) -> None:
    d = tmp_path / "tree"
    d.mkdir()
    for i in range(5):
        (d / f"f{i}").write_bytes(b"x")
    count, _ = count_deletion_targets(f"rm -rf {d}", "bash", str(tmp_path))
    assert count == 5


def test_count_empty_dir_zero(tmp_path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    count, _ = count_deletion_targets(f"rm -r {d}", "bash", str(tmp_path))
    assert count == 0


def test_count_dedup_same_path(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    count, paths = count_deletion_targets(f"rm {a} {a}", "bash", str(tmp_path))
    assert count == 1
    assert len(paths) == 1


def test_count_accumulate_across_commands(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    b = _mk(tmp_path, "b")
    d = tmp_path / "tree"
    d.mkdir()
    (d / "x").write_bytes(b"x")
    count, _ = count_deletion_targets(f"rm {a} && rm -rf {b} {d}", "bash", str(tmp_path))
    assert count == 3


def test_count_powershell_remove_item(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    b = _mk(tmp_path, "b")
    count, _ = count_deletion_targets(
        f"Remove-Item {a},{b} -Force", "powershell", str(tmp_path),
    )
    assert count >= 1  # 逗号分隔在保守解析下至少命中其一；不强制精确


def test_count_cmd_del(tmp_path) -> None:
    a = _mk(tmp_path, "a.txt")
    count, _ = count_deletion_targets(f"del {a}", "cmd", str(tmp_path))
    assert count == 1


def test_count_non_delete_command_zero(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    count, paths = count_deletion_targets(f"cat {a}", "bash", str(tmp_path))
    assert count == 0
    assert paths == []


def test_count_nonexistent_path_zero(tmp_path) -> None:
    count, _ = count_deletion_targets("rm /no/such/path", "bash", str(tmp_path))
    assert count == 0


def test_count_glob_expand(tmp_path) -> None:
    for i in range(3):
        (tmp_path / f"log{i}.txt").write_bytes(b"x")
    count, _ = count_deletion_targets("rm *.txt", "bash", str(tmp_path))
    assert count == 3


def test_count_unexpanded_var_skip(tmp_path) -> None:
    count, _ = count_deletion_targets("rm $TARGET", "bash", str(tmp_path))
    assert count == 0