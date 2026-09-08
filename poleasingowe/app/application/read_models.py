"""Modele odczytu dla interfejsu (SPEC.md §12).

Osobne od encji z `domain/` celowo. Encja opisuje aukcję taką, jaka jest;
model odczytu opisuje **wiersz na ekranie** — razem z tym, czego w encji
nie ma, bo należy do innej tabeli: czy aukcja jest obserwowana i czy zeszła
poniżej ceny docelowej. Gdyby to doklejać do `Auction`, encja zaczęłaby
zależeć od tego, kto na nią patrzy.

Wszystkie czasy są w UTC. Konwersja do strefy lokalnej należy wyłącznie do
szablonu (§8.2).
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.enums import AuctionStatus, FinalPriceState, PollTier
from app.domain.value_objects import Money

LIMIT_STRONY = 50
"""SPEC.md §12 — 50 wierszy na stronę."""


class Sortowanie(StrEnum):
    """Klucze sortowania listy (SPEC.md §12).

    Każdy klucz musi mieć **stabilny rozstrzygacz** w postaci `id`, inaczej
    paginacja keyset gubi albo powtarza wiersze przy remisach.
    """

    KONIEC_ROSNACO = "koniec"
    """Domyślne: najbliżej końca u góry — tak się tego używa operacyjnie."""
    KONIEC_MALEJACO = "koniec-desc"
    CENA_ROSNACO = "cena"
    CENA_MALEJACO = "cena-desc"
    ROCZNIK_MALEJACO = "rocznik-desc"
    PRZEBIEG_ROSNACO = "przebieg"
    NAJNOWSZE = "nowe"
    """Po `first_seen_at` malejąco — „co doszło od ostatniej wizyty"."""


ETYKIETY_SORTOWANIA: dict[Sortowanie, str] = {
    Sortowanie.KONIEC_ROSNACO: "najbliżej końca",
    Sortowanie.KONIEC_MALEJACO: "najpóźniej kończące się",
    Sortowanie.CENA_ROSNACO: "najtańsze",
    Sortowanie.CENA_MALEJACO: "najdroższe",
    Sortowanie.ROCZNIK_MALEJACO: "najnowszy rocznik",
    Sortowanie.PRZEBIEG_ROSNACO: "najmniejszy przebieg",
    Sortowanie.NAJNOWSZE: "ostatnio dodane",
}
"""Nazwy dla człowieka. `koniec-desc` w liście rozwijanej to nie interfejs."""

# Sortowania osiągalne z nagłówka kolumny: klik przełącza kierunek.
SORTOWANIE_KOLUMN: dict[str, tuple[Sortowanie, Sortowanie]] = {
    "koniec": (Sortowanie.KONIEC_ROSNACO, Sortowanie.KONIEC_MALEJACO),
    "cena": (Sortowanie.CENA_ROSNACO, Sortowanie.CENA_MALEJACO),
}


@dataclass(slots=True, frozen=True)
class Kursor:
    """Pozycja w wyniku dla paginacji keyset (SPEC.md §12 — nie `OFFSET`).

    Niesie wartość klucza sortowania ostatniego wiersza i jego `id`. `id`
    jest konieczne: bez niego dwa wiersze o tym samym `ends_at` albo się
    powtórzą, albo jeden wypadnie.

    `wartosc` bywa `None` i to jest znaczące — wiersze z pustym kluczem idą
    na koniec (`NULLS LAST`), więc kursor musi umieć powiedzieć „jestem już
    w ogonie z pustymi".
    """

    wartosc: str | None
    id: int

    def zakoduj(self) -> str:
        surowe = json.dumps([self.wartosc, self.id], separators=(",", ":"))
        return base64.urlsafe_b64encode(surowe.encode("utf-8")).decode("ascii")

    @classmethod
    def odkoduj(cls, tekst: str) -> Kursor | None:
        """Zwraca `None` dla śmieci zamiast rzucać.

        Kursor przychodzi z adresu URL, więc bywa uszkodzony przez kopiowanie
        albo po prostu stary. Pokazanie pierwszej strony jest lepsze niż 500.
        """
        try:
            surowe = base64.urlsafe_b64decode(tekst.encode("ascii"))
            wartosc, identyfikator = json.loads(surowe)
        except (ValueError, binascii.Error, UnicodeEncodeError):
            return None
        if not isinstance(identyfikator, int) or not isinstance(
            wartosc, str | type(None)
        ):
            return None
        return cls(wartosc=wartosc, id=identyfikator)


@dataclass(slots=True, frozen=True)
class Kryteria:
    """Filtry listy (SPEC.md §12).

    Puste pole znaczy „nie filtruj". `tylko_obserwowane` i `konczy_sie_w_h`
    to gotowe widoki z §12, wyrażone jako zwykłe filtry — nie osobne trasy,
    bo wtedy każda musiałaby powtarzać całą resztę.
    """

    szukaj: str | None = None
    marka: str | None = None
    model: str | None = None
    zrodlo: str | None = None
    paliwo: str | None = None
    skrzynia: str | None = None
    lokalizacja: str | None = None
    cena_od: int | None = None
    cena_do: int | None = None
    rocznik_od: int | None = None
    rocznik_do: int | None = None
    przebieg_do: int | None = None
    konczy_sie_w_h: int | None = None
    status: AuctionStatus | None = AuctionStatus.ACTIVE
    """`None` znaczy „wszystkie statusy"; `ENDED` to archiwum z §12."""
    tylko_obserwowane: bool = False
    nowe_od: dt.datetime | None = None
    """Widok „nowe od ostatniej wizyty" — porównanie z `first_seen_at`."""
    sortowanie: Sortowanie = Sortowanie.KONIEC_ROSNACO


@dataclass(slots=True, frozen=True)
class PozycjaListy:
    """Jeden wiersz listy."""

    id: int
    source_key: str
    external_id: str
    url: str
    status: AuctionStatus
    make: str | None = None
    model: str | None = None
    variant: str | None = None
    year: int | None = None
    mileage_km: int | None = None
    fuel: str | None = None
    gearbox: str | None = None
    location: str | None = None
    price_start: Money | None = None
    price_current: Money | None = None
    bid_count: int | None = None
    ends_at: dt.datetime | None = None
    first_seen_at: dt.datetime | None = None
    final_price_state: FinalPriceState = FinalPriceState.UNKNOWN
    obserwowana: bool = False
    cena_docelowa: Money | None = None
    notatka: str | None = None

    @property
    def nazwa(self) -> str:
        """Marka, model i wersja w jednym — do nagłówka wiersza."""
        czesci = [c for c in (self.make, self.model, self.variant) if c]
        return " ".join(czesci) or self.external_id

    @property
    def ponizej_progu(self) -> bool:
        """SPEC.md §12 — wyróżnienie po przekroczeniu progu.

        Bez ceny albo bez progu odpowiedź brzmi „nie wiadomo", a nie „tak".
        """
        if self.cena_docelowa is None or self.price_current is None:
            return False
        if self.cena_docelowa.currency is not self.price_current.currency:
            return False
        return self.price_current.amount <= self.cena_docelowa.amount


@dataclass(slots=True, frozen=True)
class Strona:
    """Wynik zapytania o listę.

    Bez łącznej liczby wyników: `COUNT(*)` po tych samych filtrach kosztuje
    tyle co samo zapytanie, a paginacja keyset i tak go nie potrzebuje.
    `ma_wiecej` bierze się z pobrania jednego wiersza ponad limit.
    """

    pozycje: tuple[PozycjaListy, ...] = ()
    kursor_dalej: str | None = None

    @property
    def ma_wiecej(self) -> bool:
        return self.kursor_dalej is not None


@dataclass(slots=True, frozen=True)
class Szczegoly:
    """Karta aukcji (SPEC.md §12) — wszystkie pola plus stan harmonogramu."""

    pozycja: PozycjaListy
    vin: str | None = None
    body: str | None = None
    color: str | None = None
    engine_ccm: int | None = None
    engine_hp: int | None = None
    seller: str | None = None
    bid_increment_raw: str | None = None
    last_seen_at: dt.datetime | None = None
    next_poll_at: dt.datetime | None = None
    poll_tier: PollTier = PollTier.IDLE
    last_price_lead_seconds: int | None = None
    duplicate_of: int | None = None


@dataclass(slots=True, frozen=True)
class PorownanieRynkowe:
    """Zagregowana cena podobnych, zakończonych aukcji dla wyceny AI."""

    year: int | None
    mediana_potwierdzona: int | None
    liczba_potwierdzonych: int
    mediana_ostatnia: int | None
    liczba_ostatnich: int


@dataclass(slots=True, frozen=True)
class StanZrodla:
    """Wiersz panelu diagnostycznego (SPEC.md §12)."""

    source_key: str
    source_name: str
    enabled: bool
    auth_state: str
    consecutive_auth_failures: int
    rate_limit_per_minute: int
    floor_seconds: int
    aktywne_aukcje: int
    closing_ladder_seconds: tuple[int, ...] = ()
    bid_count_semantics: str = "UNKNOWN"
    last_run_started_at: dt.datetime | None = None
    last_run_finished_at: dt.datetime | None = None
    last_run_new: int | None = None
    last_run_changed: int | None = None
    last_run_errors: int | None = None
    last_run_rss_bytes: int | None = None
    last_run_database_bytes: int | None = None

    @property
    def zablokowane(self) -> bool:
        """SPEC.md §10.2 — `LOCKED` wymaga świadomego resetu z interfejsu."""
        return self.auth_state == "LOCKED"


@dataclass(slots=True, frozen=True)
class Diagnostyka:
    """Stan procesu i bazy (SPEC.md §12).

    Pola, których jeszcze nie ma czym wypełnić, są `None` i tak też mają się
    pokazać — „brak danych" jest uczciwe, wyzerowany licznik nie.
    """

    polaczenie: str
    baza_dostepna: bool
    ostatni_blad_bazy: str | None = None
    zrodla: tuple[StanZrodla, ...] = ()
    rozmiar_bazy_bajty: int | None = None
    rss_bajty: int | None = None
    dryf_zegara_s: float | None = None
    ostatni_pg_dump: dt.datetime | None = None
    """SPEC.md §14 pkt 11 — backup jeszcze nie istnieje, więc `None`."""
    pula: dict[str, int] = field(default_factory=dict)
    debug_dumps: bool = False
