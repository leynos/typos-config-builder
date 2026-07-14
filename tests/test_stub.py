"""Tests for the generated package stub."""

from __future__ import annotations

import typos_config_builder


def test_hello_returns_stub_greeting() -> None:
    """The generated package exposes a working greeting."""
    assert typos_config_builder.hello() == "hello from Python"
