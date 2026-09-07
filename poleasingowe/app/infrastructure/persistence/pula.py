"""Pula połączeń i fabryka kontekstu żądania (SPEC.md §6.3, §12).

Interfejs pracuje na wypożyczonym połączeniu, nie na własnym. Add-on dzieli
Postgresa z innymi aplikacjami (§0), więc liczba połączeń jest zasobem
wspólnym — pula z twardym sufitem jest tu wymaganiem, nie optymalizacją.

`interfaces/` dostaje stąd wyłącznie obiekt spełniający port
`FabrykaKontekstu` i nigdy nie widzi `psycopg` (§6.3).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.application.ports import UnitOfWork, Zapytania
from app.infrastructure.persistence.queries import PgZapytania
from app.infrastructure.persistence.repositories import PgUnitOfWork

log = logging.getLogger(__name__)

# Jeden proces, jeden event loop (§7.1), a interfejs obsługuje jednego
# użytkownika za Ingressem. Cztery połączenia to zapas na równoległe żądania
# HTMX, a nie na ruch — sufit istnieje po to, żeby add-on nie zjadł slotów
# wspólnego serwera.
MIN_POLACZEN = 1
MAKS_POLACZEN = 4
TIMEOUT_S = 10.0


@dataclass(slots=True)
class KontekstPg:
    """Porty zapisu i odczytu zbudowane na jednym połączeniu."""

    uow: UnitOfWork
    zapytania: Zapytania


class PgFabrykaKontekstu:
    """Wypożycza połączenie z puli na czas jednego żądania."""

    def __init__(self, dsn: str, *, opis: str) -> None:
        # `open=False`: pula otwiera się dopiero w `lifespan`, żeby brak bazy
        # przy starcie nie wywalił procesu (SPEC.md §2 pkt 8).
        self._pula = AsyncConnectionPool(
            dsn,
            min_size=MIN_POLACZEN,
            max_size=MAKS_POLACZEN,
            timeout=TIMEOUT_S,
            open=False,
        )
        self._opis = opis
        self._otwarta = False

    async def otworz(self) -> None:
        if self._otwarta:
            return
        await self._pula.open()
        self._otwarta = True
        log.info("pula połączeń otwarta: %s", self._opis)

    @contextlib.asynccontextmanager
    async def __call__(self) -> AsyncIterator[KontekstPg]:
        async with self._pula.connection() as conn:
            yield self._zbuduj(conn)

    @staticmethod
    def _zbuduj(conn: AsyncConnection) -> KontekstPg:
        return KontekstPg(uow=PgUnitOfWork(conn), zapytania=PgZapytania(conn))

    async def zamknij(self) -> None:
        if not self._otwarta:
            return
        await self._pula.close()
        self._otwarta = False

    def stan_puli(self) -> dict[str, int]:
        """Liczby do panelu diagnostycznego (§12), nie obiekt puli.

        `interfaces/` ma pokazać stan, a nie dostać uchwyt, którym mógłby
        pulą sterować.
        """
        if not self._otwarta:
            return {}
        statystyki = self._pula.get_stats()
        interesujace = (
            "pool_size",
            "pool_available",
            "requests_waiting",
            "requests_errors",
            "connections_errors",
        )
        return {k: int(statystyki[k]) for k in interesujace if k in statystyki}
