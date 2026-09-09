"""Warstwa antykorupcyjna poleasingowe.pl (SPEC.md §6.2).

Dziwactwa serwisu żyją tutaj i nie wyciekają dalej:

- nazwa pojazdu w tytule strony niesie **rocznik i nadwozie sklejone
  z marką i modelem** („VOLKSWAGEN GOLF 2022  KOMBI"), a oba są jednocześnie
  osobnymi polami tabeli;
- lista podaje **datę końca bez godziny** („19 godzin (2026-09-07)"), więc
  z listy nie da się ustalić `ends_at` i nie udajemy, że się da;
- `auction_pending` jest **jedynym** markerem stanu końcowego — serwis nie
  dodaje żadnej etykiety tekstowej (RECON.md §4.2).
"""

from __future__ import annotations

import datetime as dt
import re
import zoneinfo

from app.application.ports import SurowaOferta
from app.domain.entities import Auction, OfertaUczestnika
from app.domain.enums import AuctionStatus, Currency
from app.domain.errors import ParseFailed
from app.domain.value_objects import Mileage, Money, NieprawidlowaWartosc, Vin
from app.infrastructure.sources.marki import podziel_marke_model
from app.infrastructure.sources.paliwa import kanoniczne_paliwo
from app.infrastructure.sources.rodzaje import rozpoznaj

# Serwis podaje `endDate` z jawną strefą: `moment('...').tz("Europe/Warsaw")`.
# Do bazy idzie UTC (SPEC.md §8.2).
STREFA_SERWISU = zoneinfo.ZoneInfo("Europe/Warsaw")

_LICZBA = re.compile(r"\d[\d\s\xa0]*")
_ROCZNIK = re.compile(r"\b(19|20)\d{2}\b")
_KONIEC = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$")


def _int_lub_none(tekst: str | None) -> int | None:
    if not tekst:
        return None
    trafienie = _LICZBA.search(tekst)
    if trafienie is None:
        return None
    return int(re.sub(r"[^\d]", "", trafienie.group(0)))


def _koniec_na_utc(wartosc: str) -> dt.datetime:
    dopasowanie = _KONIEC.match(wartosc.strip())
    if dopasowanie is None:
        raise ParseFailed(f"poleasingowe: nieznany format czasu końca: {wartosc!r}")
    r, m, d, gg, mm, ss = (int(x) for x in dopasowanie.groups())
    return dt.datetime(r, m, d, gg, mm, ss, tzinfo=STREFA_SERWISU).astimezone(dt.UTC)


def _nazwa_bez_powtorzen(nazwa: str, pola: dict[str, str]) -> str:
    """Usuwa z nazwy to, co i tak stoi w osobnych polach.

    „VOLKSWAGEN GOLF 2022  KOMBI" przy `Rok produkcji = 2022` i `Typ = KOMBI`
    zostawia „VOLKSWAGEN GOLF". Bez tego rocznik wylądowałby jako model,
    a nadwozie jako wersja wyposażenia.
    """
    okrojona = _ROCZNIK.sub(" ", nazwa)
    nadwozie = pola.get("Typ", "").strip()
    if nadwozie:
        okrojona = re.sub(rf"\b{re.escape(nadwozie)}\b", " ", okrojona, flags=re.I)
    return re.sub(r"\s+", " ", okrojona).strip()


def _cena(pola: dict[str, str]) -> Money | None:
    surowa = pola.get("cena")
    if not surowa:
        return None
    try:
        return Money.z_tekstu(surowa, Currency.PLN)
    except NieprawidlowaWartosc:
        # Cena nie do odczytania nie ma wywracać całego mapowania — reszta
        # pól jest nadal użyteczna, a brak ceny widać w interfejsie.
        return None


def _vin(pola: dict[str, str]) -> Vin | None:
    surowy = (pola.get("VIN") or "").strip()
    if not surowy:
        return None
    try:
        return Vin(surowy)
    except NieprawidlowaWartosc:
        # SPEC.md §8.4 dedupikuje po VIN, więc niechlujny VIN jest gorszy
        # niż jego brak: skleiłby ze sobą dwa różne pojazdy.
        return None


def _paliwo(wartosc: str | None) -> str | None:
    """Nazwa paliwa w postaci wspólnej dla wszystkich źródeł (§6.2)."""
    return kanoniczne_paliwo(wartosc) if wartosc else None


def na_aukcje(surowa: SurowaOferta, source_id: int, teraz: dt.datetime) -> Auction:
    """Buduje encję domenową z surowej pozycji poleasingowe.pl."""
    pola = surowa.pola

    marka = model = wersja = None
    if nazwa := pola.get("nazwa"):
        marka, model, wersja = podziel_marke_model(_nazwa_bez_powtorzen(nazwa, pola))

    przebieg = None
    if km := _int_lub_none(pola.get("Przebieg") or pola.get("przebieg")):
        przebieg = Mileage(km)

    ends_at = None
    if koniec := pola.get("end_date"):
        ends_at = _koniec_na_utc(koniec)

    # Jedyny marker stanu końcowego w tym serwisie (RECON.md §4.2).
    # Jego brak — czyli pozycja z listy — nie znaczy „zakończona".
    trwa = pola.get("auction_pending")
    status = AuctionStatus.ENDED if trwa == "false" else AuctionStatus.ACTIVE

    # Kategoria listy rozstrzyga tylko dla motocykli — `vehicles` mieszają
    # osobowe, dostawcze i ciągniki siodłowe, więc tam decyduje nazwa.
    # `Typ` (nadwozie) stoi wyłącznie na stronie szczegółów, której dla
    # większości aukcji nigdy nie pobieramy (§11.2), więc przy zwykłym
    # przemiataniu jedynym sygnałem jest nadwozie wpisane w tytuł.
    rodzaj = rozpoznaj(
        kategoria=pola.get("kategoria"),
        nazwa=" ".join(x for x in (pola.get("nazwa"), pola.get("Typ")) if x),
    )

    return Auction(
        source_id=source_id,
        external_id=surowa.external_id,
        url=surowa.url,
        status=status,
        first_seen_at=teraz,
        last_seen_at=teraz,
        make=marka,
        model=model,
        variant=wersja,
        year=_int_lub_none(pola.get("Rok produkcji") or pola.get("rocznik")),
        mileage=przebieg,
        fuel=_paliwo(pola.get("Paliwo") or pola.get("paliwo")),
        gearbox=pola.get("Skrzynia biegów"),
        engine_ccm=_int_lub_none(pola.get("Pojemność silnika")),
        engine_hp=_int_lub_none(pola.get("Moc silnika")),
        vin=_vin(pola),
        body=pola.get("Typ"),
        vehicle_kind=rodzaj,
        color=pola.get("Kolor"),
        location=pola.get("Lokalizacja"),
        price_current=_cena(pola),
        bid_count=_int_lub_none(pola.get("offers_count") or pola.get("Ilość ofert")),
        # SPEC.md §8.2 — postąpienie zapisujemy tak, jak podał je serwis,
        # i nie liczymy z niego niczego.
        bid_increment_raw=pola.get("instep_price"),
        ends_at=ends_at,
        content_hash=surowa.content_hash or None,
    )


# Data oferty w `lastOffers`: „poniedziałek 7 wrzesień 2026 12:00:41" — dzień
# tygodnia z przodu (do wyrzucenia) i nazwa miesiąca w MIANOWNIKU, nie
# w dopełniaczu. `%B` z `locale` odpada: locale jest stanem globalnym procesu
# i w kontenerze add-onu nie ma gwarancji, że polskie w ogóle istnieje.
_MIESIACE = {
    "styczeń": 1,
    "luty": 2,
    "marzec": 3,
    "kwiecień": 4,
    "maj": 5,
    "czerwiec": 6,
    "lipiec": 7,
    "sierpień": 8,
    "wrzesień": 9,
    "październik": 10,
    "listopad": 11,
    "grudzień": 12,
}
_DATA_OFERTY = re.compile(
    r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})", re.I
)


def _czas_oferty_na_utc(wartosc: str) -> dt.datetime:
    dopasowanie = _DATA_OFERTY.search(wartosc)
    if dopasowanie is None:
        raise ParseFailed(f"poleasingowe: nieznany format czasu oferty: {wartosc!r}")
    dzien, miesiac, rok, gg, mm, ss = dopasowanie.groups()
    numer = _MIESIACE.get(miesiac.lower())
    if numer is None:
        raise ParseFailed(f"poleasingowe: nieznany miesiąc: {miesiac!r}")
    lokalny = dt.datetime(
        int(rok), numer, int(dzien), int(gg), int(mm), int(ss), tzinfo=STREFA_SERWISU
    )
    return lokalny.astimezone(dt.UTC)


def na_oferty(
    surowa: SurowaOferta, auction_id: int, teraz: dt.datetime
) -> tuple[OfertaUczestnika, ...]:
    """Oferty z `lastOffers` na encje domenowe (SPEC.md §11.8).

    W przeciwieństwie do tabeli EFL (RECON.md §3.5a) to jest **chronologia
    licytacji**: kwoty rosną razem z czasem, a każda oferta ma stały
    identyfikator nadany przez serwis.

    Nazwa licytanta przychodzi już zredagowana przez serwis („u...k"), więc
    nie ma tu czego pseudonimizować ani czego rozróżniać — i tak zapisujemy
    ją bez zmian, zamiast udawać, że wiemy, kto licytował.
    """
    wynik: list[OfertaUczestnika] = []
    for pozycja in surowa.oferty:
        try:
            kwota = Money.z_tekstu(pozycja.kwota, Currency.PLN)
            zlozona = _czas_oferty_na_utc(pozycja.zlozona)
        except (NieprawidlowaWartosc, ParseFailed):
            # Lista ofert jest dodatkiem do ceny i terminu (§11.8) — jeden
            # nieczytelny wiersz nie może kosztować całego odpytu.
            continue
        wynik.append(
            OfertaUczestnika(
                auction_id=auction_id,
                uczestnik=pozycja.kod or "nieznany",
                amount=kwota,
                placed_at=zlozona,
                first_seen_at=teraz,
                external_offer_id=pozycja.identyfikator or None,
            )
        )
    return tuple(wynik)
