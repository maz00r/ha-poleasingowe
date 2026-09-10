"""Czyste parsowanie HTML z aukcje.leasygroup.pl.

Leasygroup udostępnia na jednej liście licytacje i oferty „Kup teraz”.
Adapter świadomie wpuszcza wyłącznie pierwsze: to inny mechanizm cenowy i
inny cykl życia niż aukcja, więc zmieszanie ich fałszowałoby historię cen.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from app.application.ports import SurowaOferta
from app.domain.errors import ParseFailed

BAZOWY_URL = "https://aukcje.leasygroup.pl"
SCIEZKA_LISTY = "/aukcje/pojazdy-samochodowe-i-motocykle/widok-lista/strona-{}"

_ID_Z_URL = re.compile(r"^/aukcja/(\d+)/[^/]+/?$")
_NUMER_STRONY = re.compile(r"/strona-(\d+)/?$")
_ODLICZANIE = re.compile(r"^\d+\s*:\s*\d{1,2}\s*:\s*\d{1,2}$")
_ROK = re.compile(r"^(?:19|20)\d{2}$")
_PRZEBIEG = re.compile(r"\d[\d\s\xa0]*\s*km$", re.I)
_KONTAKT = re.compile(
    r"(?:,?\s*(?:tel(?:efon)?\.?|phone|e-?mail)\s*[:.]?\s*.*|"
    r"\s+[\w.+-]+@[\w.-]+\.[a-z]{2,}|"
    r",?\s*\+?48(?:[\s-]?\d){9,})$",
    re.I,
)


def _tekst(element: Node | None) -> str:
    if element is None:
        return ""
    return " ".join(element.text(separator=" ", strip=True).split())


def _pole_ceny(kafelek: Node, pola: dict[str, str], *, prefix: str = "cena") -> None:
    wartosc = _tekst(kafelek.css_first(".price"))
    waluta = _tekst(kafelek.css_first(".currency_value"))
    if wartosc:
        pola[prefix] = wartosc
    if "netto" in waluta.lower():
        pola[f"{prefix}_podstawa"] = "netto"
    elif "brutto" in waluta.lower():
        pola[f"{prefix}_podstawa"] = "brutto"


def _uzupelnij_skrot_pojazdu(kafelek: Node, pola: dict[str, str]) -> None:
    """Lista ma ikony zamiast etykiet; rozpoznajemy stabilne formaty wartości."""
    for span in kafelek.css(".offer_details .inside_single_box span"):
        wartosc = _tekst(span)
        if not wartosc:
            continue
        if _ROK.match(wartosc):
            pola["Rok produkcji"] = wartosc
        elif _PRZEBIEG.search(wartosc):
            pola["Przebieg"] = wartosc
        elif "Paliwo" not in pola:
            # Jedyny pozostały tekstowy element w tych trzech ikonach to
            # paliwo. Szczegóły doprecyzują dane nowo odkrytej aukcji.
            pola["Paliwo"] = wartosc


def numery_stron(html: str) -> list[int]:
    """Numery stron z właściwego paginatora, bez linków stopki i menu."""
    drzewo = HTMLParser(html)
    paginator = drzewo.css_first("div.pagination_container")
    if paginator is None:
        return []
    wynik: set[int] = set()
    for link in paginator.css("a.pagination_link"):
        dopasowanie = _NUMER_STRONY.search(link.attributes.get("href") or "")
        if dopasowanie is not None:
            wynik.add(int(dopasowanie.group(1)))
    return sorted(wynik)


def identyfikatory_wierszy(html: str) -> list[str]:
    """ID wszystkich wierszy, także „Kup teraz”, do wykrycia pętli stron."""
    drzewo = HTMLParser(html)
    kontener = drzewo.css_first("div.products_list_rows_container")
    if kontener is None:
        raise ParseFailed("Leasygroup: strona nie wygląda na listę aukcji")
    wynik: list[str] = []
    for link in kontener.css("a.single_row_offer.offer_link"):
        dopasowanie = _ID_Z_URL.match(link.attributes.get("href") or "")
        if dopasowanie is not None:
            wynik.append(dopasowanie.group(1))
    return wynik


def sparsuj_liste(html: str) -> list[SurowaOferta]:
    """Rozpoznaje prawidłową listę i zwraca wyłącznie licytacje."""
    drzewo = HTMLParser(html)
    kontener = drzewo.css_first("div.products_list_rows_container")
    if kontener is None:
        raise ParseFailed("Leasygroup: strona nie wygląda na listę aukcji")

    wynik: list[SurowaOferta] = []
    for kafelek in kontener.css("div.single_row_offer_link_container"):
        link = kafelek.css_first("a.single_row_offer.offer_link")
        if link is None:
            continue
        href = link.attributes.get("href") or ""
        dopasowanie = _ID_Z_URL.match(href)
        if dopasowanie is None:
            continue

        tekst = _tekst(kafelek)
        if not re.search(r"Typ\s+aukcji:\s*Licytacja\b", tekst, re.I):
            continue

        pola: dict[str, str] = {"typ_aukcji": "Licytacja"}
        tytul = _tekst(kafelek.css_first("p.offer_title"))
        if tytul:
            pola["nazwa"] = tytul
        odliczanie = _tekst(kafelek.css_first(".time_label .time"))
        if _ODLICZANIE.match(odliczanie):
            pola["odliczanie"] = odliczanie
        _uzupelnij_skrot_pojazdu(kafelek, pola)
        _pole_ceny(kafelek, pola)
        numer_wewnetrzny = link.attributes.get("data-id")
        if numer_wewnetrzny:
            pola["numer_aukcji"] = numer_wewnetrzny
        wynik.append(
            SurowaOferta(
                external_id=dopasowanie.group(1),
                url=urljoin(BAZOWY_URL, href),
                pola=pola,
            )
        )
    return wynik


def _pary_danych(drzewo: HTMLParser) -> dict[str, str]:
    pola: dict[str, str] = {}
    for wiersz in drzewo.css("div.data_container div.single_data"):
        opisy = wiersz.css("div.description_box p")
        if len(opisy) < 2:
            continue
        etykieta = _tekst(opisy[0]).rstrip(":").strip()
        wartosc = _tekst(opisy[1])
        if etykieta and wartosc:
            pola[etykieta] = wartosc
    numer = drzewo.css_first("div.data_container_top p")
    numer_tekst = _tekst(numer)
    dopasowanie = re.search(r"Numer aukcji:\s*(\d+)", numer_tekst)
    if dopasowanie is not None:
        pola["numer_aukcji"] = dopasowanie.group(1)
    return pola


def _ceny_szczegolow(drzewo: HTMLParser, pola: dict[str, str]) -> None:
    for box in drzewo.css("div.auction_price_container div.single_box"):
        etykieta = _tekst(box.css_first("p"))
        wartosc = _tekst(box.css_first("p.product_price"))
        if not wartosc:
            continue
        if etykieta.startswith("Cena aktualna"):
            klucz = "cena"
        elif etykieta.startswith("Cena wywoławcza"):
            klucz = "cena_wywolawcza"
        else:
            continue
        liczba = re.sub(r"\s*PLN\s*(?:netto|brutto)\s*$", "", wartosc, flags=re.I)
        pola[klucz] = liczba.strip()
        if "netto" in wartosc.lower():
            pola[f"{klucz}_podstawa"] = "netto"
        elif "brutto" in wartosc.lower():
            pola[f"{klucz}_podstawa"] = "brutto"


def _stan_czasu(drzewo: HTMLParser, pola: dict[str, str]) -> None:
    etykieta = _tekst(drzewo.css_first(".time_label"))
    strona = drzewo.text().lower()
    if "zakończona" in etykieta.lower() or "sprzedaż zakończona" in strona:
        pola["zakonczona"] = "true"
        return
    odliczanie = _tekst(drzewo.css_first(".time_label .to_end"))
    if _ODLICZANIE.match(odliczanie):
        pola["odliczanie"] = odliczanie
    elif odliczanie.upper() == "LAST MINUTE":
        pola["last_minute"] = "true"


def _sprzedajacy_i_lokalizacja(drzewo: HTMLParser, pola: dict[str, str]) -> None:
    kontenery = drzewo.css("div.seller_container")
    if kontenery:
        sprzedajacy = _tekst(kontenery[0])
        if sprzedajacy:
            pola["Sprzedający"] = sprzedajacy
    if len(kontenery) > 1:
        lokalizacja = _KONTAKT.sub("", _tekst(kontenery[1])).strip(" ,")
        if lokalizacja:
            pola["Lokalizacja"] = lokalizacja


def sparsuj_szczegoly(html: str, external_id: str, url: str) -> SurowaOferta:
    """Parsuje szczegóły aktywnej lub zakończonej licytacji."""
    drzewo = HTMLParser(html)
    naglowek = drzewo.css_first("h1.product_main_title")
    if naglowek is None or drzewo.css_first("div.data_container") is None:
        raise ParseFailed("Leasygroup: strona nie wygląda na aukcję")

    pola = _pary_danych(drzewo)
    pola["nazwa"] = _tekst(naglowek)
    _ceny_szczegolow(drzewo, pola)
    _stan_czasu(drzewo, pola)
    _sprzedajacy_i_lokalizacja(drzewo, pola)
    return SurowaOferta(external_id=external_id, url=url, pola=pola)


def zdjecia(html: str) -> list[str]:
    """Pełnowymiarowe zdjęcia galerii, bez duplikatów miniaturek."""
    drzewo = HTMLParser(html)
    znalezione: dict[str, None] = {}
    for obraz in drzewo.css("div.slider_for img"):
        src = obraz.attributes.get("src", "")
        if src:
            znalezione[urljoin(BAZOWY_URL, src)] = None
    return list(znalezione)
