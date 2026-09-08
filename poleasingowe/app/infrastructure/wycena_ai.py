"""Wycena pojazdu przez OpenAI Responses API, z lokalnym cache'em.

Model dostaje wyłącznie techniczne dane pojazdu i zagregowane ceny z naszej
bazy. Nie wysyłamy VIN-u, identyfikatora aukcji ani danych sprzedającego.
Wynik jest pomocą przy analizie, nie opinią rzeczoznawcy.
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

log = logging.getLogger(__name__)

KATALOG_CACHE = pathlib.Path("/data/cache/wyceny")
WERSJA_PROMPTU = 1


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
        api_key: str,
        *,
        model: str,
        katalog: pathlib.Path = KATALOG_CACHE,
        klient: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._katalog = katalog
        self._klient = klient
        self._wlasny_klient = klient is None

    async def zamknij(self) -> None:
        if self._wlasny_klient and self._klient is not None:
            await self._klient.aclose()
            self._klient = None

    async def wycen(
        self, dane: Szczegoly, porownania: tuple[PorownanieRynkowe, ...]
    ) -> Wycena:
        wejscie = self._wejscie(dane, porownania)
        sciezka = self._sciezka_cache(wejscie)
        z_cache = self._odczytaj(sciezka)
        if z_cache is not None:
            return z_cache

        klient = self._klient
        if klient is None:
            klient = httpx.AsyncClient(timeout=60.0)
            self._klient = klient

        try:
            odpowiedz = await klient.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._zadanie(wejscie),
            )
            odpowiedz.raise_for_status()
            wynik = self._z_odpowiedzi(odpowiedz.json())
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
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

    def _zadanie(self, wejscie: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": self._model,
            "store": False,
            "instructions": (
                "Jesteś analitykiem polskiego rynku samochodów używanych. "
                "Oszacuj uczciwą cenę detaliczną pojazdu w PLN na dziś. "
                "Największą wagę nadaj porównaniom z zakończonych aukcji, "
                "ale uwzględnij różnice rocznika i przebiegu. Jeśli danych "
                "jest mało, poszerz przedział i obniż pewność. Nie zakładaj "
                "idealnego stanu technicznego; jasno wypisz założenia."
            ),
            "input": json.dumps(wejscie, ensure_ascii=False),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "wycena_pojazdu",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "wartosc": {"type": "integer", "minimum": 0},
                            "minimum": {"type": "integer", "minimum": 0},
                            "maksimum": {"type": "integer", "minimum": 0},
                            "pewnosc": {
                                "type": "string",
                                "enum": ["niska", "średnia", "wysoka"],
                            },
                            "uzasadnienie": {"type": "string"},
                            "zalozenia": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 5,
                            },
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
                    },
                }
            },
        }

    def _z_odpowiedzi(self, odpowiedz: dict[str, Any]) -> Wycena:
        tekst: str | None = None
        for element in odpowiedz.get("output", []):
            if element.get("type") != "message":
                continue
            for tresc in element.get("content", []):
                if tresc.get("type") == "output_text":
                    tekst = tresc.get("text")
                    break
        if not tekst:
            raise BladWyceny("API nie zwróciło treści wyceny.")
        dane = json.loads(tekst)
        minimum = int(dane["minimum"])
        wartosc = int(dane["wartosc"])
        maksimum = int(dane["maksimum"])
        if not 0 <= minimum <= wartosc <= maksimum:
            raise BladWyceny("API zwróciło niespójny przedział wyceny.")
        return Wycena(
            wartosc=wartosc,
            minimum=minimum,
            maksimum=maksimum,
            pewnosc=str(dane["pewnosc"]),
            uzasadnienie=str(dane["uzasadnienie"]),
            zalozenia=tuple(str(x) for x in dane["zalozenia"]),
            model=self._model,
        )
