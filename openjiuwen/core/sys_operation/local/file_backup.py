# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""自动备份管理器：写前快照 + 范围去重 + 跨会话配额与最旧优先淘汰。

对外入口：:class:`FileBackupManager` 与进程级 ``resolve_file_backup_manager()``。

- 备份时机：文件被真正写盘之前（``write_file`` / ``edit_file`` / shell 内写文件）。
- 去重：以「范围 id + 文件绝对路径」为键。宿主传入 session id 时，同一会话同一文件只备份一次。
- 目录：``<backup_dir>/<project>/<session>/<relpath>/<name>.<ts>.bak``。
- 配额：所有项目/会话共享 ``max_total_bytes``，超出按 mtime 从旧到新淘汰。
- 失败策略：fail-open，任何异常只告警，不阻断主写入流程。
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Callable

from openjiuwen.core.common.logging import logger
from openjiuwen.core.session import get_current_session
from openjiuwen.core.sys_operation.cwd import get_cwd, get_project_root

# 去重范围提供器：宿主注入。jiuwenswarm 传入当前 session id，同一会话同一文件只备份一次。
RoundProvider = Callable[[], str | None]

process_round_provider: RoundProvider | None = None
_round_provider_lock = threading.Lock()

_active_manager: "FileBackupManager | None" = None
_active_manager_lock = threading.Lock()


def set_round_provider(provider: RoundProvider | None) -> None:
    """注入轮次提供器；``None`` 卸载，回退到 session_id 兜底。"""
    global process_round_provider
    with _round_provider_lock:
        process_round_provider = provider


def get_round_provider() -> RoundProvider | None:
    with _round_provider_lock:
        return process_round_provider


def current_round_id() -> str | None:
    """解析当前轮次 id：优先宿主注入的 provider，否则回退 session_id。"""
    provider = get_round_provider()
    if provider is not None:
        try:
            value = provider()
            if isinstance(value, str) and value.strip():
                return value.strip()
        except Exception:  # noqa: BLE001
            logger.warning("[FileBackup] round_provider failed", exc_info=True)
    try:
        session = get_current_session()
        if session is not None:
            getter = getattr(session, "get_session_id", None)
            if callable(getter):
                sid = getter()
                if isinstance(sid, str) and sid.strip():
                    return sid.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def set_backup_manager(manager: "FileBackupManager | None") -> None:
    """注册/注销当前的备份管理器。"""
    global _active_manager
    with _active_manager_lock:
        _active_manager = manager


def get_backup_manager() -> "FileBackupManager | None":
    with _active_manager_lock:
        return _active_manager


def resolve_file_backup_manager() -> "FileBackupManager | None":
    """获取当前活动的备份管理器；未配置返回 ``None``。"""
    return get_backup_manager()


_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_name(name: str, fallback: str = "_") -> str:
    cleaned = _ILLEGAL_CHARS.sub("_", name or "").strip().strip(".")
    return cleaned or fallback


class FileBackupManager:
    """文件写前备份管理器。"""

    def __init__(self, *, backup_dir: str | Path, max_total_bytes: int) -> None:
        self._backup_dir = Path(backup_dir)
        self._max_total_bytes = max(0, int(max_total_bytes or 0))
        self._lock = threading.Lock()
        # key = round_id:abs_path
        self._backed_up_this_round: set[str] = set()
        self._marked_round: str | None = None

    @property
    def enabled(self) -> bool:
        return self._max_total_bytes > 0

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir

    @property
    def max_total_bytes(self) -> int:
        return self._max_total_bytes

    # ---- round 去重生命周期 ----

    def mark_turn(self, round_id: str | None) -> None:
        """切换到新轮次：清空「本轮已备份」集合，释放内存。"""
        with self._lock:
            if round_id != self._marked_round:
                self._backed_up_this_round.clear()
                self._marked_round = round_id

    def _round_key(self) -> str:
        """去重用的轮次 id：优先 provider 的 current_round_id，回退到 mark_turn 标记。"""
        provider_id = current_round_id()
        if provider_id:
            return provider_id
        with self._lock:
            return self._marked_round or "<n/a>"

    def _dedup_key(self, abs_path: str) -> str:
        return f"{self._round_key()}:{abs_path}"

    # ---- 备份路径构造 ----

    @staticmethod
    def _project_name() -> str:
        try:
            root = get_project_root()
        except Exception:  # noqa: BLE001
            root = get_cwd()
        return _sanitize_name(Path(root).name, fallback="project")

    @staticmethod
    def _session_name() -> str:
        try:
            session = get_current_session()
            if session is not None:
                getter = getattr(session, "get_session_id", None)
                if callable(getter):
                    sid = getter()
                    if isinstance(sid, str) and sid.strip():
                        return _sanitize_name(sid, fallback="session")
        except Exception:  # noqa: BLE001
            pass
        return "session"

    @staticmethod
    def _rel_dir(abs_path: str) -> str:
        """项目内文件的相对目录（保留层次）；项目外文件统一放入 ``outside``。"""
        try:
            root = Path(get_project_root()).resolve()
            target = Path(abs_path).resolve()
            rel = target.relative_to(root).parent
        except Exception:  # noqa: BLE001
            return "outside"
        parts = [_sanitize_name(p, fallback="_") for p in rel.parts]
        return os.path.join(*parts) if parts else ""

    def _backup_target(self, abs_path: str) -> Path:
        name = _sanitize_name(Path(abs_path).name, fallback="file")
        ts = int(time.time() * 1000)
        return (
            self._backup_dir
            / self._project_name()
            / self._session_name()
            / self._rel_dir(abs_path)
            / f"{name}.{ts}.bak"
        )

    # ---- 单次备份 ----

    async def backup_before_write(self, file_path: str) -> None:
        """写盘前备份；当前去重范围内已备份过 / 目标不存在时跳过。"""
        if not self.enabled:
            return
        if not file_path:
            return
        try:
            abs_path = str(Path(file_path).expanduser().resolve())
        except (OSError, ValueError):
            return

        key = self._dedup_key(abs_path)
        with self._lock:
            if key in self._backed_up_this_round:
                return

        try:
            if not os.path.isfile(abs_path):
                return
            with open(abs_path, "rb") as f:
                content = f.read()
        except OSError:
            return  # 目标不存在/不可读 → 视为新建或跳过

        target = self._backup_target(abs_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            with open(tmp, "wb") as f:
                f.write(content)
            os.replace(tmp, target)
        except OSError as exc:
            logger.warning("[FileBackup] backup write failed path=%s: %s", abs_path, exc)
            return

        with self._lock:
            self._backed_up_this_round.add(key)

        try:
            self.enforce_quota()
        except Exception:  # noqa: BLE001
            logger.warning("[FileBackup] quota enforcement failed", exc_info=True)

    # ---- 配额淘汰 ----

    def _collect_backups(self) -> list[tuple[float, int, Path]]:
        """返回 ``(mtime, size, path)`` 列表，按 mtime 升序。"""
        backups: list[tuple[float, int, Path]] = []
        if not self._backup_dir.is_dir():
            return backups
        for root, _dirs, files in os.walk(self._backup_dir):
            for name in files:
                if not name.endswith(".bak"):
                    continue
                p = Path(root) / name
                try:
                    st = os.stat(p)
                    backups.append((st.st_mtime, st.st_size, p))
                except OSError:
                    continue
        backups.sort(key=lambda item: item[0])
        return backups

    def total_backup_bytes(self) -> int:
        return sum(size for _mtime, size, _path in self._collect_backups())

    def enforce_quota(self) -> None:
        """总大小超过上限时，按 mtime 从旧到新删除直到满足。"""
        if self._max_total_bytes <= 0:
            return
        backups = self._collect_backups()
        used = sum(size for _mtime, size, _path in backups)
        while used > self._max_total_bytes and backups:
            mtime, size, path = backups.pop(0)
            try:
                path.unlink(missing_ok=True)
                used -= size
                logger.info("[FileBackup] quota evict old=%s size=%d", path, size)
            except OSError as exc:
                logger.warning("[FileBackup] evict failed path=%s: %s", path, exc)


__all__ = [
    "FileBackupManager",
    "RoundProvider",
    "current_round_id",
    "resolve_file_backup_manager",
    "set_backup_manager",
    "get_backup_manager",
    "set_round_provider",
    "get_round_provider",
]