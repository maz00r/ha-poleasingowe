"""Parsowanie HTML z aukcje.efl.com.pl.

Czyste funkcje na tekście — bez sieci, bez bazy. Wszystkie selektory pochodzą
z plików w `fixtures/efl/` (SPEC.md §4: „nic nie zmyślaj i nie zgaduj").
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser, Node

from app.application.ports import SurowaOferta
from app.domain.errors import ParseFailed

BAZOWY_URL = "https://aukcje.efl.com.pl"

# Link do aukcji konczy sie numerem: /Auction/<slug>-id435508.
# Slug bywa niespojnie zakodowany (przecinki jako ",c" i jako "%2c" w dwoch
# wariantach tego samego linku), wiec NIE uzywamy go jako klucza.
_ID_Z_URL = re.compile(r"-id(\d+)\s*$")

# "Do zakonczenia: 22 godz. 07.09.2026 godzina 14:37:00" — obok zgrubnego
# opisu stoi absolutny znacznik, i to jego uzywamy (RECON.md §4.1).
_KONIEC = re.compile(
    r"Do zakończenia:.{0,400}?(\d{2}\.\d{2}\.\d{4})\s*godzina\s*(\d{2}:\d{2}:\d{2})",
    re.S,
)
_OFERT = re.compile(r"Ofert:\s*(\d+)")
_CENA = re.compile(r"Aktualna cena[^:]*:\s*([\d\s\xa0]+,\d{2})\s*zł")


def _pary_z_tabeli(korzen: Node) -> dict[str, str]:
    """Wyciąga pary etykieta→wartość z `table.simple-table`.

    Serwis używa dwóch wariantów tej samej tabeli: na liście etykieta siedzi
    w `<label>`, w szczegółach jest zwykłym tekstem zakończonym dwukropkiem.
    Obsługujemy oba, bo to ten sam serwis i ta sama tabela.
    """
    pola: dict[str, str] = {}
    komorki = korzen.css("table.simple-table td")
    for i, td in enumerate(komorki):
        if i + 1 >= len(komorki):
            break
        etykieta = td.css_first("label")
        tekst = (etykieta or td).text(strip=True)
        if not tekst.endswith(":"):
            continue
        wartosc = komorki[i + 1].text(strip=True)
        if wartosc and wartosc != "\xa0":
            pola[tekst.rstrip(":").strip()] = wartosc
    return pola


def sparsuj_liste(html: str) -> list[SurowaOferta]:
    """Zwraca pozycje z jednej strony listy aukcji."""
    drzewo = HTMLParser(html)
    oferty: list[SurowaOferta] = []

    for kafelek in drzewo.css("div.OfferList"):
        link = kafelek.css_first("a.title")
        if link is None:
            continue
        href = link.attributes.get("href") or ""
        dopasowanie = _ID_Z_URL.search(href)
        if dopasowanie is None:
            continue

        pola = _pary_z_tabeli(kafelek)
        naglowek = link.css_first("h3")
        if naglowek is not None:
            pola["tytul"] = " ".join(naglowek.text(strip=True).split())
        for selektor, klucz in (
            ("div.price", "cena"),
            ("div.to-end", "do_konca"),
            ("div.auction-type", "format_oferty"),
        ):
            wezel = kafelek.css_first(selektor)
            if wezel is not None:
                pola[klucz] = wezel.text(strip=True)

        oferty.append(
            SurowaOferta(
                external_id=dopasowanie.group(1),
                url=f"{BAZOWY_URL}{href}",
                pola=pola,
            )
        )
    return oferty


def sparsuj_szczegoly(html: str, external_id: str, url: str) -> SurowaOferta:
    """Zwraca komplet pól ze strony pojedynczej aukcji."""
    drzewo = HTMLParser(html)
    produkt = drzewo.css_first("div.product")
    if produkt is None:
        raise ParseFailed(
            f"EFL {external_id}: brak `div.product` — strona nie wygląda na "
            "aukcję albo zmienił się szablon"
        )

    pola = _pary_z_tabeli(produkt)
    nazwa = produkt.css_first("div.product-name")
    if nazwa is not None:
        pola["tytul"] = " ".join(nazwa.text(strip=True).split())

    tekst = drzewo.text()

    koniec = _KONIEC.search(tekst)
    if koniec is not None:
        pola["koniec"] = f"{koniec.group(1)} {koniec.group(2)}"

    ofert = _OFERT.search(tekst)
    if ofert is not None:
        pola["liczba_ofert"] = ofert.group(1)

    cena = _CENA.search(tekst)
    if cena is not None:
        pola["cena"] = cena.group(1).strip()

    # Serwis ujawnia sam FAKT nieosiagniecia ceny rezerwowej, nie jej wysokosc
    # (regulamin EFL §4 ust. 11). Zapisujemy obecnosc komunikatu, a nie wniosek:
    # jego brak moze znaczyc "cena minimalna osiagnieta" ALBO "nie ustalono jej
    # wcale", a tych dwoch przypadkow nie da sie tu rozroznic.
    if "Cena minimalna nie została osiągnięta" in tekst:
        pola["cena_minimalna_nieosiagnieta"] = "True"

    return SurowaOferta(external_id=external_id, url=url, pola=pola)


def sparsuj_oferty(html: str) -> list[dict[str, str]]:
    """Historia ofert z zakładki „Oferty" (RECON.md §4.1).

    Panel jest **inline w HTML i nie wymaga logowania**, więc dla tego źródła
    kompletność historii ofert z SPEC.md §11.8 jest dana wprost — `bid_gap`
    nie musi być zgadywany z różnic snapshotów.
    """
    drzewo = HTMLParser(html)
    panel = drzewo.css_first('div[data-tabs="bidders"]')
    if panel is None:
        return []

    oferty: list[dict[str, str]] = []
    for wiersz in panel.css("table.data-table tr"):
        komorki = [td.text(strip=True) for td in wiersz.css("td")]
        if len(komorki) != 3:
            continue  # wiersz naglowka ma <th>, nie <td>
        oferty.append({"kod": komorki[0], "kwota": komorki[1], "data": komorki[2]})
    return oferty


def numery_stron(html: str) -> list[int]:
    """Numery stron widoczne w paginatorze. `page` jest liczony **od zera**."""
    # W surowym HTML separatory sa zakodowane jako `&amp;`, wiec bezposrednio
    # przed `page=` stoi srednik, a nie ampersand. Bez uwzglednienia tego
    # funkcja zwracala pusta liste mimo obecnych linkow paginacji.
    return sorted({int(m) for m in re.findall(r"[?&;]page=(\d+)", html)})
