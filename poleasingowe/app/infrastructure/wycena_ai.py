"""Wycena pojazdu przez model językowy, z lokalnym cache'em.

Model dostaje wyłącznie techniczne dane pojazdu i zagregowane ceny z naszej
bazy. Nie wysyłamy VIN-u, identyfikatora aukcji ani danych sprzedającego.
Wynik jest pomocą przy analizie, nie opinią rzeczoznawcy.

**Dostawca jest wyborem użytkownika** (`ai_provider`): OpenAI, Anthropic albo
dowolny endpoint zgodny z OpenAI — z modelem lokalnym włącznie. Ten plik
o tym wyborze nie wie nic poza tym, że dostaje `KlientModelu`: prompt, cache
i walidacja wyniku są wspólne, a różnice protokołów siedzą w
`ai_klienci.py`. Dzięki temu odcisk cache'u zależy od modelu, nie od
dostawcy — zmiana dostawcy przy tym samym modelu nie unieważnia wycen.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, replace
from decimal import Decimal
from typing import Any

import httpx

from app.application.read_models import (
    PewnoscPowiazania,
    PorownanieRynkowe,
    PowiazaneWystawienie,
    Szczegoly,
)
from app.domain.entities import OgloszenieRynkowe, WycenaAukcji
from app.domain.enums import Currency
from app.domain.value_objects import Money
from app.infrastructure.ai_klienci import BladModelu, BladOdpowiedzi, KlientModelu
from app.infrastructure.ekspertyza import OcenionyDokument
from app.infrastructure.otomoto import Ogloszenie, PojazdDoPorownania, WynikOtomoto

log = logging.getLogger(__name__)

WERSJA_PROMPTU = 13
"""Podbij, gdy zmienisz `INSTRUKCJE` albo `SCHEMAT`.

Zapisana wycena niesie wersję, którą policzono — dzięki temu widać, że
powstała starym poleceniem, i można ją świadomie przeliczyć.

2: doszedł szacunek poziomu cen ofertowych na polskich portalach.
3: opcjonalne ogłoszenia z Otomoto (Apify) i treść ekspertyzy.
4: wszystkie kwoty w PLN netto; ceny Otomoto przeliczone z brutto, bez aut
   z USA i skrajnych ofert.
5: Otomoto bez filtra przebiegu, jawna korekta za przebieg przez model.
6: własny odczyt Otomoto — netto wg odznak przy każdym ogłoszeniu; osobna
   instrukcja bazowa dla wyceny z danymi rynkowymi (bez sprzecznych zdań).
7: netto wg odznak i opisu (kwota Otomoto zawsze brutto).
8: wszystkie kwoty Otomoto ÷1,23; status faktury osobno przy ogłoszeniu.
9: wcześniejsze, niesprzedane wystawienia tego auta jako górna granica.
10: uzasadnienie w punktach „- Temat: treść" (czytelniejsze na karcie).
11: każde wcześniejsze wystawienie = niesprzedane, bez względu na status.
12: uzasadnienie jako tablica punktów (linie w napisie psuły JSON).
13: silnik wg rynku wtórnego — renoma, awaryjność, koszty, popyt."""

MAKS_OGLOSZEN_NA_KARCIE = 15

NAZWA_SCHEMATU = "wycena_pojazdu"

INSTRUKCJE = (
    "Jesteś analitykiem polskiego rynku samochodów używanych. "
    "Oszacuj uczciwą cenę detaliczną pojazdu w PLN na dziś. "
    "WSZYSTKIE kwoty w danych i w wyniku są w PLN NETTO (bez VAT). "
    "Największą wagę nadaj porównaniom z zakończonych aukcji, "
    "ale uwzględnij różnice rocznika i przebiegu. Jeśli danych "
    "jest mało, poszerz przedział i obniż pewność. Nie zakładaj "
    "idealnego stanu technicznego; jasno wypisz założenia.\n\n"
    # Poziom cen na portalach ogłoszeniowych — z WIEDZY modelu, nie z sieci.
    # Reguła „nie zmyślaj ogłoszeń" jest tu najważniejszym zdaniem: bez niej
    # model dopisuje konkretne oferty z cenami i linkami, których nikt nigdy
    # nie widział, a wyglądają jak dowód.
    "W polu `cena_portale` podaj orientacyjny poziom cen OFERTOWYCH "
    "podobnego auta na polskich portalach ogłoszeniowych (OtoMoto, OLX). "
    "NIE MASZ dostępu do internetu — to szacunek z Twojej wiedzy o rynku, "
    "więc nie podawaj konkretnych ogłoszeń, linków, nazw sprzedających ani "
    "liczby ofert. Jeśli nie potrafisz tego rozsądnie oszacować, wpisz null. "
    "Pamiętaj, że ceny ofertowe na portalach są zwykle wyższe od cen "
    "uzyskiwanych na aukcjach poleasingowych; w uzasadnieniu napisz krótko, "
    "jak duża jest ta różnica dla tego auta."
)

INSTRUKCJE_OTOMOTO = (
    # Osobna instrukcja, nie dopisek: bazowa mówi „nie masz internetu,
    # `cena_portale` to szacunek" i „największą wagę aukcjom" — przy danych
    # z Otomoto to były dwa sprzeczne polecenia naraz.
    "Jesteś analitykiem polskiego rynku samochodów używanych. Oszacuj "
    "uczciwą cenę detaliczną pojazdu w PLN na dziś. WSZYSTKIE kwoty w danych "
    "i w wyniku są w PLN NETTO (bez VAT). Nie zakładaj idealnego stanu "
    "technicznego; jasno wypisz założenia. Jeśli danych jest mało, poszerz "
    "przedział i obniż pewność.\n\n"
    "Dostajesz PRAWDZIWE dane rynkowe — opieraj się na nich:\n"
    "- `ogloszenia_otomoto`: porównywalne ogłoszenia z Otomoto pobrane dziś "
    "(ten sam model wg taksonomii Otomoto, rocznik ±1, bez uszkodzonych, bez "
    "aut z USA, bez cesji leasingu i skrajnych kwot). Cena każdego ogłoszenia "
    "jest już NETTO: KAŻDĄ kwotę z ogłoszenia podzielono przez 1,23, "
    "niezależnie od faktury. Pole `faktura` mówi, jak sprzedaje sprzedawca: "
    "`faktura VAT 23%` (odznaka Otomoto), `faktura VAT 23% (wg opisu)`, "
    "`VAT-marża` albo `brak informacji` (zwykle prywatny, bez faktury). "
    "Uwzględnij to w ocenie ceny: przy VAT-marży i bez faktury kupujący "
    "firmowy nie odliczy VAT, więc takie oferty po ÷1,23 wyglądają taniej, "
    "niż kosztują firmę — nie przeliczaj kwot drugi raz, tylko to oceń "
    "i opisz w uzasadnieniu. To ceny "
    "OFERTOWE — zwykle wyższe od uzyskiwanych na aukcji poleasingowej; "
    "napisz, jak dużą różnicę przyjmujesz. Porównania NIE są filtrowane po "
    "przebiegu — KAŻDE ogłoszenie skoryguj o różnicę przebiegu (oraz "
    "rocznika i wersji) względem wycenianego auta; nie opieraj się na samej "
    "medianie i podaj w uzasadnieniu średnią korektę za przebieg. Pole "
    "`cena_portale` wypełnij medianą z tych danych.\n"
    "- `porownania_z_zakonczonych_aukcji`: wyniki aukcji poleasingowych "
    "z naszej bazy — punkt odniesienia dla różnicy oferta/aukcja.\n"
    "- `ekspertyza`: tekst raportu rzeczoznawcy dla TEGO pojazdu (dane "
    "osobowe i identyfikatory zasłonięte). Uwzględnij opisany stan "
    "techniczny, uszkodzenia, braki, koszty napraw i wartość wskazaną przez "
    "rzeczoznawcę; w uzasadnieniu napisz, co z ekspertyzy wpłynęło na kwotę. "
    "Kwoty z ekspertyz bywają brutto albo netto — sprawdź, co mówi raport, "
    "i przelicz na netto.\n"
    "Jeśli któregoś bloku brak, napisz o tym w założeniach."
)

# Format uzasadnienia — dopisywany do każdej instrukcji wyceny. Karta
# wyświetla punkty jako listę (`punkty_uzasadnienia`), więc jeden akapit
# przestaje być ścianą tekstu.
# Silnik waży na rynku wtórnym inaczej niż w cenniku nowych aut: mocniejszy
# i droższy jako nowy (np. BMW V8 50i) bywa używany TAŃSZY od chętniej
# kupowanego słabszego (40i), bo straszy awaryjnością i kosztami. Bez tej
# reguły model wyceniał wg mocy i ceny katalogowej.
INSTRUKCJE_SILNIKA = (
    "\n\nUwzględnij silnik tak, jak wycenia go rynek WTÓRNY, a nie cennik "
    "nowych aut: renomę i awaryjność tej jednostki (znane usterki, koszty "
    "napraw), koszty utrzymania (spalanie, podatki, ubezpieczenie) i popyt "
    "— jak łatwo sprzedać auto z tym silnikiem. Mocniejszy i droższy jako "
    "nowy silnik bywa na rynku wtórnym wart MNIEJ od popularniejszego, "
    "słabszego (np. BMW V8 50i jest zwykle tańszy od chętniej kupowanego "
    "40i). Silnik rozpoznaj po wariancie, pojemności, mocy i paliwie. "
    "Porównania (ogłoszenia, aukcje) z innym silnikiem skoryguj o tę "
    "różnicę, a w uzasadnieniu dodaj punkt „Silnik: …” z oceną jednostki "
    "i jej wpływem na cenę."
)

# Punkty jako ELEMENTY TABLICY, nie linie w jednym napisie: prośba o „nową
# linię na punkt" kończyła się JSON-em z punktami poza cudzysłowem (0.32.5).
FORMAT_UZASADNIENIA = (
    "\n\nPole `uzasadnienie` to lista 3-7 krótkich punktów — każdy punkt "
    "to osobny element tablicy, zaczynający się od tematu z dwukropkiem, "
    "np. „Rynek: …”, „Silnik: …”, „Przebieg: …”, „Stan techniczny: …”, "
    "„Wcześniejsze "
    "wystawienia: …”, „Wniosek: …”. Jedno-dwa zdania na punkt, z konkretnymi "
    "kwotami, bez myślników i innego formatowania."
)

# Dopisek, gdy to samo auto stało już na aukcji i wróciło — czyli się nie
# sprzedało. Bez niego model wyceniał powyżej kwoty, której nikt nie dał.
INSTRUKCJE_WYSTAWIENIA = (
    "\n\nDostajesz też `wczesniejsze_wystawienia_tego_auta`: TEN SAM "
    "samochód był już wystawiony na aukcji i się NIE SPRZEDAŁ (wraca na "
    "aukcję) — zakładaj to ZAWSZE, także gdy tamta aukcja ma cenę "
    "końcową albo wygląda na domkniętą. `pewnosc` = `VIN` — na pewno ten "
    "egzemplarz; `podobne` — "
    "prawdopodobnie (zgodne marka, model, rocznik, silnik, kolor i przebieg), "
    "waż słabiej. Przy `liczba_ofert` = 0 nikt nie dał nawet ceny "
    "wywoławczej; gdy oferty były, `cena_ostatnia` to najwyższa oferta, która "
    "nie wystarczyła do sprzedaży. Traktuj najniższą cenę, za którą to auto "
    "się nie sprzedało, jako GÓRNĄ granicę jego wartości: `wartosc` "
    "i `maksimum` nie powinny jej przekraczać, chyba że od tamtej pory coś "
    "istotnie podniosło wartość — wtedy napisz co. W uzasadnieniu odnieś się "
    "do wcześniejszych wystawień."
)

SCHEMAT_MODELU = "model_otomoto"
INSTRUKCJE_MODELU = (
    "Masz markę, model, wersję i tytuł aukcji samochodu oraz listę modeli tej "
    "marki w taksonomii portalu Otomoto (id i nazwa). Wybierz id modelu, pod "
    "którym Otomoto wystawia TEN samochód — np. „BMW 750Li” to „Seria 7”, "
    "„Mercedes C200” to „Klasa C”. Wybieraj wyłącznie z listy. Gdy żaden "
    "model nie pasuje, zwróć „brak”."
)

SCHEMAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "wartosc": {"type": "integer", "minimum": 0},
        "minimum": {"type": "integer", "minimum": 0},
        "maksimum": {"type": "integer", "minimum": 0},
        # `null` jest tu wartością pełnoprawną: „nie umiem oszacować" musi
        # dać się powiedzieć, inaczej model wypełni pole byle czym.
        "cena_portale": {"type": ["integer", "null"], "minimum": 0},
        "pewnosc": {"type": "string", "enum": ["niska", "średnia", "wysoka"]},
        "uzasadnienie": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 8,
        },
        "zalozenia": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    # Tryb `strict` u OpenAI wymaga, żeby KAŻDE pole było w `required` —
    # opcjonalność wyraża się typem `null`, nie brakiem klucza.
    "required": [
        "wartosc",
        "minimum",
        "maksimum",
        "cena_portale",
        "pewnosc",
        "uzasadnienie",
        "zalozenia",
    ],
    "additionalProperties": False,
}


def _uzasadnienie(wartosc: object) -> str:
    """Tablica punktów (prompt v12+) → „- punkt" w liniach, jak czyta karta.
    Napis (starsze odpowiedzi, dostawcy bez schematu) zostaje bez zmian."""
    if isinstance(wartosc, list):
        return "\n".join(f"- {str(p).strip()}" for p in wartosc if str(p).strip())
    return str(wartosc)


def _liczba_lub_none(wartosc: object) -> int | None:
    """Liczba całkowita albo `None` — bez wyjątku na śmieciach.

    Pole jest z definicji opcjonalne, więc niepoprawna wartość ma znaczyć
    „model nie oszacował", a nie wywracać całą wycenę, która poza tym jest
    dobra.
    """
    if wartosc is None or isinstance(wartosc, bool):
        return None
    try:
        liczba = int(wartosc)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return liczba if liczba > 0 else None


class BladWyceny(RuntimeError):
    """API nie zwróciło wiarygodnego wyniku w oczekiwanym formacie."""


class WycenaAI:
    """Liczy wycenę. **Nie przechowuje jej** — od tego jest baza.

    Wcześniej wynik siedział w cache'u na dysku kluczowanym hashem danych
    wejściowych, razem z porównaniami z zakończonych aukcji. Każda kolejna
    zakończona aukcja tego modelu zmieniała porównania, klucz przestawał
    pasować i karta liczyła wycenę od nowa — inna kwota przy każdym wejściu
    i kolejne płatne żądanie. Trwałość należy do warstwy zapisu, nie do
    klienta modelu.
    """

    def __init__(self, klient: KlientModelu) -> None:
        self._klient = klient
        self._model = klient.model

    @property
    def model(self) -> str:
        return self._model

    async def zamknij(self) -> None:
        await self._klient.zamknij()

    async def wycen(
        self,
        dane: Szczegoly,
        porownania: tuple[PorownanieRynkowe, ...],
        *,
        teraz: dt.datetime,
        otomoto: WynikOtomoto | None = None,
        ekspertyzy: tuple[OcenionyDokument, ...] = (),
        wystawienia: tuple[PowiazaneWystawienie, ...] = (),
    ) -> WycenaAukcji:
        """`otomoto` i `ekspertyzy` tylko z przycisku „Wycena z Otomoto".

        Tekst ekspertyz musi być już zredagowany (`ekspertyza.zredaguj`) —
        ta klasa nie wysyła VIN-u ani danych osób.
        """
        wejscie = self._wejscie(dane, porownania)
        rozszerzona = otomoto is not None or bool(ekspertyzy)
        if otomoto is not None:
            wejscie["ogloszenia_otomoto"] = {
                "liczba": otomoto.liczba,
                "mediana": otomoto.mediana,
                "kwartyl_dolny": otomoto.p25,
                "kwartyl_gorny": otomoto.p75,
                "model_otomoto": otomoto.model_otomoto,
                "odrzucone_z_usa": otomoto.odrzucone_usa,
                "odrzucone_cesje_leasingu": otomoto.odrzucone_cesje,
                "odrzucone_skrajne": otomoto.odrzucone_skrajne,
                "zawezono_do_wersji": otomoto.wersja,
                "uwaga": (
                    None
                    if otomoto.wersja
                    else "porównania mogą mieszać wersje wyposażenia/silnika "
                    "tego modelu — skoryguj o różnicę wersji"
                ),
                "ogloszenia": [
                    {
                        "tytul": o.tytul,
                        "cena": o.cena,
                        "rok": o.rok,
                        "przebieg_km": o.przebieg_km,
                        "wersja": o.wersja,
                        "faktura": o.faktura,
                    }
                    for o in self._najblizsze(dane, otomoto.ogloszenia, 25)
                ],
            }
        if ekspertyzy:
            wejscie["ekspertyza"] = [
                {"dokument": d.nazwa, "tekst": d.tekst} for d in ekspertyzy
            ]
        instrukcje = INSTRUKCJE_OTOMOTO if rozszerzona else INSTRUKCJE
        wczesniejsze = self._wczesniejsze(wystawienia, teraz)
        if wczesniejsze:
            wejscie["wczesniejsze_wystawienia_tego_auta"] = wczesniejsze
            instrukcje += INSTRUKCJE_WYSTAWIENIA
        instrukcje += INSTRUKCJE_SILNIKA + FORMAT_UZASADNIENIA
        try:
            # Zepsuty JSON bywa jednorazowy (dostawca bez wymuszonego
            # schematu) — jedno ponowienie, zanim pokażemy błąd.
            for proba in (1, 2):
                tekst = await self._klient.json_wg_schematu(
                    instrukcje=instrukcje,
                    wejscie=json.dumps(wejscie, ensure_ascii=False),
                    nazwa_schematu=NAZWA_SCHEMATU,
                    schemat=SCHEMAT,
                )
                try:
                    wynik = self._z_tekstu(tekst, dane.pozycja.id, teraz)
                    break
                except json.JSONDecodeError as exc:
                    if proba == 2:
                        raise
                    log.warning("model zwrócił niepoprawny JSON (%s) — ponawiam", exc)
            if not rozszerzona:
                return wynik
            return self._z_danymi_rynku(wynik, dane, otomoto, ekspertyzy)
        except (httpx.HTTPError, BladModelu, ValueError, KeyError, TypeError) as exc:
            log.warning("wycena AI nie powiodła się: %s", exc)
            # Powód od dostawcy idzie NA KARTĘ, nie tylko do logu. „Nie udało
            # się" nie mówi, czy poprawić nazwę modelu, adres czy klucz —
            # a to jedyne trzy rzeczy, które użytkownik może tu zrobić.
            powod = exc.powod if isinstance(exc, BladOdpowiedzi) else None
            raise BladWyceny(
                f"Wycena AI nie powiodła się: {powod}"
                if powod
                else "Nie udało się teraz wygenerować wyceny AI."
            ) from exc

    async def wybierz_model_otomoto(
        self, pojazd: PojazdDoPorownania, modele: Sequence[tuple[str, str]]
    ) -> str | None:
        """Id modelu z ZAMKNIĘTEJ listy taksonomii Otomoto (np. BMW 750 → seria-7).

        Schemat z `enum` nie pozwala zmyślić id; „brak" znaczy „nie wiem".
        Błąd modelu to `None` — wtedy wycena z Otomoto mówi, że nie
        dopasowała modelu, zamiast szukać po całej marce.
        """
        ids = [i for i, _ in modele]
        schemat = {
            "type": "object",
            "properties": {"model_id": {"type": "string", "enum": [*ids, "brak"]}},
            "required": ["model_id"],
            "additionalProperties": False,
        }
        wejscie = {
            "marka": pojazd.marka,
            "model": pojazd.model,
            "wersja": pojazd.wersja,
            "tytul": pojazd.tytul,
            "modele_otomoto": [{"id": i, "nazwa": n} for i, n in modele],
        }
        try:
            tekst = await self._klient.json_wg_schematu(
                instrukcje=INSTRUKCJE_MODELU,
                wejscie=json.dumps(wejscie, ensure_ascii=False),
                nazwa_schematu=SCHEMAT_MODELU,
                schemat=schemat,
            )
            wybrany = json.loads(tekst).get("model_id")
        except (httpx.HTTPError, BladModelu, ValueError, AttributeError) as exc:
            log.warning("AI nie ustaliło modelu Otomoto: %s", exc)
            return None
        log.info("AI: %s %s → model Otomoto %s", pojazd.marka, pojazd.model, wybrany)
        return wybrany if wybrany in ids else None

    def _wejscie(
        self, dane: Szczegoly, porownania: tuple[PorownanieRynkowe, ...]
    ) -> dict[str, Any]:
        p = dane.pozycja
        return {
            "pojazd": {
                "marka": p.make,
                "model": p.model,
                "wariant": p.variant,
                "rok": p.year,
                "przebieg_km": p.mileage_km,
                "paliwo": p.fuel,
                "skrzynia": p.gearbox,
                "pojemnosc_ccm": dane.engine_ccm,
                "moc_km": dane.engine_hp,
                "nadwozie": dane.body,
                "lokalizacja": p.location,
            },
            "porownania_z_zakonczonych_aukcji": [asdict(x) for x in porownania],
        }

    @staticmethod
    def _wczesniejsze(
        wystawienia: tuple[PowiazaneWystawienie, ...], teraz: dt.datetime
    ) -> list[dict[str, Any]]:
        """Historyczne wystawienia tego auta = niesprzedane.

        Wycenia się trwające aukcje, więc każde inne wystawienie, którego
        termin minął, jest historyczne — i skoro auto znów stoi na aukcji,
        nie sprzedało się. Bez względu na status (domknięta, zniknęła,
        niedomknięta) i cenę końcową. Równoległe, wciąż trwające wystawienie
        w innym serwisie to nie porażka sprzedaży — pomijamy. Bez terminu
        decyduje kolejność. Bez serwisu i numeru aukcji: modelowi
        niepotrzebne."""

        def kwota(m: Money | None) -> int | None:
            return None if m is None else round(m.amount)

        return [
            {
                "koniec": w.ends_at.date().isoformat() if w.ends_at else None,
                "cena_wywolawcza": kwota(w.price_start),
                "cena_ostatnia": kwota(w.price_current),
                "liczba_ofert": w.bid_count,
                "przebieg_km": w.mileage_km,
                "pewnosc": "VIN" if w.pewnosc is PewnoscPowiazania.VIN else "podobne",
            }
            for w in wystawienia
            if (w.ends_at < teraz if w.ends_at is not None else not w.pozniejsze)
        ][:5]

    @staticmethod
    def _najblizsze(
        dane: Szczegoly, ogloszenia: tuple[Ogloszenie, ...], ile: int
    ) -> list[Ogloszenie]:
        """Najbardziej podobne pierwsze: rocznik, potem przebieg."""
        p = dane.pozycja

        def odleglosc(o: Ogloszenie) -> tuple[int, int]:
            rok = abs((o.rok or p.year or 0) - (p.year or o.rok or 0))
            km = abs((o.przebieg_km or 0) - (p.mileage_km or 0)) if p.mileage_km else 0
            return rok, km

        return sorted(ogloszenia, key=odleglosc)[:ile]

    def _z_danymi_rynku(
        self,
        wynik: WycenaAukcji,
        dane: Szczegoly,
        otomoto: WynikOtomoto | None,
        ekspertyzy: tuple[OcenionyDokument, ...],
    ) -> WycenaAukcji:
        """Zmierzone liczby z Otomoto mają pierwszeństwo przed pamięcią modelu."""
        waluta = Currency.PLN

        def pln(kwota: int | None) -> Money | None:
            return None if kwota is None else Money(Decimal(kwota), waluta)

        return replace(
            wynik,
            zrodlo="OTOMOTO",
            cena_portale=(
                pln(otomoto.mediana)
                if otomoto is not None and otomoto.mediana is not None
                else wynik.cena_portale
            ),
            otomoto_liczba=None if otomoto is None else otomoto.liczba,
            otomoto_p25=None if otomoto is None else pln(otomoto.p25),
            otomoto_p75=None if otomoto is None else pln(otomoto.p75),
            otomoto_adres=None if otomoto is None else otomoto.adres,
            otomoto_ogloszenia=(
                ()
                if otomoto is None
                else tuple(
                    OgloszenieRynkowe(
                        tytul=o.tytul,
                        cena=Money(Decimal(o.cena), waluta),
                        rok=o.rok,
                        przebieg_km=o.przebieg_km,
                        url=o.url,
                    )
                    for o in self._najblizsze(
                        dane, otomoto.ogloszenia, MAKS_OGLOSZEN_NA_KARCIE
                    )
                )
            ),
            ekspertyzy=tuple(d.nazwa for d in ekspertyzy),
        )

    def _z_tekstu(
        self, tekst: str, auction_id: int, teraz: dt.datetime
    ) -> WycenaAukcji:
        """Sprawdza wynik modelu, zamiast mu wierzyć.

        Schemat wymusza dostawca, ale nie każdy endpoint zgodny z OpenAI go
        honoruje, a `minimum <= wartosc <= maksimum` nie da się wyrazić
        w JSON Schema. Niespójny przedział jest gorszy niż brak wyceny —
        wygląda wiarygodnie.
        """
        dane = json.loads(tekst)
        minimum = int(dane["minimum"])
        wartosc = int(dane["wartosc"])
        maksimum = int(dane["maksimum"])
        if not 0 <= minimum <= wartosc <= maksimum:
            raise BladWyceny("Model zwrócił niespójny przedział wyceny.")
        portale = _liczba_lub_none(dane.get("cena_portale"))
        return WycenaAukcji(
            auction_id=auction_id,
            wartosc=Money(Decimal(wartosc), Currency.PLN),
            minimum=Money(Decimal(minimum), Currency.PLN),
            maksimum=Money(Decimal(maksimum), Currency.PLN),
            cena_portale=(
                None if portale is None else Money(Decimal(portale), Currency.PLN)
            ),
            pewnosc=str(dane["pewnosc"]),
            uzasadnienie=_uzasadnienie(dane["uzasadnienie"]),
            zalozenia=tuple(str(x) for x in dane["zalozenia"]),
            model=self._model,
            wersja_promptu=WERSJA_PROMPTU,
            utworzono=teraz,
        )
