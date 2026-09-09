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

from app.domain.enums import AuctionStatus, FinalPriceState, PollTier, RodzajPojazdu
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


@dataclass(slots=True, frozen=True)
class Zakres:
    """Granice suwaka, wyliczone z danych, a nie zgadnięte.

    Suwak od 1900 do 2100 jest bezużyteczny: cały ruch dzieje się na trzech
    procentach jego długości. Granice biorą się z tego, co faktycznie leży
    w bazie — `None` znaczy, że kolumna jest pusta i suwaka nie ma czego
    pokazywać.
    """

    minimum: int | None = None
    maksimum: int | None = None

    @property
    def uzyteczny(self) -> bool:
        """Czy jest co przesuwać. Jedna wartość to nie jest zakres."""
        return (
            self.minimum is not None
            and self.maksimum is not None
            and self.maksimum > self.minimum
        )


ETYKIETY_RODZAJU: dict[RodzajPojazdu, str] = {
    RodzajPojazdu.OSOBOWY: "osobowe",
    RodzajPojazdu.DOSTAWCZY: "dostawcze",
    RodzajPojazdu.CIEZAROWY: "ciężarowe",
    RodzajPojazdu.MOTOCYKL: "motocykle",
    RodzajPojazdu.PRZYCZEPA: "przyczepy i naczepy",
    RodzajPojazdu.AUTOBUS: "autobusy",
    RodzajPojazdu.INNY: "inne",
    RodzajPojazdu.NIEZNANY: "nierozpoznane",
}
"""Kolejność ma znaczenie — tak wypada lista rozwijana w filtrach."""

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
    model: str | None = None

    # --- filtry wielokrotnego wyboru --------------------------------------
    # Krotka, nie pojedyncza wartość: „diesel ALBO benzyna" to jedno
    # naturalne pytanie kupującego, a przy jednej wartości trzeba było
    # przeglądać listę dwa razy i samemu scalać wynik. Pusta krotka znaczy
    # „nie filtruj", a każdy element jest warunkiem OR wewnątrz wymiaru
    # (wymiary między sobą łączy AND).
    marki: tuple[str, ...] = ()
    zrodla: tuple[str, ...] = ()
    paliwa: tuple[str, ...] = ()
    skrzynie: tuple[str, ...] = ()
    lokalizacje: tuple[str, ...] = ()
    rodzaje: tuple[RodzajPojazdu, ...] = (RodzajPojazdu.OSOBOWY,)
    """Rodzaje pojazdu; pusta krotka znaczy „wszystkie rodzaje".

    **Domyślnie zawężone do osobowych** — to jedyny filtr z niepustą
    wartością domyślną i jest to decyzja świadoma: źródła sprzedają
    w tej samej kategorii naczepy i ciągniki siodłowe, więc lista bez
    zawężenia pokazuje je wymieszane z samochodami. Wybór „wszystkie"
    stoi w pasku filtrów obok, jednym kliknięciem.
    """

    cena_od: int | None = None
    cena_do: int | None = None
    rocznik_od: int | None = None
    rocznik_do: int | None = None
    moc_od: int | None = None
    moc_do: int | None = None
    """Moc silnika w KM. Zakres, bo o moc pyta się przedziałem („od 150
    wzwyż", „nie więcej niż 200"), a nie konkretną liczbą."""
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
    vehicle_kind: RodzajPojazdu = RodzajPojazdu.NIEZNANY
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
        """Czy cena mieści się jeszcze w Twoim limicie (SPEC.md §12).

        **Na aukcji cena tylko rośnie**, więc ten stan jest prawdziwy na
        starcie i w pewnym momencie przestaje być — i to właśnie ta chwila
        jest informacją. Patrz `ponad_limitem`.

        Bez ceny albo bez progu odpowiedź brzmi „nie wiadomo", a nie „tak".
        """
        if not self._porownywalne():
            return False
        assert self.price_current is not None and self.cena_docelowa is not None
        return self.price_current.amount <= self.cena_docelowa.amount

    @property
    def ponad_limitem(self) -> bool:
        """Licytacja przebiła Twój limit — aukcja wypadła z budżetu.

        To jest sygnał operacyjny, po który się tu przychodzi: przy
        kilkudziesięciu obserwowanych nie da się pamiętać, ile za którą
        chciało się dać, a licytacja przesuwa granicę bez ostrzeżenia.
        """
        if not self._porownywalne():
            return False
        assert self.price_current is not None and self.cena_docelowa is not None
        return self.price_current.amount > self.cena_docelowa.amount

    def _porownywalne(self) -> bool:
        """Cena i próg istnieją i są w tej samej walucie.

        Porównanie 50 000 PLN z 50 000 EUR dałoby odpowiedź wyglądającą na
        sensowną, więc wolimy nie odpowiadać wcale.
        """
        return (
            self.cena_docelowa is not None
            and self.price_current is not None
            and self.cena_docelowa.currency is self.price_current.currency
        )


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
    licytacja_proxy: bool = False
    """Serwis prowadzi licytację proxy — jeden wiersz na licytanta.

    Wynika z `bid_count_semantics = PARTICIPANTS` (§8.1). Karta musi to
    wiedzieć, żeby nie tłumaczyć reguł proxy tam, gdzie ich nie ma:
    przy zwykłym postąpieniu późniejsza oferta ZAWSZE jest wyższa.
    """


@dataclass(slots=True, frozen=True)
class PunktHistorii:
    """Jedna zmiana ceny obserwowanej aukcji (SPEC.md §8.4).

    Snapshoty powstają **wyłącznie przy zmianie** ceny, liczby ofert albo
    terminu — odpyt bez zmiany aktualizuje tylko `last_seen_at`. Historia
    jest więc listą zdarzeń, a nie pomiarem co N minut, i tak trzeba ją
    czytać: odstęp między wierszami mówi o licytacji, nie o harmonogramie.
    """

    ts: dt.datetime
    price: Money
    bid_count: int | None = None
    ends_at: dt.datetime | None = None
    bid_gap: int | None = None
    """Ile ofert przegapiono przed tym wpisem (§11.8); `None` = nie wiadomo."""


@dataclass(slots=True, frozen=True)
class OfertaNaKarcie:
    """Jedna oferta odczytana wprost ze strony aukcji (SPEC.md §11.8).

    Różnica wobec `PunktHistorii`: tam jest **nasz** odczyt ceny w chwili,
    w której akurat zapytaliśmy, tu — oferta z momentem złożenia podanym
    przez serwis. Karta pokazuje jedno albo drugie, nigdy oba naraz, bo
    zestawione obok siebie wyglądałyby jak dwie wersje tej samej prawdy.
    """

    uczestnik: str
    """Etykieta w rodzaju „Licytant A" — nadana po kolei pojawiania się.

    Nie jest to identyfikator z serwisu; ten nigdzie nie trafia
    (`012_oferty.sql`). Wystarcza, żeby zobaczyć, ilu było licytantów
    i kto przebijał kogo.
    """
    amount: Money
    placed_at: dt.datetime
    najwyzsza: bool = False
    """Najwyższa oferta w tej aukcji — czyli ta, która prowadzi albo wygrała.

    Bez tego znacznika tabela licytacji proxy czyta się jak błąd: oferta
    złożona później bywa **niższa**, bo ktoś podbił, ale nie przebił maksimum
    lidera i cena nie drgnęła.
    """
    podbita_przez_siebie: bool = False
    """Ten sam licytant złożył później wyższą ofertę; ten wiersz jest historią.

    Serwis takiego wiersza już nie pokazuje — nadpisuje go w miejscu. U nas
    zostaje, więc trzeba powiedzieć, że to stan wcześniejszy, a nie druga
    równoległa oferta tej samej osoby.
    """
    opoznienie_s: int | None = None
    """O ile spóźnił się nasz odczyt względem złożenia oferty (sekundy).

    Jedyna miara opóźnienia, jaką mamy — serwis nie mówi, kiedy oferta
    pojawiła się na stronie. `None`, gdy zobaczyliśmy ją w pierwszym odczycie
    aukcji, bo wtedy liczba mierzyłaby wiek aukcji, a nie nasze opóźnienie.
    """


class PewnoscPowiazania(StrEnum):
    """Skąd wiadomo, że to ten sam samochód."""

    VIN = "VIN"
    """Ten sam numer nadwozia. Pewne — VIN identyfikuje egzemplarz."""
    PODOBNE = "PODOBNE"
    """Zgadza się marka, model, rocznik, silnik, kolor i przebieg.

    **Prawdopodobne, nie pewne.** Flota leasingowa bywa złożona z aut
    kupionych razem: ten sam model, rocznik i zbliżony przebieg. Dlatego
    interfejs musi to pokazywać inaczej niż dopasowanie po VIN-ie.
    """


@dataclass(slots=True, frozen=True)
class PowiazaneWystawienie:
    """Ta sama fura wystawiona ponownie (SPEC.md §12).

    Niesprzedany samochód wraca na aukcję — czasem po tygodniu, czasem
    w innym serwisie. Bez powiązania obu wystawień archiwum kłamie przez
    przemilczenie: pokazuje „zakończona bez sprzedaży" i nie mówi, że ta
    sama sztuka poszła miesiąc później o 8 tysięcy taniej.
    """

    id: int
    source_key: str
    external_id: str
    url: str
    status: AuctionStatus
    pewnosc: PewnoscPowiazania
    pozniejsze: bool
    """`True`, gdy to wystawienie jest PÓŹNIEJSZE niż oglądane."""
    ends_at: dt.datetime | None = None
    price_current: Money | None = None
    final_price_state: FinalPriceState = FinalPriceState.UNKNOWN
    mileage_km: int | None = None


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
    """Kiedy zrobiono ostatnią kopię bazy (§7.1). `None` = jeszcze żadnej."""
    kopia_bajty: int | None = None
    pula: dict[str, int] = field(default_factory=dict)
    debug_dumps: bool = False
