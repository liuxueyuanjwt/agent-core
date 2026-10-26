# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public client imports must remain usable without optional model backends."""

import subprocess
import sys
from pathlib import Path


def test_client_exports_without_optional_model_imports():
    source = """
import importlib.abc
import sys
from typing import get_type_hints

class RejectModelBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"torch", "transformers", "vllm"}:
            raise AssertionError(f"unexpected optional import: {fullname}")
        return None

sys.meta_path.insert(0, RejectModelBackend())
from openjiuwen.symphony.retrieval import llm
from openjiuwen.symphony.retrieval.llm import TransformersLogitSelectionClient
from openjiuwen.symphony.retrieval.llm import transformers_logit_selection
from openjiuwen.symphony.retrieval.llm.transformers_logit_selection import client

assert llm.TransformersLogitSelectionClient is client.TransformersLogitSelectionClient
assert transformers_logit_selection.TransformersLogitSelectionClient is TransformersLogitSelectionClient
for module in (llm, transformers_logit_selection, client):
    assert TransformersLogitSelectionClient.__name__ in module.__all__
assert get_type_hints(TransformersLogitSelectionClient.from_pretrained).get("return") is TransformersLogitSelectionClient
assert {"torch", "transformers", "vllm"}.isdisjoint(sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
