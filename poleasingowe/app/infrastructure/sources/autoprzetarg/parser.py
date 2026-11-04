"""Parsowanie HTML z autoprzetarg.pl.

Czyste funkcje na tekście — bez sieci, bez bazy. Selektory pochodzą z plików
w `fixtures/autoprzetarg/` (SPEC.md §4).

Serwis zmienił szablon jesienią 2026 (RECON.md §4.4a): karta `div.auction-card`
zamiast kafelka-linku, tabela zamiast par `<b>`, siatka `span.spec-*`
w szczegółach, dziewięć pozycji na stronę. Dwie rzeczy wyróżniają ten serwis:

- **VIN jest już na liście**, jako jedyny z czterech (RECON.md §4.4).
  Deduplikacja z §8.4 działa tu bez wchodzenia w szczegóły.
- **Termin ma dwie postacie.** Lista podaje go w UTC (`data-end-time` z `Z`),
  a szczegóły w czasie lokalnym bez strefy (`endDateServerValue` w skrypcie).
  Mapper rozróżnia je po obecności strefy.

Uwaga o parowaniu, która kosztowała mnie raz błędny wynik: termin i cenę
czytamy **z zasięgu karty**, a nie ze spłaszczonej listy dopasowań po całej
stronie. Parowanie po kolejności przypisało kiedyś datę sąsiedniej aukcji
i przesunęło cały wynik o jeden.

Czego tu nie ma: **liczby ofert**. Serwis nie podaje jej bez zalogowania
nawet na stronie szczegółów (RECON.md §4.4), więc `bid_count` zostaje puste,
a nie zerowe — zero znaczyłoby „nikt nie licytował".
"""

from __future__ import annotations

import html as kodowanie_html
import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from app.application.ports import Dokument, SurowaOferta
from app.domain.errors import ParseFailed

BAZOWY_URL = "https://autoprzetarg.pl"
SCIEZKA_LISTY = "/kategoria/Pojazdy1"

SIATKA = "div.auctions-grid"
KARTA = f"{SIATKA} div.auction-card"

# `/aukcja/<TYTUL>,<ID>,<Kategoria>` — identyfikator w ŚRODKOWYM segmencie.
# Kategoria w adresie pozwala zawęzić się do aut osobowych bez dodatkowego
# żądania (RECON.md §4.4).
_ID_Z_URL = re.compile(r"/aukcja/[^,]*,([^,/]+),")

# Ten sam adres niesie kategorię w OSTATNIM segmencie: `Samochody-osobowe`,
# `Naczepy-i-przyczepy`, `Motocykle`. To jedyne źródło rodzaju pojazdu w tym
# serwisie i jest darmowe — kategoria stoi już na liście, więc rozpoznanie
# nie kosztuje ani jednego żądania więcej (SPEC.md §12).
_KATEGORIA_Z_URL = re.compile(r"/aukcja/[^,]*,[^,/]+,([^,/?#]+)")

# Szczegóły: `<span class="spec-label">Etykieta</span>` i tuż za nią wartość.
# Pusta wartość to normalny stan tego serwisu (brak przebiegu) i musi zostać
# pusta, a nie zerowa.
_PARA = re.compile(
    r'class="spec-label">\s*([^<]{1,60}?)\s*</span>\s*'
    r'<span class="spec-value"[^>]*>\s*([^<]{0,160}?)\s*</span>'
)

# Czas lokalny serwisu, ten sam, którym skrypt strony odlicza do końca.
_END_DATE = re.compile(r"endDateServerValue\s*=\s*'([^']+)'")

# Etykiety różnią się między listą a szczegółami. Mapper zna jedną nazwę pola.
_ALIASY = {
    "Pojemność": "Pojemność silnika",
    "Silnik i Moc": "Pojemność silnika",
    "Przebieg pojazdu": "Przebieg",
    "Numer VIN": "VIN",
}


def _tekst(wezel: Node | None) -> str:
    return wezel.text(strip=True) if wezel is not None else ""


def id_z_url(url: str) -> str | None:
    trafienie = _ID_Z_URL.search(url.strip())
    return trafienie.group(1) if trafienie else None


def kategoria_z_url(url: str) -> str | None:
    """Kategoria pojazdu z adresu aukcji, np. `Naczepy-i-przyczepy`."""
    trafienie = _KATEGORIA_Z_URL.search(url.strip())
    return trafienie.group(1) if trafienie else None


def _pole(etykieta: str) -> str:
    nazwa = etykieta.strip().rstrip(":").strip()
    return _ALIASY.get(nazwa, nazwa)


def _pary_etykiet(html: str) -> dict[str, str]:
    """Pary ze szczegółów, z rozkodowaniem: serwis koduje polskie znaki encjami."""
    return {
        _pole(m.group(1)): kodowanie_html.unescape(m.group(2)).strip()
        for m in _PARA.finditer(html)
        if m.group(2).strip()
    }


_CENA_LISTA = "span.price-value"
_CENA_SZCZEGOLY = "div#currentPriceDisplay"
_NAPIS_Z_ETYKIETA = "div.vin-box, div.highlight-box > div, div.location-info"


def _pola_karty(karta: Node) -> dict[str, str]:
    """Dane z jednej karty listy: tabela plus napisy `Etykieta: wartość`."""
    pola: dict[str, str] = {}
    for wiersz in karta.css("table.data-table tr"):
        etykieta = _tekst(wiersz.css_first("td.label"))
        wartosc = _tekst(wiersz.css_first("td.value"))
        if etykieta and wartosc:
            pola[_pole(etykieta)] = wartosc
    for napis in karta.css(_NAPIS_Z_ETYKIETA):
        etykieta, dwukropek, wartosc = napis.text(separator=" ").partition(":")
        if dwukropek and wartosc.strip():
            pola[_pole(etykieta)] = " ".join(wartosc.split())
    if cena := _tekst(karta.css_first(_CENA_LISTA)):
        pola["Aktualna cena aukcji"] = cena
    return pola


def sparsuj_liste(html: str) -> list[SurowaOferta]:
    """Pozycje z jednej strony listy.

    Najbogatsza lista z czterech serwisów: rocznik, pojemność i moc, tablice,
    VIN, przebieg, paliwo, sprzedający, lokalizacja, cena i absolutny termin.
    Strona za ostatnią ma pustą siatkę, a strona bez siatki to nie jest lista,
    tylko np. wyzwanie WAF.
    """
    drzewo = HTMLParser(html)
    if drzewo.css_first(SIATKA) is None:
        raise ParseFailed("autoprzetarg: strona nie wygląda na listę aukcji")
    oferty: list[SurowaOferta] = []
    widziane: set[str] = set()

    for karta in drzewo.css(KARTA):
        link = karta.css_first("a.card-img")
        url = ((link.attributes.get("href") if link is not None else "") or "").strip()
        external_id = id_z_url(url)
        if external_id is None or external_id in widziane:
            continue
        widziane.add(external_id)

        pola = _pola_karty(karta)

        kategoria = kategoria_z_url(url)
        if kategoria:
            pola["kategoria"] = kategoria

        naglowek = karta.css_first("h3")
        if naglowek is not None:
            pola["nazwa"] = _tekst(naglowek)

        # Termin z zasięgu TEJ karty — patrz uwaga o parowaniu w docstringu.
        licznik = karta.css_first("div.badge-time")
        termin = licznik.attributes.get("data-end-time") if licznik else None
        if termin:
            pola["end_date"] = termin

        oferty.append(
            SurowaOferta(
                external_id=external_id,
                url=url if url.startswith("http") else f"{BAZOWY_URL}{url}",
                pola=pola,
            )
        )
    return oferty


def numery_stron(html: str) -> list[int]:
    """Numery stron z paginacji. Serwis liczy od 1 (RECON.md §4.4)."""
    numery: set[int] = set()
    for link in HTMLParser(html).css("a[href*='page=']"):
        trafienie = re.search(r"[?&;]page=(\d+)", link.attributes.get("href") or "")
        if trafienie:
            numery.add(int(trafienie.group(1)))
    return sorted(numery)


def sparsuj_szczegoly(html: str, external_id: str, url: str) -> SurowaOferta:
    """Szczegóły jednej aukcji.

    Strona szczegółów jest źródłem **rozstrzygającym** dla terminu: to na niej
    wyszła różnica, gdy raz sparowałem daty z listy o jeden w bok.
    """
    drzewo = HTMLParser(html)
    pola = _pary_etykiet(html)

    if cena := _tekst(drzewo.css_first(_CENA_SZCZEGOLY)):
        pola["Aktualna cena aukcji"] = cena

    termin = _END_DATE.search(html)
    if termin is not None:
        pola["end_date"] = termin.group(1)

    naglowek = drzewo.css_first("h1.auction-title")
    if naglowek is not None:
        pola["nazwa"] = re.sub(r"\s+", " ", _tekst(naglowek))

    kategoria = kategoria_z_url(url)
    if kategoria:
        pola["kategoria"] = kategoria

    if "end_date" not in pola and "Aktualna cena aukcji" not in pola:
        raise ParseFailed(
            f"autoprzetarg: strona {external_id} nie wygląda na aukcję — "
            "brak zarówno terminu, jak i ceny"
        )
    return SurowaOferta(external_id=external_id, url=url, pola=pola)


_CENA_ID = re.compile(r'id="currentPriceDisplay"[^>]*>\s*([^<]*)')


def odcisk_aukcji(html: str) -> str:
    """Kanoniczny odcisk tego, co z tej strony faktycznie czytamy (§11.3).

    **Zmierzone 2026-09-08:** hash surowej odpowiedzi jest tu bezużyteczny.
    Trzy kolejne żądania tej samej, niezmienionej aukcji dały trzy różne
    treści, a różnice pochodziły wyłącznie z otoczki: znaczniki odświeżania
    cache przy plikach CSS, token CSRF wyszukiwarki, link Cloudflare
    `cdn-cgi/content` i obfuskacja adresu e-mail zmieniana przy każdym
    renderze.

    Gonienie tego regexami po kolei byłoby kruche — piąte źródło szumu
    pojawiłoby się przy najbliższej zmianie szablonu. Zamiast tego składamy
    odcisk z **pól, które i tak parsujemy**: par `etykieta: wartość`, ceny
    i terminu. Wyciągnięcie ich to trzy przebiegi wyrażeń regularnych po
    tekście, czyli wciąż tanio wobec zbudowania drzewa DOM z 68 kB, o które
    w §11.3 chodzi.
    """
    czesci = [f"{k}={v}" for k, v in sorted(_pary_etykiet(html).items())]
    if cena := _CENA_ID.search(html):
        czesci.append(f"cena={cena.group(1).strip()}")
    if termin := _END_DATE.search(html):
        czesci.append(f"koniec={termin.group(1)}")
    return "|".join(czesci)


def czy_zakonczona(kod_http: int, url_koncowy: str) -> bool:
    """Aukcja po terminie **przestaje istnieć pod swoim adresem**.

    Zmierzone (RECON.md §3.4): 10-15 s po `ends_at` serwis odpowiada
    przekierowaniem na stronę główną. To jedyny marker stanu końcowego, jaki
    ten serwis daje — nie ma etykiety w treści, bo nie ma już treści.
    """
    return kod_http in (301, 302) or url_koncowy.rstrip("/") == BAZOWY_URL


# Galeria: `/uploads/photos/<external_id>/<N>.jpg`.
_ZDJECIA = re.compile(r'/uploads/photos/[^"\'\s]+\.(?:jpe?g|png|webp)', re.I)


def zdjecia(html: str) -> list[str]:
    """Adresy zdjęć pojazdu. Względne, więc rozwijamy je do bezwzględnych."""
    znalezione = dict.fromkeys(_ZDJECIA.findall(html))
    return [f"{BAZOWY_URL}{u}" if u.startswith("/") else u for u in znalezione]


def dokumenty(html: str) -> list[Dokument]:
    """Sekcja „Pliki i dokumentacja" — pliki z `/uploads/attachements/<id>/`.

    W tej samej sekcji stoi link do ogólnej instrukcji serwisu; to nie jest
    plik aukcji, więc bierzemy wyłącznie załączniki.
    """
    drzewo = HTMLParser(html)
    wynik: list[Dokument] = []
    for link in drzewo.css("a.doc-button[href]"):
        href = link.attributes.get("href") or ""
        if "/uploads/attachements/" not in href:
            continue
        wynik.append(
            Dokument(
                nazwa=_tekst(link.css_first("strong")) or href.rsplit("/", 1)[-1],
                url=urljoin(BAZOWY_URL, href),
            )
        )
    return wynik
