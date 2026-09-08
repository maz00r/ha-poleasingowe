"""Parsowanie HTML z autoprzetarg.pl.

Czyste funkcje na tekście — bez sieci, bez bazy. Selektory pochodzą z plików
w `fixtures/autoprzetarg/` (SPEC.md §4).

Dwie rzeczy wyróżniają ten serwis:

- **VIN jest już na liście**, jako jedyny z czterech (RECON.md §4.4).
  Deduplikacja z §8.4 działa tu bez wchodzenia w szczegóły.
- **Czas zakończenia stoi w ukrytym polu `auctionEndDate`**, renderowanym
  serwerowo per pozycja. Widoczny licznik dorysowuje JS i jest pusty
  w zrzucie, więc to pole jest jedynym źródłem terminu.

Uwaga o parowaniu, która kosztowała mnie raz błędny wynik: ukryte pole stoi
**wewnątrz** kafelka `<a>`, ale **po** jego atrybucie `href`. Parowanie
„pole → następny link" przypisuje datę sąsiedniej aukcji i przesuwa cały
wynik o jeden. Dlatego czytamy je **z zasięgu kafelka**, a nie ze
spłaszczonej listy dopasowań po całej stronie.

Czego tu nie ma: **liczby ofert**. Serwis nie podaje jej bez zalogowania
nawet na stronie szczegółów (RECON.md §4.4), więc `bid_count` zostaje puste,
a nie zerowe — zero znaczyłoby „nikt nie licytował".
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser, Node

from app.application.ports import SurowaOferta
from app.domain.errors import ParseFailed

BAZOWY_URL = "https://autoprzetarg.pl"
SCIEZKA_LISTY = "/kategoria/Pojazdy1"

KAFELEK = "a.section-list-auctions-block-item"

# `/aukcja/<TYTUL>,<ID>,<Kategoria>` — identyfikator w ŚRODKOWYM segmencie.
# Kategoria w adresie pozwala zawęzić się do aut osobowych bez dodatkowego
# żądania (RECON.md §4.4).
_ID_Z_URL = re.compile(r"/aukcja/[^,]*,([^,/]+),")

# Pary `<b>Etykieta:</b> wartość<br>`. Wartość bywa pusta — „Przebieg:" bez
# liczby to normalny stan w tym serwisie i musi zostać pusty, a nie zerowy.
_PARA = re.compile(r"<b>\s*([^<:]{1,60}?)\s*:\s*</b>\s*([^<]{0,120})")

_END_DATE = re.compile(r'auctionEndDate[^>]*?value="([^"]*)"')


def _tekst(wezel: Node | None) -> str:
    return wezel.text(strip=True) if wezel is not None else ""


def id_z_url(url: str) -> str | None:
    trafienie = _ID_Z_URL.search(url.strip())
    return trafienie.group(1) if trafienie else None


def _pary_etykiet(html: str) -> dict[str, str]:
    return {
        m.group(1).strip(): m.group(2).strip()
        for m in _PARA.finditer(html)
        if m.group(2).strip()
    }


# Cena ma osobne klasy na liście i w szczegółach, ale w obu wypadkach jest
# to `<div>` z identyfikatorem albo klasą kończącą się na `-price`.
_CENA_SZCZEGOLY = "div#currentPrice"
_CENA_LISTA_TYTUL = "div.section-list-auctions-title"
_CENA_LISTA_WARTOSC = "div.section-list-auctions-price"


def _cena_i_koniec(korzen: Node) -> dict[str, str]:
    """Wyciąga cenę — z listy i ze szczegółów, bo mają różne klasy.

    Na liście etykieta i wartość to dwa sąsiednie `<div>`-y, w szczegółach
    wartość ma własny `id="currentPrice"`. Jeden selektor na oba nie istnieje,
    a zgadywanie po pozycji w drzewie byłoby kruche.
    """
    pola: dict[str, str] = {}

    etykiety = korzen.css(_CENA_LISTA_TYTUL)
    wartosci = korzen.css(_CENA_LISTA_WARTOSC)
    for etykieta, wartosc in zip(etykiety, wartosci, strict=False):
        nazwa = _tekst(etykieta).rstrip(":").strip()
        tresc = _tekst(wartosc)
        if nazwa and tresc:
            pola[nazwa] = tresc

    ze_szczegolow = korzen.css_first(_CENA_SZCZEGOLY)
    if ze_szczegolow is not None and _tekst(ze_szczegolow):
        pola["Aktualna cena aukcji"] = _tekst(ze_szczegolow)
    return pola


def sparsuj_liste(html: str) -> list[SurowaOferta]:
    """Pozycje z jednej strony listy.

    Najbogatsza lista z czterech serwisów: rocznik, pojemność i moc, tablice,
    VIN, przebieg, paliwo, sprzedający, lokalizacja, cena i absolutny termin.
    """
    drzewo = HTMLParser(html)
    oferty: list[SurowaOferta] = []
    widziane: set[str] = set()

    for kafelek in drzewo.css(KAFELEK):
        url = (kafelek.attributes.get("href") or "").strip()
        external_id = id_z_url(url)
        if external_id is None or external_id in widziane:
            continue
        widziane.add(external_id)

        wnetrze = kafelek.html or ""
        pola = _pary_etykiet(wnetrze)
        pola.update(_cena_i_koniec(kafelek))

        naglowek = kafelek.css_first("h3")
        if naglowek is not None:
            pola["nazwa"] = _tekst(naglowek)

        # Termin z zasięgu TEGO kafelka — patrz uwaga o parowaniu w docstringu.
        termin = _END_DATE.search(wnetrze)
        if termin is not None:
            pola["end_date"] = termin.group(1)

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
    pola.update(_cena_i_koniec(drzewo.body or drzewo.root))  # type: ignore[arg-type]

    termin = _END_DATE.search(html)
    if termin is not None:
        pola["end_date"] = termin.group(1)

    naglowek = drzewo.css_first("h1")
    if naglowek is not None:
        pola["nazwa"] = re.sub(r"\s+", " ", _tekst(naglowek))

    if "end_date" not in pola and "Aktualna cena aukcji" not in pola:
        raise ParseFailed(
            f"autoprzetarg: strona {external_id} nie wygląda na aukcję — "
            "brak zarówno terminu, jak i ceny"
        )
    return SurowaOferta(external_id=external_id, url=url, pola=pola)


# Cena w szczegolach ma wlasny identyfikator; na liscie klase.
_CENA_ID = re.compile(r'id="currentPrice"[^>]*>\s*([^<]*)')


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
