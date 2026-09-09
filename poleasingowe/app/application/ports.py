"""Porty — kontrakty, które implementuje `infrastructure` (SPEC.md §6.2).

`AuthenticatedSource` powstanie razem z pierwszym adapterem wymagającym
logowania (SPEC.md §10.1) — §6.3 zabrania interfejsów „na wypadek gdyby
kiedyś", więc nie definiujemy go, dopóki nie ma implementacji.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.application.read_models import (
    Kryteria,
    Kursor,
    OfertaNaKarcie,
    PorownanieRynkowe,
    PowiazaneWystawienie,
    PunktHistorii,
    StanZrodla,
    Strona,
    Szczegoly,
    Zakres,
)
from app.domain.entities import (
    Auction,
    OfertaUczestnika,
    PriceSnapshot,
    RunLog,
    SavedFilter,
    Source,
    WatchlistEntry,
    WycenaAukcji,
)
from app.domain.enums import PollTier
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
    async def najblizszy_termin(self) -> dt.datetime | None: ...
    async def zapisz_z_przemiatu(self, aukcje: Sequence[Auction]) -> int: ...
    async def po_vin(self, vin: Vin) -> Sequence[Auction]: ...
    async def odnotuj_widziana(self, auction_id: int, teraz: dt.datetime) -> None: ...
    async def zaplanuj(
        self, auction_id: int, next_poll_at: dt.datetime | None, poll_tier: PollTier
    ) -> None: ...
    async def zamknij_po_terminie(self, teraz: dt.datetime) -> int: ...
    async def oznacz_zniknione(
        self, source_id: int, poprzedni_przemiat: dt.datetime, teraz: dt.datetime
    ) -> int: ...


class SnapshotRepository(Protocol):
    async def zapisz_jesli_zmienil_sie(
        self, snapshot: PriceSnapshot, *, licznik_liczy_oferty: bool = False
    ) -> PriceSnapshot | None:
        """`licznik_liczy_oferty` to `Source.liczy_oferty` (SPEC.md §11.8).

        Domyślne `False` jest celowo ostrożne: źródło, o którym nic nie wiemy,
        ma dawać `bid_gap = NULL`, bo zero czyta się jak „komplet historii".
        """
        ...

    async def historia(self, auction_id: int) -> Sequence[PriceSnapshot]: ...


class OfertaRepository(Protocol):
    """Oferty odczytane wprost ze strony aukcji (SPEC.md §11.8)."""

    async def zapisz_nowe(self, oferty: Sequence[OfertaUczestnika]) -> int:
        """Dopisuje tylko te, których jeszcze nie mamy. Zwraca liczbę nowych.

        Idempotentne z premedytacją: w fazie domknięcia ta sama lista ofert
        wraca po kilka razy w ciągu kilkudziesięciu sekund (§11.5).
        """
        ...

    async def dla_aukcji(self, auction_id: int) -> Sequence[OfertaUczestnika]: ...


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


class WycenaRepository(Protocol):
    """Trwała wycena AI, jedna na aukcję (SPEC.md §12).

    Zapis, nie cache: wycena ma przetrwać restart add-onu i **nie zmieniać
    się sama** przy każdym wejściu na kartę.
    """

    async def zapisz(self, wycena: WycenaAukcji) -> WycenaAukcji: ...
    async def dla_aukcji(self, auction_id: int) -> WycenaAukcji | None: ...


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
    oferta: OfertaRepository
    watchlist: WatchlistRepository
    saved_filter: SavedFilterRepository
    wycena: WycenaRepository
    run_log: RunLogRepository

    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(self, *wyjatek: object) -> None: ...


@dataclass(slots=True, frozen=True)
class SurowaOfertaUczestnika:
    """Jeden wiersz z listy ofert, dokładnie tak, jak podał go serwis.

    Trzy napisy, zero interpretacji — przeliczeniem kwoty, strefy czasowej
    i pseudonimizacją kodu zajmuje się `mapper.py` adaptera (§6.2).
    """

    kod: str
    """Identyfikator nadany przez serwis. **Nie trafia do bazy** — mapper
    zamienia go na pseudonim ważny tylko w obrębie aukcji (`012_oferty.sql`)."""
    kwota: str
    zlozona: str
    identyfikator: str = ""
    """Identyfikator oferty wg serwisu. Pusty, gdy serwis go nie podaje."""


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
    content_hash: str = ""
    """Hash **surowych bajtów** odpowiedzi, liczony przed parsowaniem (§11.3)."""
    oferty: tuple[SurowaOfertaUczestnika, ...] = ()
    """Lista ofert, o ile serwis podaje ją na stronie aukcji (SPEC.md §11.8).

    Pusta krotka znaczy „to źródło jej nie pokazuje **albo** aukcja nie ma
    jeszcze ofert" — te dwa przypadki rozróżnia adapter, nie ten typ.
    """


@dataclass(slots=True, frozen=True)
class StronaPrzemiatu:
    """Jedna prawidłowo rozpoznana strona listy aukcji.

    Adapter oddaje strony osobno, aby dispatcher mógł między nimi obsłużyć
    aukcje w dogrywce. Normalne zakończenie iteratora znaczy pełny przemiat;
    wyjątek po części stron daje wynik `PARTIAL`, a przed pierwszą `FAILED`.
    """

    pozycje: tuple[SurowaOferta, ...]


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

    def strony_przemiatu(self) -> AsyncIterator[StronaPrzemiatu]:
        """Strony jednego przemiatu, od pierwszej do poprawnego końca."""
        ...

    async def pobierz_szczegoly(
        self, external_id: str, znany_hash: str | None = None
    ) -> SurowaOferta | None:
        """Pojedyncza aukcja. Wywoływane tylko dla obserwowanych (§11.2).

        `znany_hash` to `content_hash` z poprzedniego odpytu. Gdy treść się
        nie zmieniła, adapter zwraca `None` **bez parsowania** (§11.3 krok 2):
        parsowanie jest najdroższą operacją CPU w całej aplikacji i nie wolno
        go wykonywać na niezmienionej treści.

        Porównanie musi siedzieć w adapterze, nie u wywołującego — inaczej
        parsowanie i tak by się wykonało, a oszczędność zniknęła.
        """
        ...

    def na_aukcje(
        self, surowa: SurowaOferta, source_id: int, teraz: dt.datetime
    ) -> Auction:
        """Tłumaczy surowy kształt na model domenowy (anti-corruption layer)."""
        ...


@runtime_checkable
class ZrodloZOfertami(Protocol):
    """Źródło, które podaje listę ofert wprost na stronie aukcji (§11.8).

    Osobny protokół, a nie kolejna metoda w `AuctionSource`, bo §10.1 zabrania
    zmuszania adapterów do pustych implementacji. Serwis, który listy ofert nie
    pokazuje, po prostu tego protokołu nie spełnia — i dyspozytor to widzi,
    zamiast dostawać pustą krotkę nie do odróżnienia od „aukcja bez ofert".
    """

    def na_oferty(
        self, surowa: SurowaOferta, auction_id: int, teraz: dt.datetime
    ) -> tuple[OfertaUczestnika, ...]:
        """Tłumaczy surowe wiersze na encje. Bez sieci — dane są już w `surowa`."""
        ...


@runtime_checkable
class Budzik(Protocol):
    """Sposób na wybudzenie pętli dyspozytora przed czasem (SPEC.md §11.1).

    Interfejs nie zna dyspozytora i nie ma prawa go znać — ale gdy ktoś
    otwiera kartę aukcji, chce zobaczyć dane sprzed chwili, a nie sprzed
    ostatniego przemiatu. Bez tego portu odświeżenie czekałoby do końca snu
    pętli, czyli do minuty.

    **Nie omija limitów tempa.** Budzik prosi tylko o wcześniejszy obrót;
    o tym, czy żądanie w ogóle poleci, decyduje jak zawsze kubełek tokenów
    w dyspozytorze. Inaczej odświeżanie z karty byłoby furtką dookoła §11.3.
    """

    def obudz(self) -> None: ...


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

    async def porownania_rynkowe(
        self, auction_id: int
    ) -> tuple[PorownanieRynkowe, ...]: ...

    async def wartosci_filtrow(self) -> dict[str, tuple[str, ...]]:
        """Wartości do list rozwijanych — marki, paliwa, skrzynie, lokalizacje.

        Brane z danych, nie ze słownika w kodzie: filtr pokazujący markę,
        której nie ma w bazie, to filtr, który zawsze zwraca pustkę.
        """
        ...

    async def historia_cen(self, auction_id: int) -> tuple[PunktHistorii, ...]:
        """Przebieg licytacji jednej aukcji (§8.4).

        Snapshoty powstają tylko przy zmianie, więc to lista zdarzeń,
        a nie pomiar co N minut.
        """
        ...

    async def oferty(self, auction_id: int) -> tuple[OfertaNaKarcie, ...]:
        """Oferty ze strony aukcji (SPEC.md §11.8). Pusto, gdy źródło ich nie daje."""
        ...

    async def powiazane_wystawienia(
        self, auction_id: int
    ) -> tuple[PowiazaneWystawienie, ...]:
        """Inne wystawienia tego samego auta (§12).

        Liczone przy odczycie, nie zapisywane: powiązanie zapisane w kolumnie
        starzałoby się cicho, gdy przemiat dopisze pasującą aukcję później.
        """
        ...

    async def zakresy_filtrow(self) -> dict[str, Zakres]:
        """Granice suwaków rocznika i mocy, wyliczone z danych (§12).

        Ta sama zasada co przy `wartosci_filtrow`: suwak ma pokrywać to, co
        w bazie faktycznie jest, a nie teoretyczny zakres roczników.
        """
        ...

    async def diagnostyka(self) -> tuple[StanZrodla, ...]:
        """Wiersze panelu z `reporting.v_source_health` (§12)."""
        ...

    async def rozmiar_bazy(self) -> int | None: ...

    async def sa_jakiekolwiek_aukcje(self) -> bool:
        """Czy cokolwiek już zebrano — do odróżnienia pustej listy od filtrów.

        „Nic nie pasuje do tych filtrów" i „jeszcze nic nie zebrano" wymagają
        od użytkownika zupełnie różnych działań, więc muszą być różnymi
        komunikatami.
        """
        ...

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


class ZrodloZeZdjeciami(AuctionSource, Protocol):
    """Źródło udostępniające galerię zdjęć pojazdu (SPEC.md §12).

    Osobny protokół, nie pole w `AuctionSource`: adapter serwisu bez galerii
    nie ma po co implementować pustej metody (§6.3, ta sama zasada co przy
    `AuthenticatedSource`).
    """

    async def zdjecia(self, external_id: str, url: str | None = None) -> Sequence[str]:
        """Adresy zdjęć. Pobierane **na żądanie**, nie przy zbieraniu.

        `url` to adres aukcji zapamiętany przy zbieraniu. Gdy go podamy,
        adapter idzie pod niego zamiast składać adres z identyfikatora —
        patrz `infrastructure/sources/adresy.py`.
        """
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
