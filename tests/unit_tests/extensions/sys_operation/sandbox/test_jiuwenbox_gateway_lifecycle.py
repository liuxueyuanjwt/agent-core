# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exercise lifecycle cleanup through real SysOperation/Gateway/provider routing."""

import asyncio
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from openjiuwen.core.sys_operation import OperationMode, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import (
    ContainerScope, PreDeployLauncherConfig, SandboxGatewayConfig, SandboxIsolationConfig,
)
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxGateway
from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as jb


@pytest.fixture
def remote(monkeypatch):
    cls = jb._JiuwenBoxProviderMixin
    for name in ("_shared_sandbox_ids", "_creation_locks", "_creation_failures", "_lifecycle_hooks",
                 "_pending_deletions", "_delete_tasks", "_idle_timeout_cache"):
        monkeypatch.setattr(cls, name, {})
    monkeypatch.setattr(cls, "_bulk_deletions", 0)
    monkeypatch.setattr(cls, "_inflight_recreations", set())
    monkeypatch.setattr(cls, "_recreate_lock", None)
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)
    gateway = SandboxGateway()
    monkeypatch.setattr(SandboxGateway, "_instance", gateway)
    state = SimpleNamespace(
        gateway=gateway, requests=[], created=[], active=set(), fail_delete=False, block_create=False,
        entered=threading.Event(), release=threading.Event(), clients=[], lost=set(),
    )

    def respond(request):
        state.requests.append((request.method, str(request.url)))
        if request.method == "POST":
            assert request.url.path == "/api/v1/sandboxes"
            if state.block_create:
                state.entered.set()
                assert state.release.wait(5), "creation was not released"
            sid = f"sandbox-{len(state.created) + 1}"
            state.created.append((request.url.port, json.loads(request.content), sid))
            state.active.add(sid)
            return httpx.Response(201, json={"id": sid})
        sid = request.url.path.split("/")[4]
        if request.method == "DELETE":
            if state.fail_delete:
                return httpx.Response(500, json={"detail": "ACL cleanup failed"})
            state.active.discard(sid)
            return httpx.Response(204)
        assert request.method == "GET" and request.url.path.endswith("/download")
        if sid in state.lost:
            return httpx.Response(404, json={"detail": f"Sandbox '{sid}' not found"})
        return httpx.Response(200, content=sid.encode())

    def client(base_url, **_kwargs):
        result = httpx.Client(base_url=base_url, transport=httpx.MockTransport(respond))
        state.clients.append(result)
        return result

    monkeypatch.setattr(jb, "build_jiuwenbox_http_client", client)
    yield state
    state.release.set()
    for connection in state.clients:
        connection.close()


def operation(url="http://localhost:8321", scope="project", policy="old"):
    return SysOperation(SysOperationCard(
        mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.CUSTOM, custom_id=scope),
            launcher_config=PreDeployLauncherConfig(
                base_url=url, sandbox_type="jiuwenbox", extra_params={"policy": {"tag": policy}},
            ),
        ),
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("retire", [False, True])
@pytest.mark.parametrize("port", [8321, 9000])
async def test_cleanup_replaces_gateway_endpoint_provider_and_policy(remote, retire, port):
    old = operation()
    assert (await old.fs().read_file("/probe")).data.content == "sandbox-1"
    old_provider = next(iter(remote.gateway._provider_cache.values()))
    other = operation("http://localhost:83210", scope="other")
    await other.fs().read_file("/probe")
    other_key = f"{other.isolation_key_template}:fs"
    other_provider = remote.gateway._provider_cache[other_key]
    aio_config = SandboxGatewayConfig(launcher_config=PreDeployLauncherConfig(
        base_url="http://localhost:8321", sandbox_type="aio",
    ))
    aio = await remote.gateway._get_or_create_provider(aio_config, "aio-scope", "fs")
    if retire:
        assert await jb.retire_jiuwenbox_service("http://localhost:8321/") == ["sandbox-1"]
        assert remote.gateway._provider_cache[other_key] is other_provider
        assert (await remote.gateway._store.get(other.isolation_key_template)).base_url.endswith(":83210")
        assert not any(method == "DELETE" for method, _ in remote.requests)
    else:
        await jb.delete_jiuwenbox_sandbox(delete_all=True, raise_on_error=True)
        assert not remote.active
    assert await remote.gateway._store.get(old.isolation_key_template) is None
    assert remote.gateway._provider_cache["aio-scope:fs"] is aio
    assert await remote.gateway._store.get("aio-scope") is not None
    assert old_provider._sandbox_id is None
    assert "sandbox_id" not in old._run_config.config.launcher_config.extra_params

    new = operation(f"http://localhost:{port}", policy="new")
    result = await new.fs().read_file("/probe")
    assert result.code == 0
    assert remote.created[-1][:2] == (port, {"policy": {"tag": "new"}})
    provider = remote.gateway._provider_cache[f"{new.isolation_key_template}:fs"]
    assert provider is not old_provider
    assert provider.config is new._run_config.config
    assert result.data.content != "sandbox-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_failure", [False, True])
async def test_bulk_cleanup_waits_for_unpublished_create_and_keeps_failed_delete(remote, delete_failure):
    remote.block_create = True
    remote.fail_delete = delete_failure
    old = operation()
    task = asyncio.create_task(old.fs().read_file("/probe"))
    cleanup = None
    try:
        assert await asyncio.to_thread(remote.entered.wait, 2)
        cleanup = asyncio.create_task(jb.delete_jiuwenbox_sandbox(delete_all=True, raise_on_error=True))
        await asyncio.sleep(0)
        assert not cleanup.done()
        # This request also traverses the real Gateway and must not issue POST.
        rejected = await operation(scope="new-scope").fs().read_file("/probe")
        assert rejected.code != 0
        assert "bulk deletion is pending" in rejected.message
        assert len([r for r in remote.requests if r[0] == "POST"]) == 1
        remote.release.set()
        await task
        if delete_failure:
            with pytest.raises(RuntimeError, match="ACL cleanup failed"):
                await cleanup
            assert remote.active == {"sandbox-1"}
            assert jb._JiuwenBoxProviderMixin._pending_deletions
            blocked = await operation().fs().read_file("/probe")
            assert blocked.code != 0
            remote.fail_delete = False
            assert await jb.delete_jiuwenbox_sandbox(delete_all=True, raise_on_error=True) == ["sandbox-1"]
        else:
            assert await cleanup == ["sandbox-1"]
        assert not remote.active
        assert not jb._JiuwenBoxProviderMixin._shared_sandbox_ids
        assert not jb._JiuwenBoxProviderMixin._pending_deletions
        assert not remote.gateway._provider_cache
        assert not remote.gateway._store.items()
        assert jb._JiuwenBoxProviderMixin._bulk_deletions == 0
    finally:
        remote.release.set()
        await asyncio.gather(*(t for t in (task, cleanup) if t is not None), return_exceptions=True)


@pytest.mark.asyncio
async def test_retirement_discards_gateway_entries_resolved_while_joining_hooks(remote):
    old = operation()
    await old.fs().read_file("/probe")
    key = jb.build_jiuwenbox_shared_scope_key("http://localhost:8321", old.isolation_key_template)
    resolutions = []

    def hook(event, _context):
        if event == "before_delete":
            resolutions.append(asyncio.create_task(remote.gateway._get_or_create_provider(
                old._run_config.config, old.isolation_key_template, "fs",
            )))

    jb._JiuwenBoxProviderMixin.register_lifecycle_hook(key, hook)
    assert await jb.retire_jiuwenbox_service("http://localhost:8321") == ["sandbox-1"]
    assert resolutions
    await asyncio.gather(*resolutions)
    assert not remote.gateway._provider_cache
    assert not remote.gateway._store.items()
    assert not any(method == "DELETE" for method, _ in remote.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_request", [False, True])
async def test_bulk_cleanup_waits_for_gateway_automatic_recreation(remote, monkeypatch, cancel_request):
    old = operation()
    await old.fs().read_file("/probe")
    remote.lost.add("sandbox-1")
    remote.active.discard("sandbox-1")
    remote.block_create = True
    monkeypatch.setattr(jb, "_SANDBOX_RECREATE_RETRY_SLEEP_SECONDS", 0)
    task = asyncio.create_task(old.fs().read_file("/probe"))
    cleanup = None
    try:
        assert await asyncio.to_thread(remote.entered.wait, 2)
        if cancel_request:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        cleanup = asyncio.create_task(jb.delete_jiuwenbox_sandbox(delete_all=True, raise_on_error=True))
        await asyncio.sleep(0)
        assert not cleanup.done()
        assert jb._JiuwenBoxProviderMixin._inflight_recreations
        remote.release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await cleanup == ["sandbox-2"]
        assert not remote.active
        assert not jb._JiuwenBoxProviderMixin._shared_sandbox_ids
        assert not jb._JiuwenBoxProviderMixin._inflight_recreations
        assert not remote.gateway._provider_cache
    finally:
        remote.release.set()
        await asyncio.gather(*(t for t in (task, cleanup) if t is not None), return_exceptions=True)
