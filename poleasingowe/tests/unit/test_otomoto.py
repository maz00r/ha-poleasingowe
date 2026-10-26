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


async def test_jeden_przebieg_netto_bez_usa_i_skrajnych() -> None:
    zadania: list[dict[str, Any]] = []
    rekordy = [_rekord(i) for i in range(10)]
    rekordy += [_rekord(i, "Superb") for i in range(5)]  # model cofnięty do marki
    usa = _rekord(50)
    usa["title"] = "Skoda Octavia z USA, Copart"
    sprowadzenie = _rekord(51)
    sprowadzenie["shortDescription"] = "auto do sprowadzenia"
    tylko_w_opisie = _rekord(53)
    tylko_w_opisie["fullDescription"] = "Samochód sprowadzony z USA, bezwypadkowy."
    skrajna = _rekord(52)
    skrajna["pricePLN"] = 1_000
    rekordy += [usa, sprowadzenie, tylko_w_opisie, skrajna]

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        assert zadanie.headers["authorization"] == "Bearer tajny"
        assert "tajny" not in str(zadanie.url)
        zadania.append(json.loads(zadanie.content))
        return httpx.Response(200, json=rekordy)

    usluga = Otomoto(
        "tajny", klient=httpx.AsyncClient(transport=httpx.MockTransport(obsluz))
    )
    wynik = await usluga.porownaj(OCTAVIA)

    assert len(zadania) == 1
    assert "/q-octavia/" in zadania[0]["startUrls"][0]
    assert wynik.odrzucone_usa == 3
    assert zadania[0]["includeDetails"] is True
    assert wynik.odrzucone_skrajne == 1
    assert wynik.liczba == 10
    # 80 000…89 000 brutto → netto ÷1,23; mediana z kwot netto po zaokrągleniu każdej
    assert wynik.mediana == 68_700
    assert all(o.cena < 80_000 for o in wynik.ogloszenia)


def test_netto_z_brutto() -> None:
    assert otomoto.netto(123_000) == 100_000


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


def test_adres_wyklucza_auta_uszkodzone() -> None:
    assert "filter_enum_damaged%5D=0" in otomoto.adres_wyszukiwania(OCTAVIA)


def _o(tytul: str, cena: int) -> otomoto.Ogloszenie:
    return otomoto.Ogloszenie(
        tytul, cena, 2023, 30_000, f"https://o.test/{tytul}{cena}"
    )


def test_zawezenie_do_wersji_gdy_sa_co_najmniej_trzy() -> None:
    oferty = [_o("Tesla Model Y Performance", 100_000 + i) for i in range(3)]
    oferty += [
        _o("Tesla Model Y Long Range Dual Motor AWD", 70_000 + i) for i in range(6)
    ]
    zawezone, wersja = otomoto.zawez_do_wersji(oferty, "Performance AWD 534 KM")
    assert wersja == "performance" and len(zawezone) == 3


def test_bez_zawezenia_gdy_wersji_za_malo() -> None:
    oferty = [_o("Tesla Model Y Long Range AWD", 70_000 + i) for i in range(6)]
    oferty.append(_o("Tesla Model Y Performance", 100_000))
    zawezone, wersja = otomoto.zawez_do_wersji(oferty, "Performance AWD")
    assert wersja is None and len(zawezone) == 7


def test_slowa_wspolne_nie_zawezaja() -> None:
    assert otomoto.slowa_wersji("AWD Dual Motor 4x4 534 KM") == ()
