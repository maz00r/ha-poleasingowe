"""Porty — kontrakty, które implementuje `infrastructure` (SPEC.md §6.2).

`AuthenticatedSource` powstanie razem z pierwszym adapterem wymagającym
logowania (SPEC.md §10.1) — §6.3 zabrania interfejsów „na wypadek gdyby
kiedyś", więc nie definiujemy go, dopóki nie ma implementacji.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.application.read_models import (
    Kryteria,
    Kursor,
    StanZrodla,
    Strona,
    Szczegoly,
)
from app.domain.entities import (
    Auction,
    PriceSnapshot,
    RunLog,
    SavedFilter,
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
    async def wpis(self, auction_id: int) -> WatchlistEntry | None: ...


class SavedFilterRepository(Protocol):
    """Zapisane filtry (SPEC.md §12)."""

    async def zapisz(self, wpis: SavedFilter) -> SavedFilter: ...
    async def wszystkie(self) -> Sequence[SavedFilter]: ...
    async def usun(self, filter_id: int) -> bool: ...


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
    saved_filter: SavedFilterRepository
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


@dataclass(slots=True, frozen=True)
class Ciastko:
    """Jedno ciasteczko sesji, w postaci niezależnej od klienta HTTP.

    Port celowo nie mówi `httpx.Cookies`: magazyn sesji ma opisywać **co**
    przechowujemy, a nie którą biblioteką akurat chodzimy po sieci.
    """

    nazwa: str
    wartosc: str
    domena: str = ""
    sciezka: str = "/"
    wygasa: int | None = None
    """Uniksowy znacznik czasu albo `None` dla ciasteczka sesyjnego."""

    def __repr__(self) -> str:
        # SPEC.md §10.2 — wartość ciasteczka sesji to poświadczenie.
        return f"Ciastko(nazwa={self.nazwa!r}, wartosc='***', domena={self.domena!r})"

    __str__ = __repr__


class MagazynSesji(Protocol):
    """Trwałe ciasteczka sesji per źródło (SPEC.md §10.2).

    Restart add-onu **nie ma** powodować ponownego logowania: każde zbędne
    logowanie to kolejna próba na imiennym koncie, a limit z §10.2 jest
    twardy.
    """

    def wczytaj(self, source_key: str) -> tuple[Ciastko, ...]: ...
    def zapisz(self, source_key: str, ciastka: Sequence[Ciastko]) -> None: ...
    def usun(self, source_key: str) -> None: ...


@dataclass(slots=True, frozen=True)
class OdpowiedzHttp:
    """Tyle z odpowiedzi HTTP, ile potrzeba do wykrycia wygaśnięcia sesji."""

    kod: int
    tresc: str
    url_koncowy: str
    czy_przekierowano: bool = False


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


class Zapytania(Protocol):
    """Strona odczytu dla interfejsu (SPEC.md §6.2, §12).

    Osobna od repozytoriów, bo ma inne wymagania: łączy tabele, zwraca
    modele odczytu zamiast encji i nie musi mieścić się w jednej transakcji
    na use case. Repozytorium, które próbuje obsłużyć obie strony, kończy
    jako worek na zapytania raportowe.
    """

    async def lista(
        self, kryteria: Kryteria, kursor: Kursor | None, limit: int
    ) -> Strona: ...

    async def szczegoly(self, auction_id: int) -> Szczegoly | None: ...

    async def wartosci_filtrow(self) -> dict[str, tuple[str, ...]]:
        """Wartości do list rozwijanych — marki, paliwa, skrzynie, lokalizacje.

        Brane z danych, nie ze słownika w kodzie: filtr pokazujący markę,
        której nie ma w bazie, to filtr, który zawsze zwraca pustkę.
        """
        ...

    async def diagnostyka(self) -> tuple[StanZrodla, ...]:
        """Wiersze panelu z `reporting.v_source_health` (§12)."""
        ...

    async def rozmiar_bazy(self) -> int | None: ...

    async def czas_serwera(self) -> dt.datetime:
        """Zegar Postgresa — odniesienie do wykrywania dryfu (§11.7)."""
        ...


class KontekstBazy(Protocol):
    """Jedno wypożyczone połączenie z portami zapisu i odczytu na nim."""

    uow: UnitOfWork
    zapytania: Zapytania


class FabrykaKontekstu(Protocol):
    """Wypożycza połączenie z puli na czas jednego żądania.

    `interfaces/` dostaje to wstrzyknięte i nigdy nie widzi sterownika bazy
    — pilnuje tego kontrakt import-lintera (SPEC.md §6.3).
    """

    def __call__(self) -> AbstractAsyncContextManager[KontekstBazy]: ...

    async def zamknij(self) -> None: ...

    def stan_puli(self) -> dict[str, int]:
        """Liczby do panelu diagnostycznego (§12), nie obiekty puli."""
        ...


class AuthenticatedSource(AuctionSource, Protocol):
    """Źródło wymagające zalogowania (SPEC.md §10.1).

    Adaptery serwisów publicznych implementują samo `AuctionSource` — §10.1
    zabrania zmuszania ich do pustego `login()`.
    """

    async def zaloguj(self, login: str, haslo: str) -> tuple[Ciastko, ...]:
        """Loguje się i zwraca ciasteczka sesji.

        Rzuca `AuthenticationFailed`, gdy serwis odrzucił poświadczenia —
        i **tylko** wtedy. Błąd sieci to `SourceUnavailable`; pomylenie tych
        dwóch podbija licznik z §10.2 za cudzą awarię.
        """
        ...

    def przywroc_sesje(self, ciastka: Sequence[Ciastko]) -> None:
        """Wstawia ciasteczka z magazynu do klienta HTTP przy starcie."""
        ...

    def czy_sesja_wygasla(self, odpowiedz: OdpowiedzHttp) -> bool:
        """Marker wygaśnięcia **definiuje adapter**, nie warstwa wspólna.

        SPEC.md §10.2: po treści odpowiedzi, nie po samym kodzie HTTP.
        Każdy serwis sygnalizuje to inaczej — poleasingowe.pl przez
        przekierowanie i `auth: false` w JSON-ie (RECON.md §4.2), inne przez
        brak markera zalogowania w HTML.
        """
        ...
