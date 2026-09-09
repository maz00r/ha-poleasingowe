"""Testy jednostkowe działają **bez sieci** (SPEC.md §13).

„Testy parserów na `fixtures/`, offline, bez sieci." Zamiast zakładać, że tak
jest, blokujemy gniazda — próba wyjścia na zewnątrz kończy się czytelnym
błędem, a nie cichym żądaniem HTTP w trakcie testu.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any, NoReturn

import pytest


class SiecWTescieJednostkowym(RuntimeError):
    """Test jednostkowy próbował wyjść do sieci."""


@pytest.fixture(autouse=True)
def bez_sieci(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def zablokuj(*_a: Any, **_k: Any) -> NoReturn:
        raise SiecWTescieJednostkowym(
            "test jednostkowy próbował nawiązać połączenie sieciowe. "
            "Parsery mają działać na plikach z fixtures/ (SPEC.md §13). "
            "Jeśli test naprawdę potrzebuje sieci, jego miejsce jest "
            "w tests/integration/."
        )

    monkeypatch.setattr(socket.socket, "connect", zablokuj)
    monkeypatch.setattr(socket.socket, "connect_ex", zablokuj)
    monkeypatch.setattr(socket, "create_connection", zablokuj)
    # asyncio rozwiązuje DNS przez `getaddrinfo` zanim stworzy socket. Bez
    # tej blokady test "offline" kończył się błędem DNS systemu zamiast
    # naszym czytelnym wyjątkiem i nie dowodził, że strażnik rzeczywiście
    # obejmuje całą drogę do sieci.
    monkeypatch.setattr(socket, "getaddrinfo", zablokuj)
    yield
