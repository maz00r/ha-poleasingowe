"""Parsowanie HTML z dawro.pl (RECON.md §4.5).

Kilka rzeczy, których nie widać w EFL/autoprzetarg/leasygroup/mleasing:

- **Serwis nie podaje paliwa, skrzyni ani nadwozia** w żadnym polu — ani na
  liście, ani w szczegółach (24/24 próbek bez tych pól, `fixtures/dawro/
  raport.md`). Adapter ich nie zgaduje z nazwy: `sources/paliwa.py`
  normalizuje wyłącznie to, co serwis **podał**, a zgadywanie paliwa
  z modelu byłoby wymyślaniem faktu, nie jego odczytem.

- **Podstawa VAT jest `UNKNOWN` w każdej zmierzonej aukcji** — etykieta to
  zawsze „Cena wywoławcza", nigdy z dopiskiem „netto"/„brutto". W przeciwieństwie
  do Leasygroup i mLeasing (które *znają* podstawę per pozycja i przeliczają
  brutto→netto) adapter dawro **nie przelicza VAT wcale** i zapisuje kwotę
  taką, jaką podał serwis — tak samo jak EFL, autoprzetarg i poleasingowe.pl,
  które też nigdy nie ustalają podstawy. Zgadnięcie „to pewnie brutto" albo
  „to pewnie netto" byłoby założeniem bez dowodu (RECON.md §4.5, akapit
  „Cena — brak podstawy brutto/netto").

- **Najwyższa oferta na stronie szczegółów jest zawsze placeholderem JS**
  (`id="najwyzsza-oferta"` → „pobieranie danych..."), dociąganym AJAX-em
  przez `POST /WebService/PasekInformacyjny/`. Ten endpoint nigdy nie był
  wywołany — ani przez rekonesans, ani przez ten adapter (RECON.md §4.5,
  „Do ustalenia w planie adaptera"): jego kontrakt jest znany tylko z obserwacji
  JS-u strony, nie ze zmierzonego wywołania, i zgadnięcie formatu żądania
  byłoby ryzykiem wstrzyknięcia złych danych. Serwerowe źródło bieżącej
  najwyższej oferty jest więc WYŁĄCZNIE kafelek listy (`div.najwyzsza-oferta`,
  atrybut `kwota` na `div.w.najwyzsza-oferta-kwota`) — `sparsuj_szczegoly`
  go nie zna.

- **Zakończenie jest jawnym, serwerowym tekstem**, nie zgadywaniem: przycisk
  `a.przycisk-przystap` („PRZYSTĄP DO AUKCJI") zamienia się na
  `a.przycisk-licytuj` z tekstem „AUKCJA ZAKOŃCZONA" — zmierzone identycznie
  we wszystkich 32 próbkach domknięcia (`fixtures/dawro/domkniecie-*.html`).
  Kontener `pasek-informacyjny-licytacji` NIE znika (strona wciąż zwraca
  200), ale cały blok `#kwoty` (cena wywoławcza + placeholder oferty)
  przepada razem z przyciskiem.

Kodowanie: nagłówek HTTP deklaruje `iso-8859-1`, treść jest UTF-8
(RECON.md §4.5) — funkcje tutaj dostają już zdekodowany `str`, ale
`source.py` MUSI dekodować `.content` jako `utf-8`, nigdy `.text`.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

from app.application.ports import SurowaOferta
from app.domain.errors import ParseFailed

BAZOWY_URL = "https://www.dawro.pl"
SCIEZKA_LISTY = (
    "/aukcje/sortuj,data-zakonczenia,kierunek,rosnaco,strona,{},ilosc,100,"
    "wyswietlanie,boxy"
)

_KONTENER_STRONY = "div#tresc-strony"
_WIDGET_LISTY = "div.wyswietlanie"
"""Przełącznik widoku boxy/lista — jest na liście, nie ma go na landingu."""
_KONTENER_SZCZEGOLOW = 'class="pasek-informacyjny-licytacji"'
_MARKER_KONCA = "POLECANE AUKCJE"
_MARKER_ZAKONCZENIA = "AUKCJA ZAKOŃCZONA"

# Slug jest zmienny (człon SEO) i nie ma prawa decydować, czy kafelek
# trafi do wyniku — pewny jest tylko numeryczny `<id>` przed przecinkiem.
_ID_ZE_SCIEZKI = re.compile(r"^/aukcja/(\d+),([^/]+)$")
_TABLICA_W_NAZWIE = re.compile(r",\s*[A-Z]{2,3}[0-9A-Z]{4,5}\s*$")
_TS_ODLICZANIA = re.compile(r"Zegar\.odliczanie\((\d{9,})\)")
_KWOTA = re.compile(r"([\d\s\xa0]+,\d{2}|\d[\d\s\xa0]*\d)")


def _tekst(wezel: Node | None) -> str:
    if wezel is None:
        return ""
    return " ".join(wezel.text(separator=" ", strip=True).split())


def _kwota_z_tekstu(tekst: str | None) -> str | None:
    """Surowa kwota bez waluty; `None`, gdy pola nie ma albo jest puste (`&nbsp;`)."""
    if not tekst:
        return None
    dopasowanie = _KWOTA.search(tekst)
    if dopasowanie is None:
        return None
    return re.sub(r"[\s\xa0]+", " ", dopasowanie.group(1)).strip()


def _pary(korzen: HTMLParser | Node, klasa_wartosci: str) -> dict[str, str]:
    """Etykieta→wartość z kolejnych par `.k` / `.<klasa_wartosci>`.

    dawro nie zawija par w osobny kontener — to płaska sekwencja
    `div.k`, `div.w` (lista) albo `div.fl.k`, `div.fl.v` (szczegóły),
    czasem z pustymi placeholderami (`&nbsp;`) między nimi, które nie mają
    własnych węzłów `.k`/`.w` i więc nie rozjeżdżają parowania — KAŻDY `.k`
    ma dokładnie jeden odpowiadający mu `.<klasa_wartosci>` (zmierzone we
    wszystkich fixtures).

    Selektor złożony `.css(".k, .w")` **nie zachowuje kolejności dokumentu**:
    selectolax grupuje wyniki po selektorze, nie po pozycji w drzewie
    (zmierzone — wszystkie `.k` przed wszystkimi `.w`), więc parowanie
    sekwencyjne po jednym przebiegu dawało przesunięte klucz-wartość.
    Dwa osobne zapytania, każde stabilne we własnej kolejności, i `zip`
    naprawiają to bez zgadywania struktury.
    """
    return {
        _tekst(k).rstrip(":").strip().lower(): _tekst(w)
        for k, w in zip(
            korzen.css(".k"), korzen.css(f".{klasa_wartosci}"), strict=False
        )
    }


def _ts_odliczania(tekst: str) -> str | None:
    """Surowy uniksowy znacznik z `Zegar.odliczanie(<unix>)`.

    Stoi w skrypcie PO `POLECANE AUKCJE`, więc czyta się z całego tekstu
    strony, nie z `_blok_glowny`. Interpretacja (strefa, `int`) należy do
    `mapper.py` — parser tylko wyciąga surowy napis (SPEC.md §6.2).
    """
    dopasowanie = _TS_ODLICZANIA.search(tekst)
    return dopasowanie.group(1) if dopasowanie is not None else None


def _najwyzsza_oferta_z_kafelka(kafelek: Node) -> str | None:
    """Atrybut `kwota` na `div.w.najwyzsza-oferta-kwota` — surowy `Decimal`.

    Jedyne serwerowe źródło bieżącej najwyższej oferty (RECON.md §4.5):
    szczegóły dociągają ją AJAX-em i nigdy nie renderują serwerowo.
    """
    wezel = kafelek.css_first("div.najwyzsza-oferta .w")
    if wezel is None:
        return None
    atrybut = wezel.attributes.get("kwota")
    if atrybut:
        return atrybut.strip()
    return _kwota_z_tekstu(_tekst(wezel))


def sparsuj_liste(html: str) -> list[SurowaOferta]:
    """Pozycje z jednej strony listy.

    Rzuca `ParseFailed` w dwóch sytuacjach, obie celowo głośne:

    - brak kontenera strony — to nie jest strona dawro (WAF, błąd);
    - są kafelki ALBO przełącznik widoku `div.wyswietlanie` → to lista;
      brak obu naraz to landing `/aukcje` z kuratorowanymi kolumnami, na
      który serwis odsyła, gdy adres listy przestaje działać (RECON.md
      §4.5). Gdyby landing uchodził za „pustą, kompletną listę", dwa
      kolejne przemiaty oznaczyłyby KAŻDĄ aukcję dawro jako znikniętą.
      Przemiat `FAILED` niczego nie kasuje i jest widoczny w diagnostyce.
    """
    drzewo = HTMLParser(html)
    if drzewo.css_first(_KONTENER_STRONY) is None:
        raise ParseFailed("dawro: strona nie wygląda na listę aukcji")
    kafelki = drzewo.css("div.fl.aukcja-box")
    if not kafelki and drzewo.css_first(_WIDGET_LISTY) is None:
        raise ParseFailed(
            "dawro: brak kafelków i przełącznika widoku — to landing, nie lista"
        )

    wynik: list[SurowaOferta] = []
    for kafelek in kafelki:
        link = kafelek.css_first("a[href]")
        if link is None:
            continue
        sciezka = urlsplit(link.attributes.get("href") or "").path.rstrip("/")
        dopasowanie = _ID_ZE_SCIEZKI.match(sciezka)
        if dopasowanie is None:
            continue
        external_id, slug = dopasowanie.groups()

        # `title` niesie czystą nazwę; tekst nagłówka ma doklejony numer
        # rejestracyjny („Hummer H2, JE619DS") — ten jest osobno w szczegółach.
        naglowek = kafelek.css_first("h2.nazwa")
        nazwa = (naglowek.attributes.get("title") or "").strip() if naglowek else ""
        if not nazwa:
            nazwa = _TABLICA_W_NAZWIE.sub("", _tekst(naglowek)).strip()

        parametry = kafelek.css_first("div.parametry") or kafelek
        pary = _pary(parametry, "w")

        pola: dict[str, str] = {"nazwa": nazwa}
        koniec_tekst = _tekst(kafelek.css_first("div.koniec b"))
        if koniec_tekst:
            pola["koniec"] = koniec_tekst
        if wartosc := pary.get("cena wywoławcza"):
            pola["cena_wywolawcza"] = wartosc
        if wartosc := pary.get("rok produkcji"):
            pola["Rok produkcji"] = wartosc
        if wartosc := pary.get("przebieg"):
            pola["Przebieg"] = wartosc
        if wartosc := pary.get("forma sprzedaży"):
            pola["Forma sprzedaży"] = wartosc
        if wartosc := pary.get("sprzedający"):
            pola["Sprzedający"] = wartosc
        najwyzsza = _najwyzsza_oferta_z_kafelka(kafelek)
        if najwyzsza is not None:
            pola["najwyzsza_oferta"] = najwyzsza

        wynik.append(
            SurowaOferta(
                external_id=external_id,
                url=f"{BAZOWY_URL}/aukcja/{external_id},{slug}",
                pola=pola,
            )
        )
    return wynik


def _blok_glowny(html: str) -> str:
    """Wycina blok aukcji, odcinając rekomendacje przed i po nim.

    dawro ma DWA różne widgety z tą samą klasą `div.opis`: karuzelę na
    górze strony (przed `pasek-informacyjny-licytacji`) i sekcję
    „POLECANE AUKCJE" na dole. Bez tego cięcia opis firmy myli się z opisem
    przypadkowej rekomendowanej aukcji (obie mają identyczną klasę).
    """
    poczatek = html.find(_KONTENER_SZCZEGOLOW)
    if poczatek < 0:
        raise ParseFailed("dawro: strona nie wygląda na aukcję")
    koniec = html.find(_MARKER_KONCA, poczatek)
    return html[poczatek : koniec if koniec > 0 else len(html)]


def odcisk_aukcji(html: str) -> str:
    """Odcisk treści istotnej dla aukcji — do taniego odpytu (SPEC.md §11.3).

    Sam blok główny NIE wystarcza: kończy się na `Zegar.odliczanie(<unix>)`,
    który stoi w skrypcie PO `POLECANE AUKCJE` (poza blokiem). Dogrywki tu
    nie ma (RECON.md §4.5, pkt „a"), ale odcisk i tak liczy się z obu, żeby
    nie zakładać tego na zapas jako niezmiennika.
    """
    blok = _blok_glowny(html)
    return f"{blok}|{_ts_odliczania(html) or ''}"


def sparsuj_szczegoly(html: str, external_id: str, url: str) -> SurowaOferta:
    """Parsuje szczegóły aktywnej lub zakończonej aukcji."""
    blok = _blok_glowny(html)
    drzewo = HTMLParser(blok)

    pola = _pary(drzewo, "v")

    nazwa_pojazdu: dict[str, str] = {}
    if wartosc := pola.get("opis modelu"):
        nazwa_pojazdu["nazwa"] = wartosc
    if wartosc := pola.get("vin"):
        nazwa_pojazdu["VIN"] = wartosc
    if wartosc := pola.get("rok produkcji"):
        nazwa_pojazdu["Rok produkcji"] = wartosc
    if wartosc := pola.get("przebieg"):
        nazwa_pojazdu["Przebieg"] = wartosc
    if wartosc := pola.get("pojemność"):
        nazwa_pojazdu["Pojemność"] = wartosc
    if wartosc := pola.get("moc"):
        nazwa_pojazdu["Moc"] = wartosc
    if wartosc := pola.get("sprzedawca"):
        nazwa_pojazdu["Sprzedawca"] = wartosc
    if wartosc := pola.get("forma sprzedaży"):
        nazwa_pojazdu["Forma sprzedaży"] = wartosc

    kwoty = drzewo.css_first("div#kwoty")
    if kwoty is not None:
        etykiety = kwoty.css("div.fl")
        for i in range(0, len(etykiety) - 1, 2):
            if "wywo" in _tekst(etykiety[i]).lower():
                cena = _tekst(etykiety[i + 1])
                if cena:
                    nazwa_pojazdu["cena_wywolawcza"] = cena

    parking = drzewo.css_first("div.parking-informacje div.adres")
    if parking is not None:
        lokalizacja = " ".join(_tekst(parking).split())
        if lokalizacja:
            nazwa_pojazdu["Lokalizacja"] = lokalizacja

    # Zakotwiczone w elemencie, nie w gołym podciągu: regulamin aukcji jest
    # wstawiany inline w tym samym bloku i mógłby kiedyś zawierać te słowa.
    przycisk = drzewo.css_first("a.przycisk-licytuj")
    zamknieta = przycisk is not None and _MARKER_ZAKONCZENIA in _tekst(przycisk).upper()
    nazwa_pojazdu["zamknieta"] = "1" if zamknieta else "0"

    ts = _ts_odliczania(html)
    if ts is not None:
        nazwa_pojazdu["koniec_ts"] = ts

    return SurowaOferta(external_id=external_id, url=url, pola=nazwa_pojazdu)


def zdjecia(html: str) -> list[str]:
    """Pełnowymiarowe zdjęcia galerii, bez duplikatów klonowanych przez bxSlider."""
    poczatek = html.find('id="zdjecie-male"')
    if poczatek < 0:
        return []
    koniec = html.find(_MARKER_KONCA, poczatek)
    fragment = html[poczatek : koniec if koniec > 0 else len(html)]
    drzewo = HTMLParser(fragment)
    znalezione: dict[str, None] = {}
    for kotwica in drzewo.css("a.jackbox"):
        href = kotwica.attributes.get("href")
        if href:
            znalezione[urljoin(BAZOWY_URL, href)] = None
    return list(znalezione)
