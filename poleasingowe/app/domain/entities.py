"""Encje domenowe (SPEC.md §8.1).

Wszystkie jako `@dataclass(slots=True, frozen=True)` — SPEC.md §5 zabrania
używania do tego pydantica. Czas zawsze `timestamptz` w UTC; konwersja do
strefy lokalnej wyłącznie w warstwie widoku (§8.2).
"""

from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass, field, replace
from typing import Any

from app.domain.enums import (
    AuctionStatus,
    AuthState,
    BidCountSemantics,
    FinalPriceState,
    PollTier,
    RodzajPojazdu,
    SweepStatus,
)
from app.domain.value_objects import Mileage, Money, Vin


def _wymagaj_utc(nazwa: str, wartosc: dt.datetime | None) -> None:
    """SPEC.md §8.2 — wszystko zapisywane w UTC, nigdy naiwnie."""
    if wartosc is None:
        return
    if wartosc.tzinfo is None or wartosc.utcoffset() is None:
        raise ValueError(f"{nazwa}: czas musi być świadomy strefy (SPEC.md §8.2)")


@dataclass(slots=True, frozen=True)
class Source:
    """Serwis aukcyjny wraz z jego regułami tempa i dogrywki.

    Parametry dogrywki są **kolumnami, nie stałymi w kodzie** (SPEC.md §11.2),
    bo cztery zbadane serwisy mają cztery różne reguły, a jeden nie ma
    dogrywki wcale (RECON.md §3.2).
    """

    key: str
    name: str
    enabled: bool
    sweep_interval_seconds: int
    rate_limit_per_minute: int
    floor_seconds: int
    auth_state: AuthState
    consecutive_auth_failures: int
    overtime_window_seconds: int
    """Okno, w którym oferta przedłuża aukcję. 0 = serwis bez dogrywki."""
    overtime_extension_seconds: int
    """O ile przedłuża. 0 = serwis bez dogrywki."""
    overtime_cap_seconds: int | None
    """Sufit łącznego przedłużenia. `None` = serwis go nie deklaruje."""
    closing_ladder_seconds: tuple[int, ...] = (2, 5, 10, 20, 40)
    """Przesunięcia prób fazy 2 od punktu zerowego (SPEC.md §11.5).

    Bezwzględne i rosnące, nie odstępy. Domyślna siatka obowiązuje wyłącznie
    dla źródeł niezmierzonych — dla zmierzonego jest błędem konfiguracji
    (RECON.md §3.6).
    """
    bid_history_ttl_seconds: int | None = None
    """Jak długo po końcu widoczna jest historia ofert. `None` = nie znika."""
    bid_count_semantics: BidCountSemantics = BidCountSemantics.UNKNOWN
    """Co serwis liczy w `bid_count` (§11.8)."""
    last_sweep_at: dt.datetime | None = None
    """Ostatni **pełny** przemiat. Historyczna wartość nie jest dowodem.

    Po migracji starsze wartości mają status `UNKNOWN`; dopiero dwa kolejne
    pełne przemiaty mogą prowadzić do `DISAPPEARED`.
    """
    last_sweep_attempt_at: dt.datetime | None = None
    """Kiedy ostatnio rozpoczęto próbę przemiatu, także nieudaną."""
    last_sweep_status: SweepStatus = SweepStatus.UNKNOWN
    id: int | None = None

    def __post_init__(self) -> None:
        _wymagaj_utc("last_sweep_at", self.last_sweep_at)
        _wymagaj_utc("last_sweep_attempt_at", self.last_sweep_attempt_at)
        drabinka = self.closing_ladder_seconds
        if any(a >= b for a, b in itertools.pairwise(drabinka)):
            raise ValueError(
                f"closing_ladder_seconds musi rosnąć (SPEC.md §11.5): {drabinka}"
            )
        if any(s <= 0 for s in drabinka):
            raise ValueError(f"closing_ladder_seconds musi być dodatnia: {drabinka}")
        if self.bid_history_ttl_seconds is not None and (
            self.bid_history_ttl_seconds <= 0
        ):
            raise ValueError(
                "bid_history_ttl_seconds: dodatnie albo None (None = nie znika)"
            )

    @property
    def ma_dogrywke(self) -> bool:
        return self.overtime_window_seconds > 0 and self.overtime_extension_seconds > 0

    @property
    def liczy_oferty(self) -> bool:
        """Czy `bid_gap` wolno policzyć dla tego źródła (SPEC.md §11.8).

        `UNKNOWN` celowo daje `False`: brak dowodu z rekonesansu ma skutkować
        `NULL`-em, a nie zerem czytanym jak „komplet historii".
        """
        return self.bid_count_semantics is BidCountSemantics.OFFERS


@dataclass(slots=True, frozen=True)
class Auction:
    """Pojedyncza aukcja. Klucz naturalny to `(source_id, external_id)` (§8.3)."""

    source_id: int
    external_id: str
    url: str
    status: AuctionStatus
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime

    make: str | None = None
    model: str | None = None
    variant: str | None = None
    year: int | None = None
    mileage: Mileage | None = None
    fuel: str | None = None
    gearbox: str | None = None
    engine_ccm: int | None = None
    engine_hp: int | None = None
    vin: Vin | None = None
    body: str | None = None
    color: str | None = None
    location: str | None = None
    seller: str | None = None
    vehicle_kind: RodzajPojazdu = RodzajPojazdu.NIEZNANY
    """Osobowy, dostawczy, motocykl, przyczepa… (SPEC.md §12).

    Ustala go warstwa antykorupcyjna źródła — patrz `sources/rodzaje.py`.
    Domyślne `NIEZNANY` jest świadome: aukcja bez rozpoznanego rodzaju ma
    być widoczna, a nie po cichu odfiltrowana.
    """

    price_start: Money | None = None
    price_current: Money | None = None
    bid_count: int | None = None
    bid_increment_raw: str | None = None
    """Minimalne postąpienie w postaci, w jakiej podał je serwis (§8.2).

    Pole **informacyjne**, bez logiki domenowej: EFL ma progi kwotowe,
    poleasingowe podaje `instep_price`, autoprzetarg liczy 2% ostatniej
    oferty (RECON.md §4.4). Nie liczymy z tego niczego.
    """

    ends_at: dt.datetime | None = None
    next_poll_at: dt.datetime | None = None
    poll_tier: PollTier = PollTier.IDLE
    consecutive_failures: int = 0

    final_price_state: FinalPriceState = FinalPriceState.UNKNOWN
    last_price_lead_seconds: int | None = None
    """Sekundy między ostatnią obserwacją z ceną a faktycznym końcem.

    `None` dla `CONFIRMED` — tam pomiar jest z definicji dokładny (§8.2).
    """

    content_hash: str | None = None
    raw_json: dict[str, Any] | None = None
    duplicate_of: int | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        for nazwa in ("first_seen_at", "last_seen_at", "ends_at", "next_poll_at"):
            _wymagaj_utc(nazwa, getattr(self, nazwa))
        if not self.external_id:
            raise ValueError("external_id nie może być pusty — to klucz naturalny")
        if (
            self.final_price_state is FinalPriceState.CONFIRMED
            and self.last_price_lead_seconds is not None
        ):
            raise ValueError(
                "last_price_lead_seconds ma być NULL dla CONFIRMED (SPEC.md §8.2)"
            )
        if self.bid_count is not None and self.bid_count < 0:
            raise ValueError(f"bid_count ujemny: {self.bid_count}")


@dataclass(slots=True, frozen=True)
class PriceSnapshot:
    """Zapis stanu ceny w czasie.

    SPEC.md §8.4: zapisywany **wyłącznie** gdy zmieniła się cena, liczba ofert
    albo `ends_at`. Odpyt bez zmiany aktualizuje tylko `last_seen_at`.
    """

    auction_id: int
    ts: dt.datetime
    price: Money
    bid_count: int | None = None
    ends_at: dt.datetime | None = None
    bid_gap: int | None = None
    """Ile ofert przegapiono przed tym snapshotem (SPEC.md §11.8).

    `None` dla **pierwszego** snapshotu aukcji — w chwili pierwszej
    obserwacji aukcja mogła już mieć oferty, więc `0` byłoby kłamstwem.
    """
    id: int | None = None

    def __post_init__(self) -> None:
        _wymagaj_utc("ts", self.ts)
        _wymagaj_utc("ends_at", self.ends_at)
        if self.bid_gap is not None and self.bid_gap < 0:
            raise ValueError(f"bid_gap ujemny: {self.bid_gap}")


def z_cena_wywolawcza(auction: Auction) -> Auction:
    """Uzupełnia `price_start`, gdy da się go wywnioskować **pewnie**.

    Aukcja bez ani jednej oferty stoi na swojej cenie wywoławczej — nie ma
    innej możliwości, bo licytować można wyłącznie w górę. `bid_count = 0`
    razem z ceną bieżącą daje więc cenę wywoławczą **z pewnością**, a nie
    z oszacowania, i tylko dlatego wolno ją tu dopisać.

    Trzy sytuacje, w których nic nie robimy, każda z innego powodu:

    - `bid_count` jest `NULL` — serwis nie mówi, ile było ofert
      (autoprzetarg bez logowania, RECON.md §4.4). Brak liczby to nie zero.
    - `bid_count > 0` — ktoś już licytował, więc cena bieżąca jest wyższa od
      wywoławczej o nieznaną nam wartość. Zapisanie jej byłoby kłamstwem.
    - `price_start` już jest — pierwsza obserwacja bywa jedyną, w której
      aukcja nie miała jeszcze ofert, i nie wolno jej nadpisać późniejszą.
    """
    if (
        auction.price_start is not None
        or auction.bid_count != 0
        or auction.price_current is None
    ):
        return auction
    return replace(auction, price_start=auction.price_current)


@dataclass(slots=True, frozen=True)
class OfertaUczestnika:
    """Jedna oferta z listy ofert na stronie aukcji (SPEC.md §11.8).

    NIE jest tym samym co `PriceSnapshot`. Snapshot to **nasz** odczyt ceny
    w danej chwili; ta encja to oferta, którą serwis pokazuje wprost — z kwotą
    i momentem złożenia, których nie musimy zgadywać z różnic między odpytami.
    §11.8 stawia taką listę ponad częstszym odpytywaniem, i słusznie: daje ją
    ta sama odpowiedź HTTP, która i tak leci po cenę.

    `uczestnik` to pseudonim ważny **wyłącznie w obrębie jednej aukcji** —
    patrz `012_oferty.sql`. Pozwala powiedzieć „te dwie oferty złożył ten sam
    licytant", nie pozwala śledzić licytanta między aukcjami.
    """

    auction_id: int
    uczestnik: str
    amount: Money
    placed_at: dt.datetime
    """Kiedy oferta została złożona — **wg serwisu**, nie wg naszego zegara."""
    first_seen_at: dt.datetime
    """Kiedy MY zobaczyliśmy ją pierwszy raz. Różnica mierzy nasze opóźnienie."""
    external_offer_id: str | None = None
    """Identyfikator oferty nadany przez serwis, o ile go podaje (`013`).

    Lepszy klucz niż `(uczestnik, placed_at)`: rozróżnia dwie oferty złożone
    w tej samej sekundzie, a przy licytacji z postąpieniami co kilka sekund
    to nie jest przypadek teoretyczny.
    """
    id: int | None = None

    def __post_init__(self) -> None:
        _wymagaj_utc("placed_at", self.placed_at)
        _wymagaj_utc("first_seen_at", self.first_seen_at)
        if not self.uczestnik:
            raise ValueError("pusty pseudonim uczestnika")


@dataclass(slots=True, frozen=True)
class WatchlistEntry:
    auction_id: int
    added_at: dt.datetime
    note: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        _wymagaj_utc("added_at", self.added_at)


@dataclass(slots=True, frozen=True)
class SavedFilter:
    name: str
    criteria: dict[str, Any]
    created_at: dt.datetime
    id: int | None = None

    def __post_init__(self) -> None:
        _wymagaj_utc("created_at", self.created_at)


@dataclass(slots=True, frozen=True)
class WycenaAukcji:
    """Wycena z modelu językowego — **opinia**, nie zmierzony fakt.

    Encja, a nie model odczytu, bo od 0.13 wycena jest trwała: liczy się raz
    i zostaje. Wcześniej leżała w cache'u kluczowanym danymi wejściowymi,
    a te zmieniały się przy każdej zakończonej aukcji tego modelu — więc
    karta pokazywała za każdym razem inną kwotę i generowała kolejne płatne
    żądanie.

    `pewnosc` i waluta są ograniczone także w bazie (`010_wyceny.sql`):
    niespójny przedział wygląda wiarygodnie i dlatego jest groźniejszy niż
    brak wyceny.
    """

    auction_id: int
    wartosc: Money
    minimum: Money
    maksimum: Money
    pewnosc: str
    uzasadnienie: str
    zalozenia: tuple[str, ...]
    model: str
    wersja_promptu: int
    utworzono: dt.datetime
    cena_portale: Money | None = None
    """Poziom cen ofertowych na portalach — szacunek modelu, nie odczyt."""

    def __post_init__(self) -> None:
        _wymagaj_utc("utworzono", self.utworzono)
        if not (self.minimum.amount <= self.wartosc.amount <= self.maksimum.amount):
            raise ValueError(
                "przedział wyceny musi spełniać minimum <= wartość <= maksimum"
            )


@dataclass(slots=True, frozen=True)
class RunLog:
    """Przebieg odpytu. SPEC.md §13 — budżet z §1.1 ma być mierzalny."""

    source_id: int
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    new_count: int = 0
    changed_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    rss_bytes: int | None = None
    database_bytes: int | None = None
    notes: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        _wymagaj_utc("started_at", self.started_at)
        _wymagaj_utc("finished_at", self.finished_at)
