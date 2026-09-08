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

import hashlib
import json
import logging
import pathlib
import tempfile
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from app.application.read_models import PorownanieRynkowe, Szczegoly
from app.infrastructure.ai_klienci import BladModelu, KlientModelu

log = logging.getLogger(__name__)

KATALOG_CACHE = pathlib.Path("/data/cache/wyceny")
WERSJA_PROMPTU = 1
"""Podbij, gdy zmienisz `INSTRUKCJE` albo `SCHEMAT` — inaczej cache oddawałby
wyniki wygenerowane starym poleceniem."""

NAZWA_SCHEMATU = "wycena_pojazdu"

INSTRUKCJE = (
    "Jesteś analitykiem polskiego rynku samochodów używanych. "
    "Oszacuj uczciwą cenę detaliczną pojazdu w PLN na dziś. "
    "Największą wagę nadaj porównaniom z zakończonych aukcji, "
    "ale uwzględnij różnice rocznika i przebiegu. Jeśli danych "
    "jest mało, poszerz przedział i obniż pewność. Nie zakładaj "
    "idealnego stanu technicznego; jasno wypisz założenia."
)

SCHEMAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "wartosc": {"type": "integer", "minimum": 0},
        "minimum": {"type": "integer", "minimum": 0},
        "maksimum": {"type": "integer", "minimum": 0},
        "pewnosc": {"type": "string", "enum": ["niska", "średnia", "wysoka"]},
        "uzasadnienie": {"type": "string"},
        "zalozenia": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    "required": [
        "wartosc",
        "minimum",
        "maksimum",
        "pewnosc",
        "uzasadnienie",
        "zalozenia",
    ],
    "additionalProperties": False,
}


@dataclass(slots=True, frozen=True)
class Wycena:
    wartosc: int
    minimum: int
    maksimum: int
    pewnosc: str
    uzasadnienie: str
    zalozenia: tuple[str, ...]
    model: str


class BladWyceny(RuntimeError):
    """API nie zwróciło wiarygodnego wyniku w oczekiwanym formacie."""


class WycenaAI:
    def __init__(
        self,
        klient: KlientModelu,
        *,
        katalog: pathlib.Path = KATALOG_CACHE,
    ) -> None:
        self._klient = klient
        self._model = klient.model
        self._katalog = katalog

    async def zamknij(self) -> None:
        await self._klient.zamknij()

    async def wycen(
        self, dane: Szczegoly, porownania: tuple[PorownanieRynkowe, ...]
    ) -> Wycena:
        wejscie = self._wejscie(dane, porownania)
        sciezka = self._sciezka_cache(wejscie)
        z_cache = self._odczytaj(sciezka)
        if z_cache is not None:
            return z_cache

        try:
            tekst = await self._klient.json_wg_schematu(
                instrukcje=INSTRUKCJE,
                wejscie=json.dumps(wejscie, ensure_ascii=False),
                nazwa_schematu=NAZWA_SCHEMATU,
                schemat=SCHEMAT,
            )
            wynik = self._z_tekstu(tekst)
        except (httpx.HTTPError, BladModelu, ValueError, KeyError, TypeError) as exc:
            log.warning("wycena AI nie powiodła się: %s", exc)
            raise BladWyceny("Nie udało się teraz wygenerować wyceny AI.") from exc

        self._zapisz(sciezka, wynik)
        return wynik

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

    def _sciezka_cache(self, wejscie: dict[str, Any]) -> pathlib.Path:
        surowe = json.dumps(
            {"prompt": WERSJA_PROMPTU, "model": self._model, "dane": wejscie},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        odcisk = hashlib.sha256(surowe.encode()).hexdigest()
        return self._katalog / f"{odcisk}.json"

    def _odczytaj(self, sciezka: pathlib.Path) -> Wycena | None:
        try:
            dane = json.loads(sciezka.read_text(encoding="utf-8"))
            return Wycena(
                wartosc=int(dane["wartosc"]),
                minimum=int(dane["minimum"]),
                maksimum=int(dane["maksimum"]),
                pewnosc=str(dane["pewnosc"]),
                uzasadnienie=str(dane["uzasadnienie"]),
                zalozenia=tuple(str(x) for x in dane["zalozenia"]),
                model=str(dane["model"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _zapisz(self, sciezka: pathlib.Path, wynik: Wycena) -> None:
        try:
            self._katalog.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._katalog, delete=False
            ) as plik:
                json.dump(asdict(wynik), plik, ensure_ascii=False)
                tymczasowy = pathlib.Path(plik.name)
            tymczasowy.replace(sciezka)
        except OSError as exc:
            log.warning("nie udało się zapisać cache'u wyceny: %s", exc)

    def _z_tekstu(self, tekst: str) -> Wycena:
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
        return Wycena(
            wartosc=wartosc,
            minimum=minimum,
            maksimum=maksimum,
            pewnosc=str(dane["pewnosc"]),
            uzasadnienie=str(dane["uzasadnienie"]),
            zalozenia=tuple(str(x) for x in dane["zalozenia"]),
            model=self._model,
        )
