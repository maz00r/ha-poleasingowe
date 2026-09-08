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
from dataclasses import asdict
from decimal import Decimal
from typing import Any

import httpx

from app.application.read_models import PorownanieRynkowe, Szczegoly
from app.domain.entities import WycenaAukcji
from app.domain.enums import Currency
from app.domain.value_objects import Money
from app.infrastructure.ai_klienci import BladModelu, BladOdpowiedzi, KlientModelu

log = logging.getLogger(__name__)

WERSJA_PROMPTU = 2
"""Podbij, gdy zmienisz `INSTRUKCJE` albo `SCHEMAT`.

Zapisana wycena niesie wersję, którą policzono — dzięki temu widać, że
powstała starym poleceniem, i można ją świadomie przeliczyć.

2: doszedł szacunek poziomu cen ofertowych na polskich portalach."""

NAZWA_SCHEMATU = "wycena_pojazdu"

INSTRUKCJE = (
    "Jesteś analitykiem polskiego rynku samochodów używanych. "
    "Oszacuj uczciwą cenę detaliczną pojazdu w PLN na dziś. "
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
        "uzasadnienie": {"type": "string"},
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
    ) -> WycenaAukcji:
        wejscie = self._wejscie(dane, porownania)
        try:
            tekst = await self._klient.json_wg_schematu(
                instrukcje=INSTRUKCJE,
                wejscie=json.dumps(wejscie, ensure_ascii=False),
                nazwa_schematu=NAZWA_SCHEMATU,
                schemat=SCHEMAT,
            )
            return self._z_tekstu(tekst, dane.pozycja.id, teraz)
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
            uzasadnienie=str(dane["uzasadnienie"]),
            zalozenia=tuple(str(x) for x in dane["zalozenia"]),
            model=self._model,
            wersja_promptu=WERSJA_PROMPTU,
            utworzono=teraz,
        )
