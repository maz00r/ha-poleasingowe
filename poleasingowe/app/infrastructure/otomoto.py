"""Porównanie z ogłoszeniami Otomoto przez aktora Apify (0.31.0).

Aktor `automation-lab/otomoto-scraper` dostaje adres wyszukiwarki Otomoto
z filtrami i oddaje ogłoszenia (`pricePLN`, `year`, `mileageKm`, `model`,
`title`, `url`…). Koszt: ok. 0,005 USD za uruchomienie + ok. 0,001 USD za
ogłoszenie — dlatego wycena z Otomoto startuje wyłącznie z przycisku.

DWIE PUŁAPKI ZMIERZONE NA ŻYWO (2026-10-26):

- **Nieznany model w ścieżce po cichu cofa do całej marki.**
  `/osobowe/bmw/530e` odpowiada 200 i nagłówkiem „BMW - Samochody Osobowe":
  zamiast porównań dostalibyśmy wszystkie BMW. Dlatego każde ogłoszenie
  przechodzi przez `pasuje()` — model musi stać w polu `model` albo
  w tytule. Gdy ścieżka nie zadziała, drugie podejście to wyszukiwanie frazą
  (`/osobowe/<marka>/q-<model>`), też filtrowane.
- **Ceny są ofertowe**, wystawione przez sprzedających — zwykle wyższe od
  cen z aukcji poleasingowych. Model AI dostaje je z tą adnotacją.

Decyzje właściciela repo (2026-10-26):

- **Jeden przebieg aktora** na wycenę — wyszukiwanie frazą
  (`/osobowe/<marka>/q-<model>`), które działa także dla modeli nieznanych
  ścieżce, plus własny filtr `pasuje()`.
- **Wszystkie kwoty Otomoto traktujemy jako brutto** (ceny wyświetlane na
  Otomoto mają `isGross: true` — 32/32 w próbce; aktor nie oddaje tego pola)
  i przeliczamy ÷1,23 na netto, tak jak ceny aukcji w bazie.
- **Odrzucamy auta z USA** (dostępne na miejscu i „do sprowadzenia") —
  inny rynek, inny stan, inne ceny — po słowach w tytule i krótkim opisie.
- **Odrzucamy skrajne kwoty** płotami Tukeya (1,5 x IQR) — pojedyncza oferta
  „za złotówkę" albo pomyłka o rząd wielkości nie ma ruszać mediany.

Token Apify idzie w nagłówku `Authorization`, nie w adresie — adresy
żądań lądują w logach.
"""

from __future__ import annotations

import logging
import re
import statistics
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote, urlencode

import httpx

log = logging.getLogger(__name__)

AKTOR = "automation-lab~otomoto-scraper"
ADRES_API = f"https://api.apify.com/v2/acts/{AKTOR}/run-sync-get-dataset-items"
MAKS_OGLOSZEN = 50
MIN_POROWNAN = 8
"""Poniżej tego mediana z Otomoto jest szumem — wtedy jej nie podajemy."""

PALIWA: dict[str, tuple[str, ...]] = {
    "Benzyna": ("petrol",),
    "Diesel": ("diesel",),
    "Hybryda": ("hybrid", "plugin-hybrid"),
    "Elektryczny": ("electric",),
    "Wodór": ("hidrogen",),
    "Gaz": ("petrol-lpg", "petrol-cng"),
}
"""Kanoniczne paliwa aplikacji → identyfikatory filtra Otomoto (sic: `hidrogen`)."""

SKRZYNIE = {"Automatyczna": "automatic", "Manualna": "manual"}

VAT = Decimal("1.23")

_USA = re.compile(
    r"(?i)\b(?:usa|u\.s\.a|z\s+ameryki|ameryka[nń]sk\w*|kanad\w*|"
    r"copart|iaai|do\s+sprowadzenia|w\s+drodze\s+do\s+polski|"
    r"sprowadzon\w*\s+z\s+(?:usa|ameryki|kanady))\b"
)
"""Auta z USA/Kanady — także „do sprowadzenia". Aktor oddaje tylko tytuł
i krótki opis karty, więc to słowa kluczowe, nie pole „kraj pochodzenia"."""


class BladOtomoto(RuntimeError):
    """Apify nie zwrócił wyniku, z którego da się coś policzyć."""


@dataclass(slots=True, frozen=True)
class PojazdDoPorownania:
    marka: str
    model: str
    rok: int | None = None
    przebieg_km: int | None = None
    paliwo: str | None = None
    skrzynia: str | None = None
    dostawczy: bool = False


@dataclass(slots=True, frozen=True)
class Ogloszenie:
    tytul: str
    cena: int
    """PLN NETTO — cena z Otomoto (brutto) ÷1,23."""
    rok: int | None
    przebieg_km: int | None
    url: str


@dataclass(slots=True, frozen=True)
class WynikOtomoto:
    adres: str
    ogloszenia: tuple[Ogloszenie, ...]
    mediana: int | None
    p25: int | None
    p75: int | None
    odrzucone_usa: int = 0
    odrzucone_skrajne: int = 0

    @property
    def liczba(self) -> int:
        return len(self.ogloszenia)


def slug(tekst: str) -> str:
    """`Škoda` → `skoda`, `Mercedes-Benz` → `mercedes-benz`, `Seria 5` → `seria-5`."""
    bez_ogonkow = (
        unicodedata.normalize("NFKD", tekst.replace("ł", "l").replace("Ł", "L"))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", bez_ogonkow.lower()).strip("-")


def adres_wyszukiwania(
    pojazd: PojazdDoPorownania, *, fraza: bool = False, szeroko: bool = False
) -> str:
    """Adres wyszukiwarki z filtrami (składnia sprawdzona na żywo)."""
    kategoria = "dostawcze" if pojazd.dostawczy else "osobowe"
    marka, model = slug(pojazd.marka), slug(pojazd.model)
    rozrzut = 2 if szeroko else 1
    sciezka = f"/{kategoria}/{marka}/" + (f"q-{model}" if fraza else model)
    parametry: list[tuple[str, str]] = []
    if pojazd.rok is not None:
        sciezka += f"/od-{pojazd.rok - rozrzut}"
        parametry.append(("search[filter_float_year:to]", str(pojazd.rok + rozrzut)))
    if pojazd.przebieg_km is not None and not szeroko:
        parametry.append(
            ("search[filter_float_mileage:from]", str(int(pojazd.przebieg_km * 0.6)))
        )
        parametry.append(
            ("search[filter_float_mileage:to]", str(int(pojazd.przebieg_km * 1.4)))
        )
    for numer, wartosc in enumerate(PALIWA.get(pojazd.paliwo or "", ())):
        parametry.append((f"search[filter_enum_fuel_type][{numer}]", wartosc))
    if (skrzynia := SKRZYNIE.get(pojazd.skrzynia or "")) is not None:
        parametry.append(("search[filter_enum_gearbox]", skrzynia))
    zapytanie = urlencode(parametry, quote_via=quote)
    return f"https://www.otomoto.pl{sciezka}" + (f"?{zapytanie}" if zapytanie else "")


def _liczba(wartosc: Any) -> int | None:
    if isinstance(wartosc, bool) or wartosc is None:
        return None
    try:
        liczba = int(float(wartosc))
    except (TypeError, ValueError):
        return None
    return liczba if liczba > 0 else None


def pasuje(
    rekord: dict[str, Any], pojazd: PojazdDoPorownania, *, szeroko: bool
) -> bool:
    """Czy ogłoszenie opisuje TEN model — patrz pułapka w docstringu modułu."""
    model = slug(pojazd.model)
    if not model:
        return False
    teksty = " ".join(
        str(rekord.get(pole) or "") for pole in ("model", "title", "version")
    )
    if f"-{model}-" not in f"-{slug(teksty)}-":
        return False
    if slug(str(rekord.get("make") or pojazd.marka)) != slug(pojazd.marka):
        return False
    rok = _liczba(rekord.get("year"))
    rozrzut = 2 if szeroko else 1
    return not (
        pojazd.rok is not None and rok is not None and abs(rok - pojazd.rok) > rozrzut
    )


def z_usa(rekord: dict[str, Any]) -> bool:
    tekst = " ".join(
        str(rekord.get(pole) or "") for pole in ("title", "shortDescription", "version")
    )
    return _USA.search(tekst) is not None


def netto(brutto: int) -> int:
    return int((Decimal(brutto) / VAT).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _ogloszenie(rekord: dict[str, Any]) -> Ogloszenie | None:
    cena = _liczba(rekord.get("pricePLN"))
    url = rekord.get("url")
    if cena is None or not isinstance(url, str) or not url.startswith("https://"):
        return None
    return Ogloszenie(
        tytul=str(rekord.get("title") or ""),
        cena=netto(cena),
        rok=_liczba(rekord.get("year")),
        przebieg_km=_liczba(rekord.get("mileageKm")),
        url=url,
    )


def bez_skrajnych(ogloszenia: list[Ogloszenie]) -> list[Ogloszenie]:
    """Płoty Tukeya: poza [Q1 - 1,5·IQR, Q3 + 1,5·IQR] odpada.

    Przy kilku ogłoszeniach kwartyle są niestabilne — wtedy zostaje prostsza
    reguła: poza 0,5-2x mediany.
    """
    if len(ogloszenia) < 3:
        return ogloszenia
    ceny = sorted(o.cena for o in ogloszenia)
    if len(ceny) >= MIN_POROWNAN:
        q1, _, q3 = statistics.quantiles(ceny, n=4, method="inclusive")
        rozstep = q3 - q1
        dol, gora = q1 - 1.5 * rozstep, q3 + 1.5 * rozstep
    else:
        mediana = statistics.median(ceny)
        dol, gora = mediana * 0.5, mediana * 2
    return [o for o in ogloszenia if dol <= o.cena <= gora]


def statystyka(
    adres: str, ogloszenia: list[Ogloszenie], *, odrzucone_usa: int = 0
) -> WynikOtomoto:
    przefiltrowane = bez_skrajnych(ogloszenia)
    skrajne = len(ogloszenia) - len(przefiltrowane)
    ceny = sorted(o.cena for o in przefiltrowane)
    if len(ceny) < MIN_POROWNAN:
        return WynikOtomoto(
            adres, tuple(przefiltrowane), None, None, None, odrzucone_usa, skrajne
        )
    kwartyle = statistics.quantiles(ceny, n=4, method="inclusive")
    return WynikOtomoto(
        adres=adres,
        ogloszenia=tuple(przefiltrowane),
        mediana=round(statistics.median(ceny)),
        p25=round(kwartyle[0]),
        p75=round(kwartyle[2]),
        odrzucone_usa=odrzucone_usa,
        odrzucone_skrajne=skrajne,
    )


class Otomoto:
    """Uruchamia aktora Apify i liczy statystykę porównywalnych ogłoszeń."""

    def __init__(
        self,
        token: str,
        *,
        klient: httpx.AsyncClient | None = None,
        timeout: float = 180.0,
    ) -> None:
        self._token = token
        self._klient = klient
        self._timeout = timeout

    async def zamknij(self) -> None:
        if self._klient is not None:
            await self._klient.aclose()
            self._klient = None

    async def _uruchom(self, adres: str) -> list[dict[str, Any]]:
        if self._klient is None:
            self._klient = httpx.AsyncClient(timeout=self._timeout)
        try:
            odpowiedz = await self._klient.post(
                ADRES_API,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"startUrls": [adres], "maxResults": MAKS_OGLOSZEN},
            )
        except httpx.HTTPError as exc:
            raise BladOtomoto(f"Apify nie odpowiada: {exc}") from exc
        if odpowiedz.status_code in (401, 403):
            raise BladOtomoto("Apify odrzucił token — sprawdź opcję apify_token.")
        if odpowiedz.status_code == 402:
            raise BladOtomoto("Apify: brak środków na koncie.")
        if not odpowiedz.is_success:
            raise BladOtomoto(f"Apify: HTTP {odpowiedz.status_code}")
        try:
            dane = odpowiedz.json()
        except ValueError as exc:
            raise BladOtomoto("Apify zwrócił nie-JSON") from exc
        if not isinstance(dane, list):
            raise BladOtomoto("Apify zwrócił nieoczekiwany kształt danych")
        return [r for r in dane if isinstance(r, dict)]

    async def porownaj(self, pojazd: PojazdDoPorownania) -> WynikOtomoto:
        """Jeden przebieg aktora: wyszukiwanie frazą, potem własne filtry."""
        adres = adres_wyszukiwania(pojazd, fraza=True)
        widziane: dict[str, Ogloszenie] = {}
        usa = 0
        for rekord in await self._uruchom(adres):
            if not pasuje(rekord, pojazd, szeroko=False):
                continue
            if z_usa(rekord):
                usa += 1
                continue
            if (ogloszenie := _ogloszenie(rekord)) is not None:
                widziane.setdefault(ogloszenie.url, ogloszenie)
        wynik = statystyka(adres, list(widziane.values()), odrzucone_usa=usa)
        log.info(
            "Otomoto: %s porównań dla %s %s (odrzucone: %s z USA, %s skrajnych)",
            wynik.liczba,
            pojazd.marka,
            pojazd.model,
            usa,
            wynik.odrzucone_skrajne,
        )
        return wynik
