"""Parsowanie HTML z poleasingowe.pl.

Czyste funkcje na tekście — bez sieci, bez bazy. Wszystkie selektory pochodzą
z plików w `fixtures/poleasingowe/` (SPEC.md §4).

Serwis renderuje etykietę „Aktualna cena" pustą i dopełnia ją JS-em, ale
**komplet danych stoi serwerowo w HTML**, w obiekcie inicjalizującym
Alpine.js. Stamtąd bierzemy cenę, liczbę ofert, minimalne postąpienie
i — najważniejsze — absolutną datę końca z jawną strefą (RECON.md §4.2).
Parsowanie widocznego tekstu dawałoby „16 godzin", czyli zaokrąglenie
bezużyteczne w końcówce aukcji.

**Pole `winner` jest tu celowo wycinane.** Blok Alpine zawiera pełny,
niezamaskowany login zwycięzcy obok zamaskowanego `winner_formated`. To dane
osobowe **osoby trzeciej**, których nie chroni żadne hasło i które nie mają
prawa trafić do `raw_json` ani do zrzutów (SPEC.md §10.2). Wycinamy je
u źródła, a nie licząc na filtr redakcji dalej w łańcuchu.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser, Node

from app.application.ports import SurowaOferta
from app.domain.errors import ParseFailed

BAZOWY_URL = "https://poleasingowe.pl"
SCIEZKA_LISTY = "/pl/auctions/list/pub/all/vehicles"

# `external_id` to ostatni segment adresu: /pl/auctions/details/<slug>/<id>.
# Alfanumeryczny, 8 znaków — NIE liczba (RECON.md §4.2).
_ID_Z_URL = re.compile(r"/auctions/details/[^/]+/([A-Za-z0-9]+)\s*$")

# Pola z bloku inicjalizującego Alpine.js. Każde osobno i wąsko, bo to jest
# kod strony, a nie format danych — szeroki wzorzec złapałby sąsiedni skrypt.
_CENA = re.compile(r"current_price:\s*'([^']*)'")
_OFERT = re.compile(r"offers_count:\s*(\d+)")
_LICYTUJACYCH = re.compile(r"bidders_count:\s*(\d+)")
_POSTAPIENIE = re.compile(r"instep_price:\s*([\d.]+)")
_MIN_OFERTA = re.compile(r"min_offer_price:\s*([\d.]+)")
_TRWA = re.compile(r"auction_pending:\s*(true|false)")
# `endDateTimer` stoi w bloku TUŻ PRZED `endDate`, więc dwukropek zaraz po
# nazwie jest tu jedynym, co je rozróżnia.
_KONIEC = re.compile(r"\bendDate:\s*moment\('([^']+)'\)")

_KAFELEK = "div.auction-element"

# Blok `auction: { ... }` z inicjalizacji Alpine — od nazwy pola po ostatnie
# pole obiektu. To jedyna część strony, która opisuje AUKCJĘ; reszta zmienia
# się przy każdym żądaniu (patrz `wytnij_blok_aukcji`).
_BLOK_AUKCJI = re.compile(r"auction:\s*\{.*?localTimerEnded[^,}]*,?\s*\}", re.S)


def wytnij_blok_aukcji(html: str) -> str | None:
    """Zwraca fragment opisujący aukcję — do policzenia `content_hash`.

    **Zmierzone 2026-09-08:** hash całej strony jest bezużyteczny, bo trzy
    kolejne żądania dają trzy różne treści. Zmienia się token CSRF (w `<meta>`,
    w zmiennej `csrfToken` i w ukrytym polu formularza kontaktowego) oraz
    karuzela poleceń na dole strony. Hash samego bloku aukcji był w tych
    samych trzech żądaniach **identyczny**.

    Hashujemy więc dokładnie to, co parsujemy. Wycięcie jest jednym
    przebiegiem wyrażenia regularnego po tekście — tanim wobec zbudowania
    drzewa DOM ze strony ważącej 200 kB, którą §11.3 każe nam pominąć.
    """
    trafienie = _BLOK_AUKCJI.search(html)
    return trafienie.group(0) if trafienie else None


def _tekst(wezel: Node | None) -> str:
    return wezel.text(strip=True) if wezel is not None else ""


def _dopasuj(wzorzec: re.Pattern[str], tekst: str) -> str | None:
    trafienie = wzorzec.search(tekst)
    return trafienie.group(1) if trafienie else None


def id_z_url(url: str) -> str | None:
    trafienie = _ID_Z_URL.search(url.strip())
    return trafienie.group(1) if trafienie else None


def sparsuj_liste(html: str) -> list[SurowaOferta]:
    """Pozycje z jednej strony listy pojazdów.

    Lista podaje **datę końca bez godziny** („19 godzin (2026-09-07)"), więc
    przenosimy ją jako tekst i nie udajemy, że mamy dokładny termin.
    Dokładny `endDate` przychodzi dopiero ze strony szczegółów.
    """
    drzewo = HTMLParser(html)
    oferty: list[SurowaOferta] = []
    widziane: set[str] = set()

    for kafelek in drzewo.css(_KAFELEK):
        link = kafelek.css_first("a[href*='/auctions/details/']")
        if link is None:
            continue
        url = (link.attributes.get("href") or "").strip()
        external_id = id_z_url(url)
        if external_id is None or external_id in widziane:
            continue
        widziane.add(external_id)

        pola: dict[str, str] = {}
        naglowek = kafelek.css_first("h2 span")
        if naglowek is not None:
            pola["nazwa"] = _tekst(naglowek)

        # Etykiety nad kafelkiem: rocznik, paliwo, przebieg — w tej kolejności,
        # ale bez nazw, więc rozpoznajemy je po kształcie wartości.
        for etykieta in kafelek.css("div.filter-labels div.filter-label"):
            wartosc = _tekst(etykieta)
            if re.fullmatch(r"\d{4}", wartosc):
                pola.setdefault("rocznik", wartosc)
            elif wartosc.lower().endswith("km"):
                pola.setdefault("przebieg", wartosc)
            elif wartosc:
                pola.setdefault("paliwo", wartosc)

        for wiersz in kafelek.css("div.listing-box-line, span.listing-box-line"):
            nazwa = _tekst(wiersz.css_first("div.line-title")).rstrip(":").strip()
            wartosc = _tekst(wiersz.css_first("div.line-value"))
            if nazwa and wartosc:
                pola[nazwa] = wartosc

        cena = kafelek.css_first("span.listing-price")
        if cena is not None:
            # Wewnątrz ceny siedzi jeszcze `<span>PLN</span>` — bierzemy sam
            # tekst węzła, żeby waluta nie skleiła się z kwotą.
            pola["cena"] = re.sub(r"\s*PLN\s*$", "", _tekst(cena)).strip()

        czas = kafelek.css_first("span.listing-box-time")
        if czas is not None:
            pola["do_konca_tekst"] = re.sub(r"\s+", " ", _tekst(czas)).strip()

        oferty.append(SurowaOferta(external_id=external_id, url=url, pola=pola))
    return oferty


def numery_stron(html: str) -> list[int]:
    """Numery stron z paginacji. Serwis liczy od 1 (RECON.md §4.2)."""
    drzewo = HTMLParser(html)
    numery: set[int] = set()
    for link in drzewo.css("a[href*='page=']"):
        # W HTML-u `&` bywa zapisane jako `&amp;`, więc przed `page=`
        # stoi wtedy średnik, nie ampersand.
        trafienie = re.search(r"[?&;]page=(\d+)", link.attributes.get("href") or "")
        if trafienie:
            numery.add(int(trafienie.group(1)))
    return sorted(numery)


def sparsuj_szczegoly(html: str, external_id: str, url: str) -> SurowaOferta:
    """Szczegóły jednej aukcji: blok Alpine plus tabela „Dane podstawowe"."""
    if _KONIEC.search(html) is None and _CENA.search(html) is None:
        raise ParseFailed(
            f"poleasingowe: brak bloku danych aukcji na stronie {external_id} — "
            "serwis zmienił układ albo to nie jest strona aukcji"
        )

    pola: dict[str, str] = {}
    for nazwa, wzorzec in (
        ("cena", _CENA),
        ("offers_count", _OFERT),
        ("bidders_count", _LICYTUJACYCH),
        ("instep_price", _POSTAPIENIE),
        ("min_offer_price", _MIN_OFERTA),
        ("auction_pending", _TRWA),
        ("end_date", _KONIEC),
    ):
        wartosc = _dopasuj(wzorzec, html)
        if wartosc is not None:
            pola[nazwa] = wartosc

    drzewo = HTMLParser(html)
    tytul = drzewo.css_first("title")
    if tytul is not None:
        # „VOLKSWAGEN GOLF 2022 KOMBI Aukcja: 1384/STR/AU/2026" — numer aukcji
        # doklejony na końcu nie jest częścią nazwy pojazdu.
        pola["nazwa"] = re.sub(r"\s*Aukcja:.*$", "", _tekst(tytul)).strip()

    for element in drzewo.css("div.auction-data-item"):
        nazwa = _tekst(element.css_first("div.auction-data-label"))
        wartosc = _tekst(element.css_first("div.auction-data-value"))
        if nazwa and wartosc:
            pola[nazwa] = wartosc

    # `winner` NIE trafia do `pola` — patrz docstring modułu.
    return SurowaOferta(external_id=external_id, url=url, pola=pola)


# Galeria: `https://poleasingowe.pl/images/sgallery_<uuid>_127.png`.
# `sgallery_` odróżnia zdjęcia pojazdu od logotypów i ikon serwisu.
_ZDJECIA = re.compile(
    r'https://[^"\']+/images/sgallery_[^"\']+\.(?:jpe?g|png|webp)', re.I
)


def zdjecia(html: str) -> list[str]:
    """Adresy zdjęć pojazdu ze strony szczegółów.

    Nie zapisujemy ich w bazie — pobieramy w razie potrzeby, gdy ktoś otworzy
    kartę aukcji (SPEC.md §12: proxy z cache na dysku, nigdy hotlink).
    """
    return list(dict.fromkeys(_ZDJECIA.findall(html)))
