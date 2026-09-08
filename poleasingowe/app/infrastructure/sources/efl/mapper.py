"""Warstwa antykorupcyjna EFL (SPEC.md §6.2).

Tłumaczy surowy kształt serwisu na model domenowy. Dziwactwa EFL — formaty
kwot, sklejona marka z modelem, moc zaszyta w opisie silnika — **żyją tutaj
i nie wyciekają poza ten plik**.
"""

from __future__ import annotations

import datetime as dt
import re
import zoneinfo

from app.application.ports import SurowaOferta
from app.domain.entities import Auction
from app.domain.enums import AuctionStatus, Currency
from app.domain.errors import ParseFailed
from app.domain.value_objects import Mileage, Money, NieprawidlowaWartosc, Vin
from app.infrastructure.sources.marki import podziel_marke_model
from app.infrastructure.sources.paliwa import kanoniczne_paliwo
from app.infrastructure.sources.rodzaje import rozpoznaj

# RECON.md §4.1: serwis podaje czas bez strefy. Zakladamy czas lokalny Polski,
# bo taki pokazuje uzytkownikowi. Do bazy idzie UTC (SPEC.md §8.2).
STREFA_SERWISU = zoneinfo.ZoneInfo("Europe/Warsaw")

_PRZEBIEG = re.compile(r"(\d[\d\s\xa0]*)\s*km", re.I)
# Tytul z LISTY sklada trzy rzeczy w jedno zdanie (zmierzone w fixtures):
#
#     Audi A4 35 TDI mHEV Advanced S tronic 2022r. FAT0933N Pojazd znajduje
#     sie w firmie ARCTOS GROUP sp. z o.o. Al. Krakowska 7, 02-183 Warszawa
#     Audi A3 30 TDI S tronic Hatchback 2022r. UY5XE05 Magnice
#
# Rocznik z kropka („2022r.") jest w tym granica pewna i wystepuje w KAZDEJ
# pozycji listy — dlatego dzielimy po nim, a nie po kolejnych slowach.
_ROCZNIK_W_TYTULE = re.compile(r"\b(?:19|20)\d{2}\s*r\.")
# Tablica rejestracyjna: 2-3 litery i 4-5 znakow alfanumerycznych, w tym co
# najmniej jedna cyfra. Modele („A4", „RS6", „X6", „M60I") sa krotsze i nie
# wpadaja w ten wzorzec.
_TABLICA = re.compile(r"\b(?=[A-Z0-9]{6,8}\b)[A-Z]{2,3}[A-Z0-9]*\d[A-Z0-9]*\b")
_KOD_POCZTOWY = re.compile(r"\d{2}-\d{3}\s+(.+)$")
_LOKALIZACJA_W_FIRMIE = re.compile(r"pojazd\s+znajduje\s+si[eę]", re.I)
_POJEMNOSC = re.compile(r"(\d[\d\s\xa0]*)\s*ccm", re.I)
_MOC = re.compile(r"(\d+)\s*KM", re.I)
_KONIEC = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})$")


def _int_lub_none(tekst: str | None) -> int | None:
    if not tekst:
        return None
    cyfry = re.sub(r"[^\d]", "", tekst)
    return int(cyfry) if cyfry else None


def _koniec_na_utc(wartosc: str) -> dt.datetime:
    dopasowanie = _KONIEC.match(wartosc.strip())
    if dopasowanie is None:
        raise ParseFailed(f"EFL: nieznany format czasu zakończenia: {wartosc!r}")
    d, m, r, gg, mm, ss = (int(x) for x in dopasowanie.groups())
    lokalny = dt.datetime(r, m, d, gg, mm, ss, tzinfo=STREFA_SERWISU)
    return lokalny.astimezone(dt.UTC)


def _paliwo(wartosc: str | None) -> str | None:
    """Nazwa paliwa w postaci wspólnej dla wszystkich źródeł (§6.2)."""
    return kanoniczne_paliwo(wartosc) if wartosc else None


def rozbierz_tytul(tytul: str) -> tuple[str, str | None]:
    """Dzieli tytuł EFL na nazwę pojazdu i lokalizację.

    **To jest naprawa „dziwnych tytułów".** EFL nie ma na liście osobnego
    pola z nazwą pojazdu — jest jedno zdanie, w którym za nazwą stoi rocznik,
    tablica rejestracyjna i adres miejsca postoju:

        `Audi A4 35 TDI mHEV Advanced S tronic 2022r. FAT0933N Pojazd
        znajduje się w firmie ARCTOS GROUP sp. z o.o. Al. Krakowska 7,
        02-183 Warszawa`

    Wrzucone w całości do `podziel_marke_model` dawało markę `Audi`, model
    `A4` i **wersję wyposażenia z adresem firmy w środku**. Tak wyglądała
    większość wierszy z EFL, bo strony szczegółów — jedyne miejsce z czystym
    polem `Marka, Model, Wersja wyposażenia` — pobieramy tylko dla aukcji
    obserwowanych (§11.2).

    Przy okazji odzyskujemy `location`, którego lista w ogóle nie podaje jako
    pola: `Magnice` albo miasto z adresu za kodem pocztowym.

    Tablicy rejestracyjnej **nie zapisujemy nigdzie** — nie mamy na nią
    kolumny, a jako identyfikator konkretnego egzemplarza nie jest nam do
    niczego potrzebna.
    """
    tekst = re.sub(r"\s+", " ", tytul).strip()

    podzial = _ROCZNIK_W_TYTULE.search(tekst)
    if podzial is not None:
        nazwa, ogon = tekst[: podzial.start()], tekst[podzial.end() :]
    else:
        # Tytuł ze strony szczegółów: `UY5XE05 Audi A3 30 TDI S tronic`.
        # Tu tablica stoi z przodu, a rocznika nie ma w ogóle.
        nazwa, ogon = _TABLICA.sub(" ", tekst, count=1), ""

    ogon = _TABLICA.sub(" ", ogon, count=1).strip(" ,")
    if _LOKALIZACJA_W_FIRMIE.search(ogon):
        # „…w firmie X sp. z o.o. Al. Krakowska 7, 02-183 Warszawa" —
        # bierzemy miasto zza kodu pocztowego, a nie nazwę firmy: to
        # lokalizacja pojazdu, nie sprzedający.
        kod = _KOD_POCZTOWY.search(ogon)
        lokalizacja = kod.group(1).strip() if kod else None
    else:
        lokalizacja = ogon or None

    return re.sub(r"\s+", " ", nazwa).strip(" ,"), lokalizacja


def _bez_nadwozia(nazwa: str, nadwozie: str | None) -> str:
    """Usuwa z nazwy nadwozie, skoro stoi ono w osobnym polu.

    `Audi A3 30 TDI S tronic Hatchback` przy `Typ nadwozia = Hatchback`
    zostawia `Audi A3 30 TDI S tronic`. Inaczej nadwozie ląduje w wersji
    wyposażenia i ta sama Audi A3 wygląda na dwa różne warianty zależnie od
    tego, czy tytuł je akurat zawierał.
    """
    if not nadwozie:
        return nazwa
    okrojona = re.sub(rf"\b{re.escape(nadwozie.strip())}\b", " ", nazwa, flags=re.I)
    return re.sub(r"\s+", " ", okrojona).strip()


def na_aukcje(surowa: SurowaOferta, source_id: int, teraz: dt.datetime) -> Auction:
    """Buduje encję domenową z surowej pozycji EFL."""
    pola = surowa.pola

    nadwozie = pola.get("Typ nadwozia")
    lokalizacja = pola.get("Lokalizacja")

    marka = model = wersja = None
    if "Marka, Model, Wersja wyposażenia" in pola:
        # Strona szczegółów — pole jest już czyste, bez rocznika i adresu.
        marka, model, wersja = podziel_marke_model(
            _bez_nadwozia(pola["Marka, Model, Wersja wyposażenia"], nadwozie)
        )
    elif "tytul" in pola:
        nazwa, z_tytulu = rozbierz_tytul(pola["tytul"])
        marka, model, wersja = podziel_marke_model(_bez_nadwozia(nazwa, nadwozie))
        lokalizacja = lokalizacja or z_tytulu

    przebieg = None
    surowy_przebieg = pola.get("Przebieg odczytany")
    if surowy_przebieg:
        trafienie = _PRZEBIEG.search(surowy_przebieg)
        if trafienie is not None:
            try:
                przebieg = Mileage.z_tekstu(trafienie.group(1))
            except NieprawidlowaWartosc:
                przebieg = None

    # Na liscie: "Pojemnosc silnika: 1968ccm" i "Moc silnika: 163KM".
    # W szczegolach jedno pole: "Silnik: 1968ccm (Moc 150KM/110KW)".
    opis_silnika = pola.get("Silnik", "")
    pojemnosc = _int_lub_none(pola.get("Pojemność silnika"))
    if pojemnosc is None and opis_silnika:
        trafienie = _POJEMNOSC.search(opis_silnika)
        pojemnosc = _int_lub_none(trafienie.group(1)) if trafienie else None
    moc = _int_lub_none(pola.get("Moc silnika"))
    if moc is None and opis_silnika:
        trafienie = _MOC.search(opis_silnika)
        moc = int(trafienie.group(1)) if trafienie else None

    vin = None
    surowy_vin = pola.get("VIN")
    if surowy_vin:
        try:
            vin = Vin(surowy_vin)
        except NieprawidlowaWartosc:
            # Niechlujny VIN jest gorszy niz jego brak — to podstawa
            # deduplikacji miedzy serwisami (SPEC.md §8.4).
            vin = None

    cena = None
    surowa_cena = pola.get("cena")
    if surowa_cena:
        try:
            cena = Money.z_tekstu(surowa_cena, Currency.PLN)
        except NieprawidlowaWartosc as exc:
            raise ParseFailed(f"EFL {surowa.external_id}: {exc}") from exc

    koniec = _koniec_na_utc(pola["koniec"]) if pola.get("koniec") else None

    return Auction(
        source_id=source_id,
        external_id=surowa.external_id,
        url=surowa.url,
        status=AuctionStatus.ACTIVE,
        first_seen_at=teraz,
        last_seen_at=teraz,
        make=marka,
        model=model,
        variant=wersja,
        year=_int_lub_none(pola.get("Rok produkcji")),
        mileage=przebieg,
        fuel=_paliwo(pola.get("Rodzaj paliwa")),
        gearbox=pola.get("Skrzynia biegów"),
        engine_ccm=pojemnosc,
        engine_hp=moc,
        vin=vin,
        body=nadwozie,
        # EFL jako jedyny podaje rodzaj wprost — i pisze go na trzy sposoby
        # w jednej liście (`osobowy`, `Osobowy`, `Samochód osobowy`).
        vehicle_kind=rozpoznaj(
            kategoria=pola.get("Rodzaj pojazdu"), nazwa=pola.get("tytul")
        ),
        color=pola.get("Kolor"),
        location=lokalizacja,
        seller=None,
        price_current=cena,
        bid_count=_int_lub_none(pola.get("liczba_ofert")),
        # EFL nie podaje postapienia na stronie; wynika z regulaminu §4 ust. 5
        # (10/100/200 zl wg ceny wywolawczej). Pole jest informacyjne (§8.2),
        # wiec zapisujemy to, co wiemy, bez wyliczania.
        bid_increment_raw="wg regulaminu EFL §4 ust. 5: 10/100/200 zł",
        ends_at=koniec,
    )
