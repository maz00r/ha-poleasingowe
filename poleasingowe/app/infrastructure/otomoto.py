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

Token Apify idzie w nagłówku `Authorization`, nie w adresie — adresy
żądań lądują w logach.
"""

from __future__ import annotations

import logging
import re
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx

log = logging.getLogger(__name__)

AKTOR = "automation-lab~otomoto-scraper"
ADRES_API = f"https://api.apify.com/v2/acts/{AKTOR}/run-sync-get-dataset-items"
MAKS_OGLOSZEN = 40
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


def _ogloszenie(rekord: dict[str, Any]) -> Ogloszenie | None:
    cena = _liczba(rekord.get("pricePLN"))
    url = rekord.get("url")
    if cena is None or not isinstance(url, str) or not url.startswith("https://"):
        return None
    return Ogloszenie(
        tytul=str(rekord.get("title") or ""),
        cena=cena,
        rok=_liczba(rekord.get("year")),
        przebieg_km=_liczba(rekord.get("mileageKm")),
        url=url,
    )


def statystyka(adres: str, ogloszenia: list[Ogloszenie]) -> WynikOtomoto:
    ceny = sorted(o.cena for o in ogloszenia)
    if len(ceny) < MIN_POROWNAN:
        return WynikOtomoto(adres, tuple(ogloszenia), None, None, None)
    kwartyle = statistics.quantiles(ceny, n=4, method="inclusive")
    return WynikOtomoto(
        adres=adres,
        ogloszenia=tuple(ogloszenia),
        mediana=round(statistics.median(ceny)),
        p25=round(kwartyle[0]),
        p75=round(kwartyle[2]),
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
        """Najwyżej dwa uruchomienia aktora: wąsko po ścieżce, potem frazą szeroko."""
        widziane: dict[str, Ogloszenie] = {}
        adres = adres_wyszukiwania(pojazd)
        for proba, (fraza, szeroko) in enumerate(((False, False), (True, True))):
            adres = adres_wyszukiwania(pojazd, fraza=fraza, szeroko=szeroko)
            for rekord in await self._uruchom(adres):
                if not pasuje(rekord, pojazd, szeroko=szeroko):
                    continue
                if (ogloszenie := _ogloszenie(rekord)) is not None:
                    widziane.setdefault(ogloszenie.url, ogloszenie)
            log.info(
                "Otomoto (próba %s): %s pasujących ogłoszeń dla %s %s",
                proba + 1,
                len(widziane),
                pojazd.marka,
                pojazd.model,
            )
            if len(widziane) >= MIN_POROWNAN:
                break
        return statystyka(adres, list(widziane.values()))
