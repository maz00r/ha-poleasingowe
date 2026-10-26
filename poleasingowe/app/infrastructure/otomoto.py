"""Porównanie z ogłoszeniami Otomoto — własny odczyt strony wyników (0.32.0).

Do 0.31 ogłoszenia dostarczał aktor Apify `automation-lab/otomoto-scraper`.
Nie oddawał jednak tego, czego wycena potrzebuje najbardziej, a cudzego
aktora nie da się zmienić. Strona wyników Otomoto niesie to wszystko w JSON-ie
(`<script id="__NEXT_DATA__">` → stan urql), zmierzone 2026-10-26:

- `price.isGross` i odznaki ceny `INVOICE_ISSUED` / `INCLUDE_VAT` — z nich
  liczymy netto (reguła niżej), zamiast dzielić wszystko ÷1,23;
- parametry ogłoszenia: `model` (id taksonomii, np. `seria-7`), `version`,
  `country_origin` (auta z USA), `mileage`, `year`;
- taksonomię modeli WSZYSTKICH marek (filtr `filter_enum_model` warunkowany
  `filter_enum_make`) — każda strona ją ma.

**Model z taksonomii.** Tytuł aukcji mówi „BMW 750", Otomoto zna tylko
„Seria 7". Dopasowanie po kolei: dokładne (slug modelu albo wersji == id lub
nazwa modelu Otomoto), a gdy się nie uda — model AI wybiera id z ZAMKNIĘTEJ
listy modeli tej marki (`WybieraczModelu`). Nieznany model w ścieżce Otomoto
po cichu cofa do całej marki, więc bez trafionego id nie szukamy wcale.

**Netto wg odznak i opisu** (decyzja właściciela repo, 2026-10-26; poprawka
po pomiarze 2026-10-27). Kwota w wynikach jest ZAWSZE brutto — `isGross =
false` mówi tylko, że sprzedawca wpisał ją jako netto (strona ogłoszenia:
164 700 brutto przy `isGross=false`, netto 133 903). Dlatego:

- odznaka `INCLUDE_VAT` („VAT do odliczenia") → ÷1,23;
- bez niej, ale tytuł/krótki opis mówi „FV 23%", „faktura VAT 23%", „+VAT"
  (prywatni sprzedawcy z firmą często nie zaznaczają odznaki) → ÷1,23;
- sama odznaka `INVOICE_ISSUED` albo „marża" w opisie → faktura VAT-marża,
  VAT nie do odliczenia → bez zmian;
- nic z tego (prywatny, bez faktury) → bez zmian: pełny koszt.

**Odrzucamy:** auta z USA/Kanady (`country_origin` + słowa w tytule/opisie),
„cesje leasingu" (cena to rata — 4 324 zł za BMW), uszkodzone (filtr
wyszukiwarki), inne modele, skrajne kwoty (płoty Tukeya).
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import time
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote, urlencode

import httpx

log = logging.getLogger(__name__)

BAZOWY_URL = "https://www.otomoto.pl"
NA_STRONE = 32
MAKS_STRON = 2
MIN_POROWNAN = 8
"""Poniżej tego mediana z Otomoto jest szumem — wtedy jej nie podajemy."""
WAZNOSC_TAKSONOMII_S = 24 * 3600

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

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

KRAJE_ODRZUCANE = frozenset({"usa", "us", "ca", "stany zjednoczone", "kanada"})

_USA = re.compile(
    r"(?i)\b(?:usa|u\.s\.a|z\s+ameryki|ameryka[nń]sk\w*|kanad\w*|"
    r"copart|iaai|do\s+sprowadzenia|w\s+drodze\s+do\s+polski|"
    r"sprowadzon\w*\s+z\s+(?:usa|ameryki|kanady))\b"
)
_CESJA = re.compile(r"(?i)\bcesj\w*|\bodst[eę]pne\b|\bprzej[eę]cie\s+leasingu\b")

PODSTAWA_Z_VAT = "z VAT ÷1,23"
PODSTAWA_Z_VAT_OPIS = "FV 23% wg opisu ÷1,23"
PODSTAWA_MARZA = "VAT-marża"
PODSTAWA_BEZ_VAT = "bez VAT"
# „FV 23%", „FV23", „Faktura VAT 23%", „Faktura VAT", „+ VAT", „VAT 23%".
_FV23 = re.compile(
    r"(?i)\bf(?:aktur\w*)?\.?\s*v(?:at)?\.?\s*-?\s*23"
    r"|\bfaktur\w*\s+vat\b"
    r"|\+\s*vat\b"
    r"|\bvat\s*-?\s*23\s*%"
)
_MARZA = re.compile(r"(?i)\bmar[żz][aąy]\b")


class BladOtomoto(RuntimeError):
    """Otomoto nie oddało strony, z której da się coś policzyć."""


@dataclass(slots=True, frozen=True)
class PojazdDoPorownania:
    marka: str
    model: str
    rok: int | None = None
    przebieg_km: int | None = None
    paliwo: str | None = None
    skrzynia: str | None = None
    dostawczy: bool = False
    wersja: str | None = None
    """Wersja/wyposażenie z aukcji (np. „Performance AWD", „Li xDrive")."""
    tytul: str = ""


@dataclass(slots=True, frozen=True)
class Ogloszenie:
    tytul: str
    cena: int
    """PLN NETTO wg odznak ceny (patrz docstring modułu)."""
    rok: int | None
    przebieg_km: int | None
    url: str
    podstawa: str = PODSTAWA_BEZ_VAT
    wersja: str = ""


@dataclass(slots=True, frozen=True)
class WynikOtomoto:
    adres: str
    ogloszenia: tuple[Ogloszenie, ...]
    mediana: int | None
    p25: int | None
    p75: int | None
    odrzucone_usa: int = 0
    odrzucone_skrajne: int = 0
    wersja: str | None = None
    """Słowa wersji, po których zawężono porównania; `None` — nie zawężono."""
    model_otomoto: str | None = None
    """Nazwa modelu w taksonomii Otomoto, np. „Seria 7"."""
    odrzucone_cesje: int = 0

    @property
    def liczba(self) -> int:
        return len(self.ogloszenia)


WybieraczModelu = Callable[
    [PojazdDoPorownania, Sequence[tuple[str, str]]], Awaitable[str | None]
]
"""(pojazd, [(id, nazwa), …]) → id z listy albo `None`. Implementuje go
wycena AI (`WycenaAI.wybierz_model_otomoto`)."""


def slug(tekst: str) -> str:
    """`Škoda` → `skoda`, `Mercedes-Benz` → `mercedes-benz`, `Seria 5` → `seria-5`."""
    bez_ogonkow = (
        unicodedata.normalize("NFKD", tekst.replace("ł", "l").replace("Ł", "L"))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", bez_ogonkow.lower()).strip("-")


def _liczba(wartosc: Any) -> int | None:
    if isinstance(wartosc, bool) or wartosc is None:
        return None
    try:
        liczba = int(float(wartosc))
    except (TypeError, ValueError):
        return None
    return liczba if liczba > 0 else None


def netto(brutto: int) -> int:
    return int((Decimal(brutto) / VAT).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------
# Parsowanie strony
# --------------------------------------------------------------------------

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _stany(html: str) -> list[Any]:
    """Zdekodowane wpisy stanu urql strony."""
    trafienie = _NEXT_DATA.search(html)
    if trafienie is None:
        raise BladOtomoto("strona Otomoto bez __NEXT_DATA__")
    try:
        dane = json.loads(trafienie.group(1))
        stan = dane["props"]["pageProps"]["urqlState"]
        return [json.loads(wpis["data"]) for wpis in stan.values() if "data" in wpis]
    except (ValueError, KeyError, TypeError) as exc:
        raise BladOtomoto("nieoczekiwany kształt danych Otomoto") from exc


def _przejdz(obiekt: Any) -> Any:
    if isinstance(obiekt, dict):
        yield obiekt
        for wartosc in obiekt.values():
            yield from _przejdz(wartosc)
    elif isinstance(obiekt, list):
        for wartosc in obiekt:
            yield from _przejdz(wartosc)


def taksonomia(html: str) -> dict[str, tuple[tuple[str, str], ...]]:
    """Marka (id Otomoto) → [(id modelu, nazwa)], bez generacji `gen-*`."""
    wynik: dict[str, tuple[tuple[str, str], ...]] = {}
    for stan in _stany(html):
        for wezel in _przejdz(stan):
            if (
                wezel.get("filterId") != "filter_enum_model"
                or wezel.get("__typename") != "OpenForInputFilterState"
            ):
                continue
            marki = [
                w.get("value")
                for w in wezel.get("conditions") or []
                if w.get("filterId") == "filter_enum_make"
            ]
            modele = tuple(
                (str(m["id"]), str(m.get("name") or m["id"]))
                for grupa in wezel.get("values") or []
                for m in grupa.get("values") or []
                if isinstance(m, dict)
                and m.get("id")
                and not str(m["id"]).startswith("gen-")
                and m["id"] != "other"
            )
            for marka in marki:
                if isinstance(marka, str) and modele:
                    wynik[marka] = modele
    return wynik


@dataclass(slots=True, frozen=True)
class Rekord:
    """Jedno ogłoszenie ze strony wyników, przed oceną."""

    tytul: str
    opis: str
    url: str
    cena: int
    is_gross: bool
    odznaki: tuple[str, ...]
    parametry: dict[str, tuple[str, str]]
    """klucz → (wartość, wartość wyświetlana)."""

    def parametr(self, klucz: str) -> str:
        return self.parametry.get(klucz, ("", ""))[0]


def wyniki(html: str) -> tuple[list[Rekord], int]:
    """Ogłoszenia ze strony wyników i łączna liczba trafień."""
    for stan in _stany(html):
        wyszukiwanie = stan.get("advertSearch") if isinstance(stan, dict) else None
        if not isinstance(wyszukiwanie, dict):
            continue
        rekordy: list[Rekord] = []
        for krawedz in wyszukiwanie.get("edges") or []:
            wezel = (krawedz or {}).get("node") or {}
            cena = (wezel.get("price") or {}).get("amount") or {}
            kwota = _liczba(cena.get("units"))
            url = wezel.get("url")
            if (
                kwota is None
                or not isinstance(url, str)
                or not url.startswith("https://")
            ):
                continue
            rekordy.append(
                Rekord(
                    tytul=str(wezel.get("title") or ""),
                    opis=str(wezel.get("shortDescription") or ""),
                    url=url,
                    cena=kwota,
                    is_gross=(wezel.get("price") or {}).get("isGross") is not False,
                    odznaki=tuple(
                        str(o) for o in (wezel.get("price") or {}).get("badges") or []
                    ),
                    parametry={
                        str(p.get("key")): (
                            str(p.get("value") or ""),
                            str(p.get("displayValue") or ""),
                        )
                        for p in wezel.get("parameters") or []
                        if isinstance(p, dict) and p.get("key")
                    },
                )
            )
        return rekordy, int(wyszukiwanie.get("totalCount") or 0)
    raise BladOtomoto("strona Otomoto bez wyników wyszukiwania")


def cena_netto(rekord: Rekord) -> tuple[int, str]:
    """Reguła z docstringu modułu. `is_gross` celowo pominięte — kwota jest
    brutto niezależnie od niego."""
    if "INCLUDE_VAT" in rekord.odznaki:
        return netto(rekord.cena), PODSTAWA_Z_VAT
    tekst = f"{rekord.tytul} {rekord.opis}"
    if "INVOICE_ISSUED" in rekord.odznaki or _MARZA.search(tekst):
        return rekord.cena, PODSTAWA_MARZA
    if _FV23.search(tekst):
        return netto(rekord.cena), PODSTAWA_Z_VAT_OPIS
    return rekord.cena, PODSTAWA_BEZ_VAT


def z_usa(rekord: Rekord) -> bool:
    kraj = rekord.parametry.get("country_origin")
    if kraj is not None and (
        kraj[0].lower() in KRAJE_ODRZUCANE or kraj[1].lower() in KRAJE_ODRZUCANE
    ):
        return True
    return _USA.search(f"{rekord.tytul} {rekord.opis}") is not None


def cesja(rekord: Rekord) -> bool:
    return _CESJA.search(f"{rekord.tytul} {rekord.opis}") is not None


# --------------------------------------------------------------------------
# Model z taksonomii
# --------------------------------------------------------------------------


def dopasuj_model(
    pojazd: PojazdDoPorownania, modele: Sequence[tuple[str, str]]
) -> str | None:
    """Dokładne dopasowanie modelu albo pierwszego słowa wersji do taksonomii."""
    kandydaci = [slug(pojazd.model)]
    if pojazd.wersja:
        kandydaci.append(slug(f"{pojazd.model} {pojazd.wersja.split()[0]}"))
    for kandydat in kandydaci:
        for identyfikator, nazwa in modele:
            if kandydat and kandydat in (identyfikator, slug(nazwa)):
                return identyfikator
    return None


# --------------------------------------------------------------------------
# Wyszukiwanie
# --------------------------------------------------------------------------


def adres_wyszukiwania(
    pojazd: PojazdDoPorownania, model_id: str, *, strona: int = 1
) -> str:
    kategoria = "dostawcze" if pojazd.dostawczy else "osobowe"
    sciezka = f"/{kategoria}/{slug(pojazd.marka)}/{model_id}"
    parametry: list[tuple[str, str]] = []
    if pojazd.rok is not None:
        sciezka += f"/od-{pojazd.rok - 1}"
        parametry.append(("search[filter_float_year:to]", str(pojazd.rok + 1)))
    # Bez filtra przebiegu — różnicę koryguje model AI (decyzja 2026-10-26).
    for numer, wartosc in enumerate(PALIWA.get(pojazd.paliwo or "", ())):
        parametry.append((f"search[filter_enum_fuel_type][{numer}]", wartosc))
    if (skrzynia := SKRZYNIE.get(pojazd.skrzynia or "")) is not None:
        parametry.append(("search[filter_enum_gearbox]", skrzynia))
    # Otomoto domyślnie pokazuje też uszkodzone — „0" = Nieuszkodzony.
    parametry.append(("search[filter_enum_damaged]", "0"))
    if strona > 1:
        parametry.append(("page", str(strona)))
    return f"{BAZOWY_URL}{sciezka}?{urlencode(parametry, quote_via=quote)}"


# Słowa, które nie odróżniają wersji — są i w „Long Range AWD", i w
# „Performance AWD". Zawężanie po nich niczego by nie zawęziło.
_SLOWA_WSPOLNE = frozenset(
    {
        "awd", "fwd", "rwd", "4x4", "4wd", "dual", "motor", "automat", "aut",
        "automatic", "manual", "km", "kw", "hp", "kombi", "sedan", "hatchback",
        "suv", "dsg", "cvt", "edition", "plus", "pakiet", "salon", "polska",
    }
)  # fmt: skip


def slowa_wersji(wersja: str | None) -> tuple[str, ...]:
    """Wyróżniające słowa wersji: „Performance AWD 534 KM" → („performance",)."""
    if not wersja:
        return ()
    return tuple(
        s
        for s in slug(wersja).split("-")
        if len(s) >= 3 and not s.isdigit() and s not in _SLOWA_WSPOLNE
    )


def zawez_do_wersji(
    ogloszenia: list[Ogloszenie], wersja: str | None
) -> tuple[list[Ogloszenie], str | None]:
    """Zostawia ogłoszenia tej samej wersji, jeśli jest ich co najmniej 3."""
    slowa = slowa_wersji(wersja)
    if not slowa:
        return ogloszenia, None
    pasujace = [
        o
        for o in ogloszenia
        if any(s in slug(f"{o.tytul} {o.wersja}").split("-") for s in slowa)
    ]
    if len(pasujace) < 3:
        return ogloszenia, None
    return pasujace, " ".join(slowa)


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
    adres: str,
    ogloszenia: list[Ogloszenie],
    *,
    odrzucone_usa: int = 0,
    odrzucone_cesje: int = 0,
    wersja: str | None = None,
    model_otomoto: str | None = None,
) -> WynikOtomoto:
    ogloszenia, wersja_uzyta = zawez_do_wersji(ogloszenia, wersja)
    przefiltrowane = bez_skrajnych(ogloszenia)
    wspolne: dict[str, Any] = {
        "adres": adres,
        "ogloszenia": tuple(przefiltrowane),
        "odrzucone_usa": odrzucone_usa,
        "odrzucone_skrajne": len(ogloszenia) - len(przefiltrowane),
        "wersja": wersja_uzyta,
        "model_otomoto": model_otomoto,
        "odrzucone_cesje": odrzucone_cesje,
    }
    ceny = sorted(o.cena for o in przefiltrowane)
    if len(ceny) < MIN_POROWNAN:
        return WynikOtomoto(mediana=None, p25=None, p75=None, **wspolne)
    kwartyle = statistics.quantiles(ceny, n=4, method="inclusive")
    return WynikOtomoto(
        mediana=round(statistics.median(ceny)),
        p25=round(kwartyle[0]),
        p75=round(kwartyle[2]),
        **wspolne,
    )


Postep = Callable[[str, float], None]
"""(opis etapu, udział 0-1) — do paska postępu na karcie."""


class Otomoto:
    """Odczyt Otomoto: taksonomia modeli, wyniki wyszukiwania, statystyka."""

    def __init__(
        self,
        *,
        klient: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._klient = klient
        self._timeout = timeout
        self._taksonomia: dict[
            str, tuple[float, dict[str, tuple[tuple[str, str], ...]]]
        ] = {}

    async def zamknij(self) -> None:
        if self._klient is not None:
            await self._klient.aclose()
            self._klient = None

    async def _pobierz(self, adres: str) -> str:
        if self._klient is None:
            self._klient = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": _UA, "Accept-Language": "pl-PL,pl;q=0.9"},
            )
        try:
            odpowiedz = await self._klient.get(adres)
            odpowiedz.raise_for_status()
        except httpx.HTTPError as exc:
            raise BladOtomoto(f"Otomoto nie odpowiada: {exc}") from exc
        return odpowiedz.text

    async def modele(self, pojazd: PojazdDoPorownania) -> tuple[tuple[str, str], ...]:
        """Modele marki z taksonomii Otomoto (pamięć 24 h na kategorię)."""
        kategoria = "dostawcze" if pojazd.dostawczy else "osobowe"
        wpis = self._taksonomia.get(kategoria)
        if wpis is None or time.monotonic() - wpis[0] > WAZNOSC_TAKSONOMII_S:
            mapa = taksonomia(await self._pobierz(f"{BAZOWY_URL}/{kategoria}"))
            self._taksonomia[kategoria] = (time.monotonic(), mapa)
        else:
            mapa = wpis[1]
        return mapa.get(slug(pojazd.marka), ())

    async def porownaj(
        self,
        pojazd: PojazdDoPorownania,
        *,
        wybieracz: WybieraczModelu | None = None,
        postep: Postep | None = None,
    ) -> WynikOtomoto:
        def krok(opis: str, udzial: float) -> None:
            if postep is not None:
                postep(opis, udzial)

        krok("Ustalanie modelu w Otomoto", 0.05)
        modele = await self.modele(pojazd)
        if not modele:
            raise BladOtomoto(f"Otomoto nie zna marki {pojazd.marka}")
        model_id = dopasuj_model(pojazd, modele)
        if model_id is None and wybieracz is not None:
            krok("Model AI ustala model w taksonomii Otomoto", 0.1)
            wybrany = await wybieracz(pojazd, modele)
            model_id = wybrany if wybrany in {i for i, _ in modele} else None
        if model_id is None:
            raise BladOtomoto(
                f"nie udało się dopasować modelu „{pojazd.model}” do Otomoto"
            )
        nazwa_modelu = dict(modele).get(model_id, model_id)

        rekordy: list[Rekord] = []
        adres = adres_wyszukiwania(pojazd, model_id)
        for strona in range(1, MAKS_STRON + 1):
            krok(
                f"Pobieranie ogłoszeń {nazwa_modelu} (strona {strona})",
                0.15 + 0.15 * strona,
            )
            na_stronie, razem = wyniki(
                await self._pobierz(adres_wyszukiwania(pojazd, model_id, strona=strona))
            )
            rekordy.extend(na_stronie)
            if strona * NA_STRONE >= razem or not na_stronie:
                break

        widziane: dict[str, Ogloszenie] = {}
        usa = cesje = 0
        for rekord in rekordy:
            if rekord.parametr("model") and rekord.parametr("model") != model_id:
                continue
            rok = _liczba(rekord.parametr("year"))
            if pojazd.rok is not None and rok is not None and abs(rok - pojazd.rok) > 1:
                continue
            if z_usa(rekord):
                usa += 1
                continue
            if cesja(rekord):
                cesje += 1
                continue
            kwota, podstawa = cena_netto(rekord)
            widziane.setdefault(
                rekord.url,
                Ogloszenie(
                    tytul=rekord.tytul,
                    cena=kwota,
                    rok=rok,
                    przebieg_km=_liczba(rekord.parametr("mileage")),
                    url=rekord.url,
                    podstawa=podstawa,
                    wersja=rekord.parametry.get("version", ("", ""))[1],
                ),
            )
        wynik = statystyka(
            adres,
            list(widziane.values()),
            odrzucone_usa=usa,
            odrzucone_cesje=cesje,
            wersja=pojazd.wersja,
            model_otomoto=nazwa_modelu,
        )
        log.info(
            "Otomoto: %s porównań dla %s %s → %s (odrzucone: %s USA, %s cesji, "
            "%s skrajnych)",
            wynik.liczba,
            pojazd.marka,
            pojazd.model,
            nazwa_modelu,
            usa,
            cesje,
            wynik.odrzucone_skrajne,
        )
        return wynik
