# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parser and encoding failures must not grant literal-command evidence."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.harness.security import literal_shell


@pytest.mark.parametrize("error", [ValueError("invalid input"), RuntimeError("parser failed")])
def test_parser_errors_fail_closed(monkeypatch, error):
    parser = Mock()
    parser.parse.side_effect = error
    monkeypatch.setattr(literal_shell, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(literal_shell, "_get_tree_sitter_bash_parser", Mock(return_value=parser))

    assert literal_shell.parse_literal_shell("python3 script.py") is None
    parser.parse.assert_called_once_with(b"python3 script.py")


def test_invalid_unicode_fails_before_parser(monkeypatch):
    parser = Mock()
    monkeypatch.setattr(literal_shell, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(literal_shell, "_get_tree_sitter_bash_parser", Mock(return_value=parser))

    assert literal_shell.parse_literal_shell("python3 \ud800.py") is None
    parser.parse.assert_not_called()


def test_unexpected_parser_error_is_not_swallowed(monkeypatch):
    parser = Mock()
    parser.parse.side_effect = TypeError("unexpected parser contract")
    monkeypatch.setattr(literal_shell, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(literal_shell, "_get_tree_sitter_bash_parser", Mock(return_value=parser))

    with pytest.raises(TypeError, match="unexpected parser contract"):
        literal_shell.parse_literal_shell("python3 script.py")
