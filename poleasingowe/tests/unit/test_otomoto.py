"""Własny odczyt Otomoto (0.32.0): JSON strony, netto wg odznak, taksonomia."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.infrastructure import otomoto
from app.infrastructure.otomoto import BladOtomoto, Otomoto, PojazdDoPorownania

BMW = PojazdDoPorownania(
    "BMW", "750", 2020, 90_000, "Diesel", "Automatyczna", wersja="Li xDrive"
)


def _strona(stany: list[dict[str, Any]]) -> str:
    """Strona Otomoto w kształcie zmierzonym 2026-10-26: `__NEXT_DATA__`
    → `props.pageProps.urqlState` → wpisy z polem `data` (JSON w napisie)."""
    dane = {
        "props": {
            "pageProps": {
                "urqlState": {
                    str(i): {"data": json.dumps(s)} for i, s in enumerate(stany)
                }
            }
        }
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(dane)
        + "</script></html>"
    )


TAKSONOMIA = {
    "filters": {
        "states": [
            {
                "__typename": "OpenForInputFilterState",
                "filterId": "filter_enum_model",
                "conditions": [{"filterId": "filter_enum_make", "value": "bmw"}],
                "values": [
                    {
                        "values": [
                            {"id": "seria-5", "name": "Seria 5"},
                            {"id": "seria-7", "name": "Seria 7"},
                            {"id": "x5", "name": "X5"},
                            {"id": "gen-g11", "name": "G11"},
                            {"id": "other", "name": "Inny"},
                        ]
                    }
                ],
            }
        ]
    }
}


def _ogloszenie(
    i: int,
    cena: int,
    *,
    is_gross: bool = True,
    odznaki: tuple[str, ...] = (),
    kraj: str = "pl",
    model: str = "seria-7",
    rok: int = 2020,
    tytul: str = "BMW Seria 7 750Li xDrive",
    opis: str = "",
) -> dict[str, Any]:
    return {
        "node": {
            "title": tytul,
            "shortDescription": opis,
            "url": f"https://www.otomoto.pl/osobowe/oferta/bmw-{i}.html",
            "price": {
                "amount": {"units": cena},
                "isGross": is_gross,
                "badges": list(odznaki),
            },
            "parameters": [
                {"key": "model", "value": model, "displayValue": "Seria 7"},
                {"key": "year", "value": str(rok), "displayValue": str(rok)},
                {"key": "mileage", "value": str(80_000 + i), "displayValue": ""},
                {"key": "country_origin", "value": kraj, "displayValue": kraj},
                {"key": "version", "value": "ver-x", "displayValue": "750Li xDrive"},
            ],
        }
    }


def _wyniki(krawedzie: list[dict[str, Any]], razem: int | None = None) -> str:
    return _strona(
        [{"advertSearch": {"totalCount": razem or len(krawedzie), "edges": krawedzie}}]
    )


def test_taksonomia_bez_generacji_i_innych() -> None:
    mapa = otomoto.taksonomia(_strona([TAKSONOMIA]))
    assert mapa["bmw"] == (("seria-5", "Seria 5"), ("seria-7", "Seria 7"), ("x5", "X5"))


def test_dopasowanie_modelu_deterministyczne() -> None:
    modele = otomoto.taksonomia(_strona([TAKSONOMIA]))["bmw"]
    assert otomoto.dopasuj_model(BMW, modele) is None  # „750" — potrzebne AI
    seria = PojazdDoPorownania("BMW", "Seria 7", 2020)
    assert otomoto.dopasuj_model(seria, modele) == "seria-7"
    x5 = PojazdDoPorownania("BMW", "X5", 2020)
    assert otomoto.dopasuj_model(x5, modele) == "x5"


Z_VAT = otomoto.FAKTURA_VAT
Z_OPISU = otomoto.FAKTURA_VAT_OPIS
MARZA = otomoto.FAKTURA_MARZA
BEZ_VAT = otomoto.FAKTURA_BRAK


@pytest.mark.parametrize(
    ("is_gross", "odznaki", "opis", "oczekiwane"),
    [
        # isGross=false nie znaczy „kwota netto" — kwota jest zawsze brutto.
        (False, ("INCLUDE_VAT", "INVOICE_ISSUED"), "", Z_VAT),
        (False, ("INCLUDE_VAT",), "", Z_VAT),
        (True, ("INCLUDE_VAT", "INVOICE_ISSUED"), "", Z_VAT),
        (True, ("INVOICE_ISSUED",), "Faktura Vat Marża", MARZA),
        (True, ("INVOICE_ISSUED",), "", MARZA),
        # Prywatni z firmą: odznak brak, VAT tylko w opisie.
        (True, (), "Hak, Ceramika, opony zimowe, FAKTURA VAT 23% SALON PL", Z_OPISU),
        (True, (), "Salon PL, FV 23%, Rozszerzony Autopilot", Z_OPISU),
        (True, (), "Tesla Y Long Range AWD Gwarancja | FV23%", Z_OPISU),
        (True, (), "105 000 + VAT", Z_OPISU),
        (True, (), "VAT marża, bezwypadkowy", MARZA),
        (True, (), "Salon PL, bezwypadkowy", BEZ_VAT),
    ],
)
def test_faktura_wg_odznak_i_opisu(
    is_gross: bool, odznaki: tuple[str, ...], opis: str, oczekiwane: str
) -> None:
    (rekord,), _ = otomoto.wyniki(
        _wyniki(
            [_ogloszenie(1, 100_000, is_gross=is_gross, odznaki=odznaki, opis=opis)]
        )
    )
    assert otomoto.faktura(rekord) == oczekiwane


def test_adres_z_modelem_taksonomii_bez_przebiegu() -> None:
    adres = otomoto.adres_wyszukiwania(BMW, "seria-7", strona=2)
    assert adres.startswith("https://www.otomoto.pl/osobowe/bmw/seria-7/od-2019?")
    assert "filter_enum_damaged%5D=0" in adres
    assert "filter_enum_fuel_type%5D%5B0%5D=diesel" in adres
    assert "mileage" not in adres
    assert adres.endswith("&page=2")


def test_strona_bez_danych_to_blad() -> None:
    with pytest.raises(BladOtomoto):
        otomoto.wyniki("<html></html>")


async def test_porownanie_ai_wybiera_model_i_odrzuca_usa_cesje_inne() -> None:
    krawedzie = [_ogloszenie(i, 200_000 + 1_000 * i) for i in range(10)]
    krawedzie += [
        _ogloszenie(50, 90_000, kraj="usa"),
        _ogloszenie(51, 4_324, opis="CESJA LEASINGU, odstępne"),
        _ogloszenie(52, 150_000, model="seria-5"),
        _ogloszenie(53, 150_000, rok=2015),
        _ogloszenie(54, 205_500, opis="FV 23%"),
    ]
    zadania: list[str] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        zadania.append(str(zadanie.url))
        if zadanie.url.path == "/osobowe":
            return httpx.Response(200, text=_strona([TAKSONOMIA]))
        return httpx.Response(200, text=_wyniki(krawedzie))

    wybory: list[Any] = []

    async def ai(pojazd: PojazdDoPorownania, modele: Any) -> str | None:
        wybory.append([i for i, _ in modele])
        return "seria-7"

    etapy: list[str] = []
    usluga = Otomoto(klient=httpx.AsyncClient(transport=httpx.MockTransport(obsluz)))
    wynik = await usluga.porownaj(
        BMW, wybieracz=ai, postep=lambda etap, _: etapy.append(etap)
    )

    assert wybory == [["seria-5", "seria-7", "x5"]]
    assert wynik.model_otomoto == "Seria 7"
    assert wynik.odrzucone_usa == 1 and wynik.odrzucone_cesje == 1
    assert wynik.liczba == 11  # 10 + jedno z FV; seria-5 i rocznik 2015 odpadły
    assert any(o.faktura == otomoto.FAKTURA_VAT_OPIS for o in wynik.ogloszenia)
    # Każda kwota ÷1,23 — także bez faktury.
    assert all(o.cena < 171_000 for o in wynik.ogloszenia)
    assert len(zadania) == 2  # taksonomia + jedna strona (15 < 32)
    assert any("Model AI" in e for e in etapy)


async def test_bez_dopasowanego_modelu_nie_szuka_po_calej_marce() -> None:
    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        assert zadanie.url.path == "/osobowe", "nie wolno szukać bez modelu"
        return httpx.Response(200, text=_strona([TAKSONOMIA]))

    async def ai(pojazd: PojazdDoPorownania, modele: Any) -> str | None:
        return "zmyslony"

    usluga = Otomoto(klient=httpx.AsyncClient(transport=httpx.MockTransport(obsluz)))
    with pytest.raises(BladOtomoto, match="dopasować modelu"):
        await usluga.porownaj(BMW, wybieracz=ai)


def _o(tytul: str, cena: int) -> otomoto.Ogloszenie:
    return otomoto.Ogloszenie(
        tytul, cena, 2023, 30_000, f"https://o.test/{tytul}{cena}"
    )


def test_zawezenie_do_wersji_gdy_sa_co_najmniej_trzy() -> None:
    oferty = [_o("Tesla Model Y Performance", 100_000 + i) for i in range(3)]
    oferty += [_o("Tesla Model Y Long Range AWD", 70_000 + i) for i in range(6)]
    zawezone, wersja = otomoto.zawez_do_wersji(oferty, "Performance AWD 534 KM")
    assert wersja == "performance" and len(zawezone) == 3


def test_statystyka_odrzuca_skrajne() -> None:
    oferty = [_o("a", 100_000 + 1_000 * i) for i in range(10)] + [_o("b", 1_000)]
    wynik = otomoto.statystyka("u", oferty)
    assert wynik.odrzucone_skrajne == 1 and wynik.mediana is not None
