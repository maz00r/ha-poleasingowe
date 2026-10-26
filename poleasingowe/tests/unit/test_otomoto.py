"""Porównanie z Otomoto przez Apify (0.31.0)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.infrastructure import otomoto
from app.infrastructure.otomoto import BladOtomoto, Otomoto, PojazdDoPorownania

OCTAVIA = PojazdDoPorownania(
    "Škoda", "Octavia", 2022, 150_000, "Hybryda", "Automatyczna"
)


def test_adres_z_filtrami_otomoto() -> None:
    adres = otomoto.adres_wyszukiwania(OCTAVIA)
    assert adres.startswith("https://www.otomoto.pl/osobowe/skoda/octavia/od-2021?")
    assert "filter_float_year%3Ato%5D=2023" in adres
    assert "filter_float_mileage%3Afrom%5D=90000" in adres
    assert "filter_enum_fuel_type%5D%5B0%5D=hybrid" in adres
    assert "filter_enum_fuel_type%5D%5B1%5D=plugin-hybrid" in adres
    assert "filter_enum_gearbox%5D=automatic" in adres


def test_szeroko_fraza_bez_przebiegu_i_dostawcze() -> None:
    p = PojazdDoPorownania(
        "Peugeot", "Boxer", 2016, 290_000, "Diesel", None, dostawczy=True
    )
    adres = otomoto.adres_wyszukiwania(p, fraza=True, szeroko=True)
    assert adres.startswith("https://www.otomoto.pl/dostawcze/peugeot/q-boxer/od-2014?")
    assert "mileage" not in adres


def test_slug_marek() -> None:
    assert otomoto.slug("Mercedes-Benz") == "mercedes-benz"
    assert otomoto.slug("Citroën") == "citroen"
    assert otomoto.slug("Land Rover") == "land-rover"


def test_model_spoza_sciezki_odfiltrowany() -> None:
    """Otomoto cofa nieznany model do całej marki — nie liczymy innych modeli."""
    assert otomoto.pasuje(
        {"make": "Skoda", "model": "Octavia", "year": 2021}, OCTAVIA, szeroko=False
    )
    assert not otomoto.pasuje(
        {"make": "Skoda", "model": "Superb", "year": 2022}, OCTAVIA, szeroko=False
    )
    assert not otomoto.pasuje(
        {"make": "Skoda", "model": "Octavia", "year": 2018}, OCTAVIA, szeroko=False
    )
    bmw = PojazdDoPorownania("BMW", "530e", 2021)
    assert otomoto.pasuje(
        {"make": "BMW", "model": "Seria 5", "title": "BMW Seria 5 530e"},
        bmw,
        szeroko=False,
    )
    assert not otomoto.pasuje(
        {"make": "BMW", "model": "Seria 5", "title": "BMW 520d"}, bmw, szeroko=False
    )


def _rekord(i: int, model: str = "Octavia") -> dict[str, Any]:
    return {
        "make": "Skoda",
        "model": model,
        "year": 2022,
        "mileageKm": 140_000 + i,
        "pricePLN": 80_000 + 1_000 * i,
        "title": f"Skoda {model} {i}",
        "url": f"https://www.otomoto.pl/osobowe/oferta/x-{model}-{i}",
    }


async def test_dwie_proby_i_statystyka() -> None:
    zadania: list[dict[str, Any]] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        assert zadanie.headers["authorization"] == "Bearer tajny"
        assert "tajny" not in str(zadanie.url)
        zadania.append(json.loads(zadanie.content))
        if len(zadania) == 1:
            # Pierwsza próba: model cofnięty do marki — same Superby i 3 Octavie.
            return httpx.Response(
                200,
                json=[_rekord(i, "Superb") for i in range(10)]
                + [_rekord(i) for i in range(3)],
            )
        return httpx.Response(200, json=[_rekord(i) for i in range(12)])

    usluga = Otomoto(
        "tajny", klient=httpx.AsyncClient(transport=httpx.MockTransport(obsluz))
    )
    wynik = await usluga.porownaj(OCTAVIA)

    assert len(zadania) == 2
    assert "/q-octavia/" in zadania[1]["startUrls"][0]
    assert wynik.liczba == 12
    assert wynik.mediana == 85_500
    assert wynik.p25 is not None and wynik.p75 is not None and wynik.p25 < wynik.p75


async def test_za_malo_porownan_bez_mediany() -> None:
    usluga = Otomoto(
        "t",
        klient=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=[_rekord(1)])
            )
        ),
    )
    wynik = await usluga.porownaj(OCTAVIA)
    assert wynik.liczba == 1 and wynik.mediana is None


async def test_zly_token_to_czytelny_blad() -> None:
    usluga = Otomoto(
        "t",
        klient=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(401))
        ),
    )
    with pytest.raises(BladOtomoto, match="apify_token"):
        await usluga.porownaj(OCTAVIA)
