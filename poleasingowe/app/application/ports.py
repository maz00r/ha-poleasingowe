"""Porty — kontrakty, które implementuje `infrastructure` (SPEC.md §6.2).

`AuthenticatedSource` powstanie razem z pierwszym adapterem wymagającym
logowania (SPEC.md §10.1) — §6.3 zabrania interfejsów „na wypadek gdyby
kiedyś", więc nie definiujemy go, dopóki nie ma implementacji.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.domain.entities import (
    Auction,
    PriceSnapshot,
    RunLog,
    Source,
    WatchlistEntry,
)
from app.domain.value_objects import Vin


@runtime_checkable
class Clock(Protocol):
    """Zegar jako port (SPEC.md §6.2).

    Żadnego `datetime.now()` poza implementacją tego portu — inaczej
    harmonogram z §11 jest nietestowalny, a `PollingPolicy` przestaje być
    czystą funkcją.

    `now()` zwraca czas **świadomy strefy, w UTC**. Konwersja do strefy
    lokalnej należy wyłącznie do warstwy widoku (§8.2).
    """

    def now(self) -> dt.datetime: ...


class SourceRepository(Protocol):
    """SPEC.md §6.2 — SQL wyłącznie w `infrastructure/persistence/`."""

    async def zapisz(self, source: Source) -> Source: ...
    async def po_kluczu(self, key: str) -> Source | None: ...
    async def wlaczone(self) -> Sequence[Source]: ...


class AuctionRepository(Protocol):
    async def zapisz(self, auction: Auction) -> Auction: ...
    async def po_kluczu_naturalnym(
        self, source_id: int, external_id: str
    ) -> Auction | None: ...
    async def do_odpytu(self, teraz: dt.datetime, limit: int) -> Sequence[Auction]: ...
    async def po_vin(self, vin: Vin) -> Sequence[Auction]: ...
    async def odnotuj_widziana(self, auction_id: int, teraz: dt.datetime) -> None: ...


class SnapshotRepository(Protocol):
    async def zapisz_jesli_zmienil_sie(
        self, snapshot: PriceSnapshot
    ) -> PriceSnapshot | None: ...
    async def historia(self, auction_id: int) -> Sequence[PriceSnapshot]: ...


class WatchlistRepository(Protocol):
    async def dodaj(self, wpis: WatchlistEntry) -> WatchlistEntry: ...
    async def usun(self, auction_id: int) -> bool: ...
    async def obserwowana(self, auction_id: int) -> bool: ...


class RunLogRepository(Protocol):
    async def rozpocznij(self, wpis: RunLog) -> RunLog: ...
    async def zakoncz(self, wpis: RunLog) -> RunLog: ...


class UnitOfWork(Protocol):
    """Jedna transakcja na use case, nie na zapytanie (SPEC.md §6.2).

    Commit na wyjściu, rollback na wyjątku.
    """

    source: SourceRepository
    auction: AuctionRepository
    snapshot: SnapshotRepository
    watchlist: WatchlistRepository
    run_log: RunLogRepository

    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(self, *wyjatek: object) -> None: ...


@dataclass(slots=True, frozen=True)
class SurowaOferta:
    """Surowa pozycja z serwisu, przed tłumaczeniem na model domenowy.

    Warstwa antykorupcyjna (SPEC.md §6.2): dziwactwa serwisu żyją tutaj
    i w `mapper.py` adaptera, i nie wyciekają dalej.
    """

    external_id: str
    url: str
    pola: dict[str, str]
    """Pola tak, jak podał je serwis — bez interpretacji i bez konwersji."""


class AuctionSource(Protocol):
    """Port źródła (SPEC.md §6.2 — Strategy + Protocol).

    Adaptery serwisów publicznych implementują tylko to. §10.1 zabrania
    zmuszania ich do pustego `login()`.
    """

    key: str

    async def przemiec_liste(self) -> Sequence[SurowaOferta]:
        """Zbiorczy przemiat listy — główna oszczędność systemu (§11.2)."""
        ...

    async def pobierz_szczegoly(self, external_id: str) -> SurowaOferta:
        """Pojedyncza aukcja. Wywoływane tylko dla obserwowanych (§11.2)."""
        ...

    def na_aukcje(
        self, surowa: SurowaOferta, source_id: int, teraz: dt.datetime
    ) -> Auction:
        """Tłumaczy surowy kształt na model domenowy (anti-corruption layer)."""
        ...
