"""Tests must never open a network connection."""

import socket
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def disable_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail fast if a test attempts network I/O."""

    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are forbidden in tests")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    yield
