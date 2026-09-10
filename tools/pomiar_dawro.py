#!/usr/bin/env python3
"""ETAP 0 dla dawro.pl — pełny rekonesans jednym przebiegiem.

Odpowiada na wszystko, co trzeba wiedzieć, zanim powstanie adapter `dawro`:
listy i paginacja, kształt `external_id`, pola pojazdu i ich pokrycie,
podstawa ceny (brutto/netto), galeria, publiczny endpoint najwyższej oferty
oraz — najważniejsze — zachowanie aukcji przy domknięciu (SPEC.md §4).

Bez logowania. Same GET-y. Skrypt nie wysyła formularzy, nie licytuje i nie
woła endpointów wymagających sesji ani nieudokumentowanych — publiczny
`/WebService/PasekInformacyjny/` zostaje **zapisany w raporcie, nie wywołany**
(spec §4, akapit o JS).

Bezpieczeństwo danych: odpowiedzi lądują najpierw w katalogu tymczasowym.
Do `fixtures/dawro/` trafiają dopiero po redakcji VIN-ów, numerów
rejestracyjnych i loginów tym samym mechanizmem co reszta fixtures
(`tools/redakcja_fixtures.py`).

Uruchomienie:
    python3 tools/pomiar_dawro.py                 # skan + szczegóły + domknięcie
    python3 tools/pomiar_dawro.py --static-only   # bez czekania na koniec aukcji
    python3 tools/pomiar_dawro.py --dry-run       # jedna próbka, kontrola parsera
    python3 tools/pomiar_dawro.py --url <URL>     # wymuś pomiar wskazanej aukcji

Pomiar domknięcia biegnie lokalnie i czeka do końca aukcji — **komputer musi
zostać aktywny i online przez całe okno domknięcia** (do ~10 minut po
terminie). Wynik: `fixtures/dawro/raport.md` i `pomiar-domkniecie-<id>.json`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as htmllib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import zoneinfo
from collections.abc import Callable

TZ = zoneinfo.ZoneInfo("Europe/Warsaw")
BAZA = "https://www.dawro.pl"
KODOWANIE = "utf-8"
"""dawro deklaruje `charset=iso-8859-1` w nagłówku HTTP, ale treść jest
UTF-8 (`<meta charset="utf-8">`, `ó` = 0xC3 0xB3). Nagłówkowi nie ufamy."""
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Widok listy: sortowanie po dacie zakończenia rosnąco (najbliższy koniec
# u góry — to on jest kandydatem do pomiaru domknięcia), 100 pozycji na
# stronę, układ "boxy" (najwięcej pól: cena wywoławcza, rok, sprzedawca,
# forma sprzedaży, najwyższa oferta).
WZORZEC_LISTY = (
    "/aukcje/sortuj,data-zakonczenia,kierunek,rosnaco,"
    "strona,{strona},ilosc,100,wyswietlanie,boxy"
)
MAKS_STRON = 100

# Gęsty run-up przed końcem i drabinka po nim (spec §4).
RUNUP_OD_S = 180
RUNUP_KROK_S = 10
PO_KONCU_S = [0, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600]
# Przesunięcie `ends_at` większe niż tyle liczymy jako dogrywkę, nie szum.
DOGRYWKA_TOLERANCJA_S = 15

LIMIT_NA_MINUTE = 20

# Publiczny endpoint aktualizacji najwyższej oferty — POST {id}. Wołany przez
# stronę anonimowo co 2 s. ZAPISUJEMY GO, nie wołamy (spec §4).
ENDPOINT_OFERTY = "/WebService/PasekInformacyjny/"

POLA_POKRYCIA = (
    "vin",
    "nr_rejestracyjny",
    "parking",
    "opis_modelu",
    "rok_produkcji",
    "przebieg",
    "pojemnosc",
    "moc",
    "paliwo",
    "skrzynia",
    "nadwozie",
    "sprzedawca",
    "forma_sprzedazy",
    "zdjecia",
    "cena_wywolawcza",
    "podstawa_ceny",
)


class BladRekonesansu(RuntimeError):
    """Skan nie może być kontynuowany: WAF, przekierowanie na główną,
    brak kontenera, powtórzona strona."""


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


class Odpowiedz:
    """Jedna odpowiedź HTTP wraz z surowymi bajtami (do zrzutu) i tekstem."""

    __slots__ = ("status", "headers", "bajty", "tekst", "przeskoki", "url_koncowy")

    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        bajty: bytes,
        przeskoki: int,
        url_koncowy: str,
    ) -> None:
        self.status = status
        self.headers = headers
        self.bajty = bajty
        self.tekst = bajty.decode(KODOWANIE, "replace")
        self.przeskoki = przeskoki
        self.url_koncowy = url_koncowy


def pobierz(url: str, *, limit_s: int = 30) -> Odpowiedz:
    """GET przez curl (spójnie z `recon_0b.py`).

    curl domyka niepełne łańcuchy certyfikatów, których magazyn CA Pythona
    nie domyka. Weryfikacja certyfikatu ZOSTAJE włączona.
    """
    # curl z listą stałych argumentów; url pochodzi z listy aukcji dawro.
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "-m",
            str(limit_s),
            "-L",
            "--compressed",
            "-D",
            "-",
            "-o",
            "-",
            "--write-out",
            "\n@@META@@ %{http_code} %{num_redirects} %{url_effective}\n",
            "-A",
            UA,
            "-H",
            "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H",
            "Accept-Language: pl-PL,pl;q=0.9",
            url,
        ],
        capture_output=True,
        timeout=limit_s + 15,
        check=False,
    )
    if proc.returncode != 0:
        raise BladRekonesansu(
            f"curl {proc.returncode} dla {url}: "
            f"{proc.stderr.decode('utf-8', 'replace')[:200]}"
        )

    surowe = proc.stdout
    znacznik = surowe.rfind(b"\n@@META@@ ")
    meta = surowe[znacznik + 1 :].decode("ascii", "replace").strip()
    reszta = surowe[:znacznik]
    m = re.match(r"@@META@@ (\d{3}) (\d+) (\S+)", meta)
    status = int(m.group(1)) if m else 0
    przeskoki = int(m.group(2)) if m else 0
    url_koncowy = m.group(3) if m else url

    # Przy -L nagłówki każdego przeskoku poprzedzają ciało; bierzemy ostatni
    # blok nagłówków, a za ciało — wszystko po nim.
    czesci = reszta.split(b"\r\n\r\n")
    headers: dict[str, str] = {}
    ciez = reszta
    for i, czesc in enumerate(czesci):
        if czesc.startswith(b"HTTP/"):
            headers = {}
            for linia in czesc.decode("iso-8859-1", "replace").splitlines()[1:]:
                if ":" in linia:
                    k, v = linia.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            ciez = b"\r\n\r\n".join(czesci[i + 1 :])
    return Odpowiedz(status, headers, ciez, przeskoki, url_koncowy)


class Sesja:
    """GET-y z limitem tempa. Pomiar domknięcia wołamy z `pilne=True`,
    co pomija limit — krótka, jednorazowa seria kilkunastu żądań (spec §4)."""

    def __init__(self, limit_na_minute: int = LIMIT_NA_MINUTE) -> None:
        self._odstep = 60.0 / max(limit_na_minute, 1)
        self._ostatnie = 0.0
        self.licznik = 0

    def get(self, sciezka_lub_url: str, *, pilne: bool = False) -> Odpowiedz:
        url = (
            sciezka_lub_url
            if sciezka_lub_url.startswith("http")
            else BAZA + sciezka_lub_url
        )
        if not pilne:
            czekaj = self._odstep - (time.monotonic() - self._ostatnie)
            if czekaj > 0:
                time.sleep(czekaj)
        self._ostatnie = time.monotonic()
        self.licznik += 1
        return pobierz(url)


# --------------------------------------------------------------------------
# Wykrywanie stanów, w których skan nie ma sensu
# --------------------------------------------------------------------------

_WAF = re.compile(
    r"(Attention Required|cf-browser-verification|Just a moment|Request unsuccessful|"
    r"Incapsula|Access Denied|Web Application Firewall|__cf_chl)",
    re.I,
)


def skontroluj_odpowiedz(o: Odpowiedz, *, oczekiwany_kontener: str) -> None:
    """Rzuca `BladRekonesansu`, gdy odpowiedź nie jest prawdziwą stroną dawro."""
    if o.status != 200:
        raise BladRekonesansu(f"HTTP {o.status} dla {o.url_koncowy}")
    if _WAF.search(o.tekst):
        raise BladRekonesansu(f"WAF/anty-bot na {o.url_koncowy}")
    if len(o.bajty) < 512 or "<html" not in o.tekst.lower():
        raise BladRekonesansu(
            f"odpowiedź nie wygląda na stronę HTML "
            f"({len(o.bajty)} B) — {o.url_koncowy}"
        )
    sciezka_koncowa = re.sub(r"^https?://[^/]+", "", o.url_koncowy).rstrip("/")
    if not sciezka_koncowa or sciezka_koncowa in ("/index", "/aukcje"):
        # `/aukcje` to strona-landing z kuratorowanymi kolumnami, nie
        # paginowana lista — trafienie tam znaczy, że nasz adres listy
        # przestał działać.
        raise BladRekonesansu(
            f"przekierowanie na stronę główną/landing: {o.url_koncowy}"
        )
    if oczekiwany_kontener not in o.tekst:
        raise BladRekonesansu(
            f"brak kontenera '{oczekiwany_kontener}' w {o.url_koncowy}"
        )


# --------------------------------------------------------------------------
# Parsowanie listy
# --------------------------------------------------------------------------

_KONTENER_LISTY = 'class="fl aukcja-box"'
_KONTENER_SZCZEGOLOW = 'class="pasek-informacyjny-licytacji"'

_POCZATEK_BOKSU = re.compile(r'<div class="fl aukcja-box">')
_ID_ZE_SCIEZKI = re.compile(r"/aukcja/(\d+),([a-z0-9-]+)")
_KONIEC_W_BOKSIE = re.compile(
    r"Koniec aukcji:\s*<span[^>]*>\s*<b>\s*([\d-]{10}\s+\d{2}:\d{2})"
)
_NAJW_OFERTA_W_BOKSIE = re.compile(
    r'<div class="najwyzsza-oferta">(.*?)'
    r'(?=<div class="twoja-oferta"|<div class="k">|</div>\s*</div>)',
    re.S,
)
_KV_LISTA = re.compile(
    r'<div class="k">([^<]+?):?</div>\s*<div class="w">(.*?)</div>', re.S
)
_H2_NAZWA = re.compile(r'<h2 class="nazwa"[^>]*>(.*?)</h2>', re.S)


def _lista_str(wartosc: object) -> list[object]:
    return wartosc if isinstance(wartosc, list) else []


def _czysty(fragment: str) -> str:
    bez_tagow = re.sub(r"<[^>]+>", " ", fragment)
    return htmllib.unescape(re.sub(r"\s+", " ", bez_tagow)).strip()


def _kwota(surowe: str | None) -> str | None:
    """Zostawia surową kwotę bez waluty; `None`, gdy pola nie ma."""
    if not surowe:
        return None
    m = re.search(r"([\d\s\xa0]+,\d{2}|\d[\d\s\xa0]*\d)", surowe)
    return re.sub(r"[\s\xa0]+", " ", m.group(1)).strip() if m else None


def podstawa_ceny(surowa_etykieta: str | None) -> str:
    """`NETTO` / `BRUTTO` tylko, gdy strona to mówi wprost. Inaczej `UNKNOWN`.

    Bez automatycznego przeliczania VAT — to jest decyzja adaptera, nie
    rekonesansu (spec §4, §Założenia).
    """
    if not surowa_etykieta:
        return "UNKNOWN"
    tekst = surowa_etykieta.lower()
    ma_netto = "netto" in tekst
    ma_brutto = "brutto" in tekst
    if ma_netto and not ma_brutto:
        return "NETTO"
    if ma_brutto and not ma_netto:
        return "BRUTTO"
    return "UNKNOWN"


def parsuj_liste(tekst: str) -> list[dict[str, object]]:
    """Pozycje z jednej strony listy. Rzuca `BladRekonesansu` bez kontenera."""
    if _KONTENER_LISTY not in tekst:
        raise BladRekonesansu("strona listy bez kontenera 'aukcja-box'")
    # Każdy kafelek to fragment od jego `aukcja-box` do początku następnego
    # (albo do końca strony). Prostsze i pewniejsze niż zgadywanie, jaki
    # `</div>` domyka kafelek.
    granice = [m.start() for m in _POCZATEK_BOKSU.finditer(tekst)] + [len(tekst)]
    pozycje: list[dict[str, object]] = []
    for i in range(len(granice) - 1):
        blok = tekst[granice[i] : granice[i + 1]]
        m = _ID_ZE_SCIEZKI.search(blok)
        if m is None:
            continue
        kv = {k.strip().lower(): v for k, v in (_KV_LISTA.findall(blok))}
        h2 = _H2_NAZWA.search(blok)
        nazwa_pelna = _czysty(h2.group(1)) if h2 else None
        # dawro dokleja numer rejestracyjny do nazwy: "Fiat Ducato, NO310CJ".
        # Zostawiamy pełny string dla redakcji i osobno nazwę bez tablicy.
        nazwa = (
            re.sub(r",\s*[A-Z]{2,3}[0-9A-Z]{4,5}\s*$", "", nazwa_pelna).strip()
            if nazwa_pelna
            else None
        )
        koniec = _KONIEC_W_BOKSIE.search(blok)
        etykieta_ceny = next((k for k in kv if "wywo" in k and "awcza" in k), "")
        surowa_cena = next(
            (v for k, v in kv.items() if "wywo" in k and "awcza" in k), None
        )
        n_of = _NAJW_OFERTA_W_BOKSIE.search(blok)
        najwyzsza = _czysty(n_of.group(1)) if n_of else None
        if not najwyzsza or najwyzsza in ("\xa0",):
            najwyzsza = None
        pozycje.append(
            {
                "external_id": m.group(1),
                "slug": m.group(2),
                "url": f"{BAZA}/aukcja/{m.group(1)},{m.group(2)}",
                "nazwa": nazwa,
                "nazwa_pelna": nazwa_pelna,
                "koniec": koniec.group(1) if koniec else None,
                "cena_wywolawcza": (
                    _kwota(_czysty(surowa_cena)) if surowa_cena else None
                ),
                "cena_wywolawcza_etykieta": (
                    _czysty(surowa_cena) if surowa_cena else None
                ),
                "podstawa_ceny": podstawa_ceny(etykieta_ceny),
                "najwyzsza_oferta": _kwota(najwyzsza),
                "rok": next(
                    (
                        _czysty(v).replace(" r.", "").strip()
                        for k, v in kv.items()
                        if k.startswith("rok")
                    ),
                    None,
                ),
                "przebieg": next(
                    (
                        re.sub(r"[^\d]", "", _czysty(v)) or None
                        for k, v in kv.items()
                        if "przebieg" in k
                    ),
                    None,
                ),
                "sprzedawca": next(
                    (_czysty(v) for k, v in kv.items() if "sprzedaj" in k), None
                ),
                "forma_sprzedazy": next(
                    (_czysty(v) for k, v in kv.items() if "forma" in k), None
                ),
            }
        )
    return pozycje


# --------------------------------------------------------------------------
# Parsowanie szczegółów
# --------------------------------------------------------------------------

_KV_SZCZEGOLY = re.compile(
    r'<div class="fl k">([^<]+?)</div>\s*<div class="fl v">(.*?)</div>', re.S
)
_TS_ODLICZANIA = re.compile(r"Zegar\.odliczanie\((\d{9,})\)")
_CZAS_TRWANIA = re.compile(
    r'koniec-aukcji">\s*od\s+([\d-]{10})\s+do\s+([\d-]{10}\s+\d{2}:\d{2})'
)
_KWOTY = re.compile(r'id="kwoty">(.*?)</div>\s*<div class="clear">', re.S)
_PARKING = re.compile(r'<div class="adres">(.*?)</div>', re.S)
_OPIS_FIRMY = re.compile(r'<div class="opis">\s*(.*?)\s*</div>', re.S)
_PELNE_ZDJECIE = re.compile(r'<a class="jackbox"[^>]*href="(/cache/zdjecia/[^"]+)"')
# Tytuł pojazdu to <h1 class="fl nazwa-przedmiotu">. Bez zakotwiczenia
# w tej klasie `.search` łapał pierwszy <h1> ze strony — a od kiedy regulamin
# aukcji jest wstawiany inline w #regulamin, jego nagłówki ("I. Słownik pojęć")
# stoją przed blokiem POLECANE i wygrywały. Numer rejestracyjny doklejony do
# nazwy ("Hummer H2, DW929KM") ucinamy tak samo jak na liście.
_H1_SZCZEGOLY = re.compile(
    r'<h1[^>]*class="[^"]*nazwa-przedmiotu[^"]*"[^>]*>\s*(.*?)\s*</h1>', re.S
)
_TABLICA_W_NAZWIE = re.compile(r",\s*[A-Z]{2,3}[0-9A-Z]{4,5}\s*$")

_MAPA_POL = {
    "vin": ("vin",),
    "nr_rejestracyjny": ("nr rejestracyjny", "nr rej"),
    "opis_modelu": ("opis modelu",),
    "rok_produkcji": ("rok produkcji",),
    "przebieg": ("przebieg",),
    "pojemnosc": ("pojemność", "pojemnosc"),
    "moc": ("moc",),
    "paliwo": ("paliwo", "rodzaj paliwa"),
    "skrzynia": ("skrzynia", "skrzynia biegów"),
    "nadwozie": ("nadwozie", "typ nadwozia"),
    "sprzedawca": ("sprzedawca", "sprzedający"),
    "forma_sprzedazy": ("forma sprzedaży", "forma sprzedazy"),
}


def _blok_glowny(tekst: str) -> str:
    """Wycina główną aukcję, odcinając sekcję 'POLECANE AUKCJE'.

    Bez tego cięcia pola pojazdu miesza się z kilkunastoma rekomendacjami,
    które mają ten sam układ etykiet.
    """
    poczatek = tekst.find(_KONTENER_SZCZEGOLOW)
    if poczatek < 0:
        raise BladRekonesansu("strona szczegółów bez paska licytacji")
    koniec = tekst.find("POLECANE AUKCJE", poczatek)
    return tekst[poczatek : koniec if koniec > 0 else poczatek + 60_000]


def _end_z_ts(tekst: str) -> str | None:
    m = _TS_ODLICZANIA.search(tekst)
    if m is None:
        return None
    chwila = dt.datetime.fromtimestamp(int(m.group(1)), tz=dt.UTC).astimezone(TZ)
    return chwila.strftime("%Y-%m-%d %H:%M:%S")


def parsuj_szczegoly(tekst: str, external_id: str) -> dict[str, object]:
    blok = _blok_glowny(tekst)
    kv = {
        k.strip().lower().rstrip(":"): _czysty(v)
        for k, v in _KV_SZCZEGOLY.findall(blok)
    }

    pola: dict[str, object] = {}
    for nazwa, klucze in _MAPA_POL.items():
        pola[nazwa] = next(
            (
                v
                for k, v in kv.items()
                if any(k == kk or k.startswith(kk) for kk in klucze)
            ),
            None,
        )

    kwoty = _KWOTY.search(blok)
    surowa_cena = None
    etykieta_ceny = ""
    najwyzsza_placeholder = None
    if kwoty:
        pary = re.findall(r'<div class="fl"[^>]*>(.*?)</div>', kwoty.group(1), re.S)
        czyste = [_czysty(p) for p in pary]
        for i, p in enumerate(czyste):
            if "wywo" in p.lower() and i + 1 < len(czyste):
                # Podstawa (netto/brutto) siedzi w ETYKIECIE, nie w kwocie.
                etykieta_ceny = p
                surowa_cena = czyste[i + 1]
            if "najwy" in p.lower() and "oferta" in p.lower() and i + 1 < len(czyste):
                najwyzsza_placeholder = czyste[i + 1]

    parking = _PARKING.search(blok)
    # Galeria „male" to pas miniatur pod ceną; pełne zdjęcia to `href`
    # znaczników `a.jackbox` po tej kotwicy (przed sekcją POLECANE). bxSlider
    # klonuje pierwszy i ostatni slajd dla pętli, więc odsiewamy duplikaty
    # zachowując kolejność.
    kotwica = tekst.find('id="zdjecie-male"')
    do_polecanych = tekst.find("POLECANE AUKCJE")
    zdjecia = (
        list(
            dict.fromkeys(
                _PELNE_ZDJECIE.findall(
                    tekst[
                        kotwica : do_polecanych
                        if do_polecanych > kotwica
                        else len(tekst)
                    ]
                )
            )
        )
        if kotwica >= 0
        else []
    )
    h1 = _H1_SZCZEGOLY.search(tekst)
    nazwa_pojazdu = _TABLICA_W_NAZWIE.sub("", _czysty(h1.group(1))) if h1 else None

    return {
        "external_id": external_id,
        "nazwa": nazwa_pojazdu or pola.get("opis_modelu"),
        "end_ts": _end_z_ts(tekst),
        "czas_trwania": (
            list(m_czas.groups()) if (m_czas := _CZAS_TRWANIA.search(blok)) else None
        ),
        "parking": _czysty(parking.group(1)) if parking else None,
        "opis_firmy": next(
            (_czysty(m) for m in _OPIS_FIRMY.findall(blok) if "," in _czysty(m)),
            None,
        ),
        "cena_wywolawcza": _kwota(surowa_cena),
        "cena_wywolawcza_etykieta": surowa_cena,
        "podstawa_ceny": podstawa_ceny(etykieta_ceny),
        "najwyzsza_oferta_placeholder": najwyzsza_placeholder,
        "endpoint_najwyzszej_oferty": (
            ENDPOINT_OFERTY
            if re.search(r"pasekInformacyjny|PasekInformacyjny", tekst)
            else None
        ),
        "liczba_zdjec": len(zdjecia),
        "zdjecia": [BAZA + z for z in zdjecia],
        # Sygnały stanu aukcji — SUROWE, bez zgadywania „zakończona". dawro
        # renderuje odliczanie i tekst „Aukcja zakończona!" WYŁĄCZNIE po
        # stronie klienta (`Zegar.odliczanie` wpisuje je w `#czas-do-konca`
        # JS-em), więc curl ich nie zobaczy. Jedyne serwerowe sygnały są
        # strukturalne — i to pomiar domknięcia ma pokazać, który się zmienia.
        "strona_aukcji": _KONTENER_SZCZEGOLOW in tekst,
        "przystap_do_aukcji_widoczne": "przycisk-przystap" in tekst,
        "licytacja_za_logowaniem": "przycisk-przystap" in tekst
        and "Dialog.logowanie()" in tekst,
        "js_tekst_zakonczenia_obecny": "Aukcja zakończona!" in tekst,
        "recaptcha": "recaptcha/api.js" in tekst,
        "pola": pola,
    }


def pokrycie_pol(detale: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    """Ile aukcji ma daną informację. Materiał do decyzji, które pola są
    obowiązkowe w modelu, a które opcjonalne (spec §Weryfikacja)."""
    raport: dict[str, dict[str, int]] = {
        p: {"jest": 0, "brak": 0} for p in POLA_POKRYCIA
    }
    for d in detale:
        pola = d.get("pola", {})
        assert isinstance(pola, dict)
        wartosci = {
            "vin": pola.get("vin"),
            "nr_rejestracyjny": pola.get("nr_rejestracyjny"),
            "parking": d.get("parking"),
            "opis_modelu": pola.get("opis_modelu"),
            "rok_produkcji": pola.get("rok_produkcji"),
            "przebieg": pola.get("przebieg"),
            "pojemnosc": pola.get("pojemnosc"),
            "moc": pola.get("moc"),
            "paliwo": pola.get("paliwo"),
            "skrzynia": pola.get("skrzynia"),
            "nadwozie": pola.get("nadwozie"),
            "sprzedawca": pola.get("sprzedawca"),
            "forma_sprzedazy": pola.get("forma_sprzedazy"),
            "zdjecia": d.get("liczba_zdjec") or None,
            "cena_wywolawcza": d.get("cena_wywolawcza"),
            "podstawa_ceny": (
                None if d.get("podstawa_ceny") == "UNKNOWN" else d.get("podstawa_ceny")
            ),
        }
        for pole, wartosc in wartosci.items():
            raport[pole]["jest" if wartosc else "brak"] += 1
    return raport


# --------------------------------------------------------------------------
# Galeria
# --------------------------------------------------------------------------


def zbadaj_zdjecia(
    sesja: Sesja, zdjecia: list[str], *, wszystkie: bool
) -> dict[str, object]:
    """Sprawdza dostępność zdjęć. Domyślnie pierwsze i ostatnie; `--probe-images
    all` sprawdza każde (spec §4)."""
    if not zdjecia:
        return {"liczba": 0, "sprawdzone": [], "wszystkie_ok": None}
    docelowe = zdjecia if wszystkie else [zdjecia[0], zdjecia[-1]]
    wyniki: list[dict[str, object]] = []
    for adres in dict.fromkeys(docelowe):
        try:
            o = sesja.get(adres)
            typ = o.headers.get("content-type", "")
            wyniki.append(
                {
                    "url": adres,
                    "http": o.status,
                    "bytes": len(o.bajty),
                    "obraz": typ.startswith("image/"),
                }
            )
        except BladRekonesansu as exc:
            wyniki.append({"url": adres, "error": str(exc)})
    return {
        "liczba": len(zdjecia),
        "sprawdzone": wyniki,
        "wszystkie_ok": all(w.get("obraz") for w in wyniki),
    }


# --------------------------------------------------------------------------
# Skan listy
# --------------------------------------------------------------------------


def skan(
    sesja: Sesja, zapisz_html: Callable[[str, Odpowiedz], None]
) -> list[dict[str, object]]:
    """Przechodzi kolejne strony listy aż do braku nowych pozycji.

    Koniec listy = strona bez ani jednej nowej aukcji (dawro przy `ilosc,100`
    i katalogu < 100 pozycji zwraca stronę 1 także dla `strona,2` — to nie
    błąd, tylko koniec).

    Pętla paginacji = strona powtarza zbiór aukcji z WCZEŚNIEJSZEJ strony niż
    poprzednia (serwis cyklicznie podaje tę samą treść). Wtedy przerywamy
    błędem — inaczej `MAKS_STRON` żądań poszłoby w kółko.
    """
    wszystkie: dict[str, dict[str, object]] = {}
    widziane_zbiory: set[frozenset[str]] = set()
    poprzedni_zbior: frozenset[str] | None = None
    for numer in range(1, MAKS_STRON + 1):
        o = sesja.get(WZORZEC_LISTY.format(strona=numer))
        skontroluj_odpowiedz(o, oczekiwany_kontener=_KONTENER_LISTY)
        zapisz_html(f"lista-{numer:02d}.html", o)
        pozycje = parsuj_liste(o.tekst)
        zbior = frozenset(str(p["external_id"]) for p in pozycje)
        if not zbior:
            break
        nowe = [p for p in pozycje if str(p["external_id"]) not in wszystkie]
        if not nowe:
            break
        if zbior != poprzedni_zbior and zbior in widziane_zbiory:
            raise BladRekonesansu(
                f"strona {numer} powtarza zbiór aukcji sprzed poprzedniej — "
                "pętla paginacji"
            )
        widziane_zbiory.add(zbior)
        for p in pozycje:
            wszystkie.setdefault(str(p["external_id"]), p)
        poprzedni_zbior = zbior
    else:
        raise BladRekonesansu(
            f"lista nie skończyła się w {MAKS_STRON} stronach — podejrzenie "
            "nieskończonej paginacji"
        )
    return list(wszystkie.values())


# --------------------------------------------------------------------------
# Pomiar domknięcia
# --------------------------------------------------------------------------


def _parsuj_koniec(tekst: str | None) -> dt.datetime | None:
    if not tekst:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(tekst, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def _probka_domkniecia(
    sesja: Sesja,
    url: str,
    external_id: str,
    tag: str,
    zapisz_html: Callable[[str, Odpowiedz], None],
    poprzednia: dict[str, object] | None,
) -> dict[str, object]:
    teraz = dt.datetime.now(TZ)
    try:
        o = sesja.get(url, pilne=True)
    except BladRekonesansu as exc:
        rec: dict[str, object] = {
            "tag": tag,
            "ts": teraz.isoformat(),
            "error": str(exc),
        }
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        return rec
    zapisz_html(f"domkniecie-{tag}.html", o)
    szczegoly = (
        parsuj_szczegoly(o.tekst, external_id)
        if _KONTENER_SZCZEGOLOW in o.tekst
        else {}
    )
    rec = {
        "tag": tag,
        "ts": teraz.isoformat(),
        "http": o.status,
        "przeskoki": o.przeskoki,
        "url_koncowy": o.url_koncowy,
        "bytes": len(o.bajty),
        "cena_wywolawcza": szczegoly.get("cena_wywolawcza"),
        "najwyzsza_oferta_placeholder": szczegoly.get("najwyzsza_oferta_placeholder"),
        "end_ts": szczegoly.get("end_ts"),
        "strona_aukcji": _KONTENER_SZCZEGOLOW in o.tekst,
        "przystap_do_aukcji_widoczne": szczegoly.get("przystap_do_aukcji_widoczne"),
        "przekierowano_poza_aukcje": _KONTENER_SZCZEGOLOW not in o.tekst,
    }
    if poprzednia:
        rec["zmiany"] = sorted(
            k
            for k in (
                "cena_wywolawcza",
                "najwyzsza_oferta_placeholder",
                "end_ts",
                "strona_aukcji",
                "przystap_do_aukcji_widoczne",
                "przekierowano_poza_aukcje",
                "http",
            )
            if rec.get(k) != poprzednia.get(k)
        )
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    return rec


def pomiar_domkniecia(
    sesja: Sesja,
    url: str,
    external_id: str,
    zapisz_html: Callable[[str, Odpowiedz], None],
    po_koncu: list[int],
) -> list[dict[str, object]]:
    probki: list[dict[str, object]] = []
    poprzednia: dict[str, object] | None = None

    pierwsza = _probka_domkniecia(sesja, url, external_id, "t0", zapisz_html, None)
    probki.append(pierwsza)
    koniec = _parsuj_koniec(str(pierwsza.get("end_ts") or ""))
    if koniec is None:
        raise BladRekonesansu("nie odczytano terminu aukcji do pomiaru domknięcia")
    print(f"# termin aukcji: {koniec.isoformat()}", flush=True)
    poprzednia = pierwsza

    # Faza 1 — gęsty run-up, z ciągłym odczytem terminu (dogrywka go przesuwa).
    while True:
        zostalo = (koniec - dt.datetime.now(TZ)).total_seconds()
        if zostalo <= 0:
            break
        if zostalo > RUNUP_OD_S:
            time.sleep(min(zostalo - RUNUP_OD_S, 60))
            continue
        time.sleep(min(RUNUP_KROK_S, max(zostalo, 1)))
        pozostalo = int(max((koniec - dt.datetime.now(TZ)).total_seconds(), 0))
        rec = _probka_domkniecia(
            sesja, url, external_id, f"pre-{pozostalo}s", zapisz_html, poprzednia
        )
        probki.append(rec)
        poprzednia = rec
        nowy = _parsuj_koniec(str(rec.get("end_ts") or ""))
        if nowy and (nowy - koniec).total_seconds() > DOGRYWKA_TOLERANCJA_S:
            print(
                f"# DOGRYWKA: {koniec.isoformat()} -> {nowy.isoformat()} "
                f"(+{(nowy - koniec).total_seconds():.0f}s)",
                flush=True,
            )
            rec["dogrywka_shift_s"] = (nowy - koniec).total_seconds()
            koniec = nowy
        elif nowy and nowy > koniec:
            koniec = nowy

    # Faza 2 — drabinka po wygaśnięciu (sedno pomiaru).
    for offset in po_koncu:
        cel = koniec + dt.timedelta(seconds=offset)
        opoznienie = (cel - dt.datetime.now(TZ)).total_seconds()
        if opoznienie > 0:
            time.sleep(opoznienie)
        rec = _probka_domkniecia(
            sesja, url, external_id, f"post+{offset}s", zapisz_html, poprzednia
        )
        probki.append(rec)
        poprzednia = rec
    return probki


# --------------------------------------------------------------------------
# Artefakty
# --------------------------------------------------------------------------


def zredaguj_do_repo(tmp: pathlib.Path, cel: pathlib.Path) -> dict[str, int]:
    """Przenosi zrzuty z katalogu tymczasowego do repo po redakcji.

    Używa tego samego mechanizmu co reszta fixtures: zbiera mapowanie
    tablic rejestracyjnych ze wszystkich plików naraz, potem redaguje każdy
    (VIN-y, tablice, loginy) i dopisuje wyprodukowane zamienniki do rejestru.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import redakcja_fixtures as rf

    pliki = sorted(
        p for p in tmp.iterdir() if p.suffix in {".html", ".json", ".md", ".txt"}
    )
    tresci = {p: rf._wczytaj(p) for p in pliki}
    znane = rf.wczytaj_rejestr()
    rejestracje = {
        o: z
        for o, z in rf.zbierz_rejestracje(list(tresci.values())).items()
        if o not in znane
    }
    znane.update(rejestracje.values())

    suma = {"vin": 0, "rejestracja": 0, "login": 0}
    cel.mkdir(parents=True, exist_ok=True)
    for plik in pliki:
        nowy, liczniki = rf.redaguj(tresci[plik], rejestracje, znane)
        for k, v in liczniki.items():
            suma[k] += v
        rf._zapisz(cel / plik.name, nowy)
    rf.zapisz_rejestr(znane)
    return suma


def zapisz_raport(
    cel: pathlib.Path,
    pozycje: list[dict[str, object]],
    detale: list[dict[str, object]],
    pokrycie: dict[str, dict[str, int]],
    galerie: dict[str, dict[str, object]],
    domkniecie: list[dict[str, object]] | None,
    status_domkniecia: str,
    liczba_zadan: int,
) -> None:
    linie: list[str] = []
    linie.append("# RECON dawro.pl — ETAP 0\n")
    linie.append(
        f"Wygenerowano: {dt.datetime.now(TZ).isoformat(timespec='seconds')}  \n"
        f"Żądań w tym przebiegu: {liczba_zadan}\n"
    )
    linie.append("## Listy i identyfikatory\n")
    linie.append(
        f"- Adres skanu: `{WZORZEC_LISTY.format(strona='N')}` "
        f"(sortowanie po dacie zakończenia rosnąco, 100/stronę, układ *boxy*)\n"
        f"- `external_id`: liczba ze ścieżki `/aukcja/<id>,<slug>` — slug jest "
        f"zmienny, `<id>` stały\n"
        f"- Aukcji na liście: **{len(pozycje)}**\n"
        "- Kodowanie: treść jest UTF-8, mimo że nagłówek HTTP mówi "
        "`iso-8859-1` — nagłówkowi nie ufać\n"
        f"- robots.txt: `Allow: /` dla wszystkich; sitemap "
        f"`{BAZA}/sitemap.xml`\n"
    )
    sprzedawcy = sorted(
        {str(p.get("sprzedawca")) for p in pozycje if p.get("sprzedawca")}
    )
    linie.append(f"- Sprzedawcy w tej próbce: {', '.join(sprzedawcy) or '—'}\n")

    linie.append("\n## Pokrycie pól (szczegóły)\n")
    linie.append("| pole | jest | brak |\n|---|---:|---:|\n")
    for pole, licz in pokrycie.items():
        linie.append(f"| {pole} | {licz['jest']} | {licz['brak']} |\n")

    linie.append("\n## Cena\n")
    podstawy: dict[str, int] = {}
    for d in detale:
        podstawy[str(d.get("podstawa_ceny"))] = (
            podstawy.get(str(d.get("podstawa_ceny")), 0) + 1
        )
    linie.append(
        "- Podstawa ceny ustalana **wyłącznie** z jawnego „netto”/„brutto” "
        "na stronie; brak oznaczenia → `UNKNOWN`, bez przeliczania VAT.\n"
        f"- Rozkład w próbce: {json.dumps(podstawy, ensure_ascii=False)}\n"
        "- „Najwyższa oferta” na stronie szczegółów ładowana AJAX-em przez "
        f"`POST {ENDPOINT_OFERTY}` (`{{id}}` → `{{kwota, twoja_oferta}}`). "
        "Endpoint jest publiczny (strona woła go anonimowo co 2 s), ale ten "
        "skrypt go **nie wywołuje**. Serwerowo najwyższą ofertę podaje kafelek "
        "listy (`div.najwyzsza-oferta`).\n"
    )

    linie.append("\n## Galeria\n")
    ze_zdjeciami = sum(1 for g in galerie.values() if g.get("liczba"))
    linie.append(
        f"- Aukcji ze zdjęciami: {ze_zdjeciami}/{len(galerie)}\n"
        "- Pełne zdjęcie: `href` znacznika `a.jackbox` w `#zdjecie-male` "
        "(`/cache/zdjecia/…`)\n"
    )
    for eid, g in list(galerie.items())[:5]:
        linie.append(
            f"  - `{eid}`: {g.get('liczba')} zdjęć, "
            f"pierwsze/ostatnie {'OK' if g.get('wszystkie_ok') else 'do sprawdzenia'}\n"
        )

    linie.append("\n## Domknięcie\n")
    linie.append(f"- Status: **{status_domkniecia}**\n")
    if domkniecie:
        linie.append(
            f"- Próbek: {len(domkniecie)} "
            f"(`{PO_KONCU_S}` s po terminie, run-up co {RUNUP_KROK_S} s "
            f"od T-{RUNUP_OD_S} s)\n"
            f"- Surowe odczyty: `pomiar-domkniecie-*.json`, zrzuty "
            f"`domkniecie-*.html`\n"
        )
        znika = [
            r["tag"]
            for r in domkniecie
            if r.get("przekierowano_poza_aukcje") or r.get("http") not in (200, None)
        ]
        bez_przystap = [
            r["tag"]
            for r in domkniecie
            if r.get("strona_aukcji") and not r.get("przystap_do_aukcji_widoczne")
        ]
        cena_znika = [
            r["tag"]
            for r in domkniecie
            if r.get("strona_aukcji") and not r.get("cena_wywolawcza")
        ]
        linie.append(
            f"- Strona przestaje być stroną aukcji przy: "
            f"{znika[0] if znika else 'nie zaobserwowano w oknie pomiaru'}\n"
            f"- Przycisk „PRZYSTĄP DO AUKCJI” znika przy: "
            f"{bez_przystap[0] if bez_przystap else 'nie zaobserwowano'}\n"
            f"- Cena wywoławcza przestaje być czytana przy: "
            f"{cena_znika[0] if cena_znika else 'nie zaobserwowano'}\n"
        )
    linie.append(
        "\n## Do ustalenia w planie adaptera\n"
        "- Drabinka domknięcia (z `pomiar-domkniecie-*.json`).\n"
        "- Polityka brutto/netto per sprzedawca (kolumna `podstawa_ceny`).\n"
        "- Czy `POST /WebService/PasekInformacyjny/` wolno odpytywać w adapterze, "
        "czy trzymać się kafelka listy.\n"
    )
    (cel / "raport.md").write_text("".join(linie), encoding="utf-8")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def _wybierz_do_domkniecia(
    pozycje: list[dict[str, object]],
) -> dict[str, object] | None:
    teraz = dt.datetime.now(TZ)
    kandydaci = [
        (k, p)
        for p in pozycje
        if (k := _parsuj_koniec(str(p.get("koniec") or ""))) and k > teraz
    ]
    if not kandydaci:
        return None
    kandydaci.sort(key=lambda para: para[0])
    return kandydaci[0][1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="wymuś pomiar wskazanej aukcji")
    ap.add_argument(
        "--static-only", action="store_true", help="skan i szczegóły, bez domknięcia"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="jedna próbka wskazanej/pierwszej aukcji i wyjście — kontrola parsera",
    )
    ap.add_argument("--rate-limit-per-minute", type=int, default=LIMIT_NA_MINUTE)
    ap.add_argument(
        "--probe-images", choices=("first-last", "all"), default="first-last"
    )
    ap.add_argument("--outdir", default="fixtures/dawro")
    ap.add_argument("--tmpdir", default=None)
    ap.add_argument(
        "--po-koncu",
        default=",".join(str(x) for x in PO_KONCU_S),
        help="sekundy po wygaśnięciu do próbkowania",
    )
    args = ap.parse_args(argv)

    cel = pathlib.Path(args.outdir)
    tmp = pathlib.Path(args.tmpdir or tempfile.mkdtemp(prefix="pomiar-dawro-"))
    tmp.mkdir(parents=True, exist_ok=True)
    sesja = Sesja(args.rate_limit_per_minute)
    meta: dict[str, object] = {
        "pobrano_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "user_agent": "Chrome/126 (desktop UA)",
        "zalogowany": False,
        "pliki": {},
    }

    def zapisz_html(nazwa: str, o: Odpowiedz) -> None:
        (tmp / nazwa).write_bytes(o.bajty)
        assert isinstance(meta["pliki"], dict)
        meta["pliki"][nazwa] = {
            "status": o.status,
            "bytes": len(o.bajty),
            "redirects": o.przeskoki,
            "url_koncowy": o.url_koncowy,
            "headers": {
                k: o.headers[k]
                for k in ("content-type", "cache-control", "etag", "last-modified")
                if k in o.headers
            },
        }

    # --dry-run: pojedyncza aukcja, bez czekania.
    if args.dry_run:
        url = args.url
        if not url:
            pozycje = skan(sesja, zapisz_html)
            if not pozycje:
                print("STOP: skan nie znalazł żadnej aukcji", file=sys.stderr)
                return 1
            url = str(pozycje[0]["url"])
        o = sesja.get(url)
        skontroluj_odpowiedz(o, oczekiwany_kontener=_KONTENER_SZCZEGOLOW)
        eid = _ID_ZE_SCIEZKI.search(url)
        detal = parsuj_szczegoly(o.tekst, eid.group(1) if eid else "0")
        print(json.dumps(detal, ensure_ascii=False, indent=2))
        return 0

    # Skan.
    try:
        pozycje = skan(sesja, zapisz_html)
    except BladRekonesansu as exc:
        print(f"STOP (skan): {exc}", file=sys.stderr)
        return 1
    if not pozycje:
        print("STOP: skan nie znalazł żadnej aukcji", file=sys.stderr)
        return 1
    print(f"# skan: {len(pozycje)} aukcji", flush=True)

    # Szczegóły każdej aukcji + galeria.
    detale: list[dict[str, object]] = []
    galerie: dict[str, dict[str, object]] = {}
    for p in pozycje:
        try:
            o = sesja.get(str(p["url"]))
            skontroluj_odpowiedz(o, oczekiwany_kontener=_KONTENER_SZCZEGOLOW)
        except BladRekonesansu as exc:
            print(f"# pominięto {p['external_id']}: {exc}", flush=True)
            continue
        zapisz_html(f"szczegoly-{p['external_id']}.html", o)
        d = parsuj_szczegoly(o.tekst, str(p["external_id"]))
        detale.append(d)
        galerie[str(p["external_id"])] = zbadaj_zdjecia(
            sesja,
            [str(z) for z in _lista_str(d.get("zdjecia"))],
            wszystkie=args.probe_images == "all",
        )

    pokrycie = pokrycie_pol(detale)

    # Domknięcie.
    domkniecie: list[dict[str, object]] | None = None
    status_domkniecia = "pominięte (--static-only)"
    kod_wyjscia = 0
    if not args.static_only:
        if args.url:
            wybrany_url, wybrany_id = (
                args.url,
                (
                    _ID_ZE_SCIEZKI.search(args.url).group(1)  # type: ignore[union-attr]
                    if _ID_ZE_SCIEZKI.search(args.url)
                    else "0"
                ),
            )
        else:
            kand = _wybierz_do_domkniecia(pozycje)
            wybrany_url = None
            if kand is None:
                status_domkniecia = "pomiar domknięcia do wykonania później"
                print(f"# {status_domkniecia}: brak aukcji z przyszłym terminem")
                kod_wyjscia = 2
            elif (
                koniec := _parsuj_koniec(str(kand["koniec"] or ""))
            ) and koniec - dt.datetime.now(TZ) > dt.timedelta(hours=24):
                # Najbliższa aukcja kończy się dopiero za dobę+. Nie wisimy
                # w `sleep` przez dni — skan i szczegóły są zapisane, pomiar
                # domknięcia trzeba odpalić bliżej terminu (`--url <URL>` albo
                # zaplanować, tak jak leasygroup).
                status_domkniecia = (
                    f"pomiar domknięcia do wykonania później — najbliższa aukcja "
                    f"({kand['external_id']}) kończy się {koniec:%Y-%m-%d %H:%M}"
                )
                print(f"# {status_domkniecia}")
                kod_wyjscia = 2
            else:
                wybrany_url = str(kand["url"])
                wybrany_id = str(kand["external_id"])
        if wybrany_url:
            po_koncu = [int(x) for x in args.po_koncu.split(",") if x.strip()]
            try:
                domkniecie = pomiar_domkniecia(
                    sesja, wybrany_url, wybrany_id, zapisz_html, po_koncu
                )
                status_domkniecia = "wykonane"
                (tmp / f"pomiar-domkniecie-{wybrany_id}.json").write_text(
                    json.dumps(domkniecie, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
            except BladRekonesansu as exc:
                status_domkniecia = f"przerwane: {exc}"
                print(f"STOP (domknięcie): {exc}", file=sys.stderr)
                kod_wyjscia = 1

    (tmp / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    zapisz_raport(
        tmp,
        pozycje,
        detale,
        pokrycie,
        galerie,
        domkniecie,
        status_domkniecia,
        sesja.licznik,
    )

    suma = zredaguj_do_repo(tmp, cel)
    print(
        f"# fixtures → {cel} po redakcji "
        f"(VIN {suma['vin']}, tablice {suma['rejestracja']}, loginy {suma['login']})",
        flush=True,
    )
    print(f"# raport: {cel / 'raport.md'}", flush=True)
    return kod_wyjscia


if __name__ == "__main__":
    raise SystemExit(main())
