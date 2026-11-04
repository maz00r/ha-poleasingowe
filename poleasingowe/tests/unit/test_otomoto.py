"""Własny odczyt Otomoto (0.32.0): JSON strony, netto wg odznak, taksonomia."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.infrastructure import otomoto
from app.infrastructure.otomoto import (
    BladDostepuOtomoto,
    BladOtomoto,
    Otomoto,
    PojazdDoPorownania,
)

BMW = PojazdDoPorownania(
    "BMW", "750", 2020, 90_000, "Diesel", "Automatyczna", wersja="Li xDrive"
)


FORD = PojazdDoPorownania(
    "Ford", "TRANSIT", 2019, 150_000, "Diesel", "Manualna", True, "2.0 EcoBlue L3H2"
)

MARKI = {
    "filterId": "filter_enum_make",
    "__typename": "OpenForInputFilterState",
    "values": [
        {"values": [{"id": "ford", "name": "Ford"}]},
        {"values": [{"id": "fiat", "name": "Fiat"}, "śmieć"]},
    ],
}


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


def test_adres_dostawczego_ma_fraze_za_rocznikiem() -> None:
    """Fraza przed `od-2018` kasuje filtr roku: 972 trafienia zamiast 470."""
    adres = otomoto.adres_wyszukiwania(FORD, "transit", strona=2)
    assert adres.startswith("https://www.otomoto.pl/dostawcze/ford/od-2018/q-transit?")
    assert "filter_enum_fuel_type" in adres and "page=2" in adres


def test_adres_dostawczego_bez_rocznika() -> None:
    pojazd = PojazdDoPorownania("Fiat", "DUCATO", dostawczy=True)
    adres = otomoto.adres_wyszukiwania(pojazd, "ducato")
    assert adres.startswith("https://www.otomoto.pl/dostawcze/fiat/q-ducato?")


def test_marki_z_filtra_kategorii() -> None:
    assert otomoto.marki(_strona([MARKI])) == {"ford", "fiat"}


@pytest.mark.parametrize(
    ("model", "wersja", "fraza"),
    [
        ("TRANSIT", "2.0 EcoBlue L3H2", "transit"),
        ("TRANSIT", None, "transit"),
        ("TRANSIT", "CUSTOM 2.0 L2", "transit-custom"),
        ("TRANSIT", "Connect", "transit-connect"),
        ("DUCATO", "Custom", "ducato"),
    ],
)
def test_fraza_dostawczego(model: str, wersja: str | None, fraza: str) -> None:
    pojazd = PojazdDoPorownania("Ford", model, dostawczy=True, wersja=wersja)
    assert otomoto.model_dostawczego(pojazd) == fraza


@pytest.mark.parametrize(
    ("tytul", "model", "fraza", "pasuje"),
    [
        ("Ford Transit Kasten Trend", "Transit Kasten", "transit", True),
        ("Ford transit-connect 8 SPD", "transit-connect", "transit", False),
        ("Ford Custom Transit DCIV", "Custom Transit", "transit", False),
        ("Ford Connect", "Connect", "transit", False),
        ("Ford Transit Custom", "TRANSIT CUSTOM", "transit-custom", True),
        ("Ford Transit", "Transit", "transit-custom", False),
        ("Fiat Ducato L3H2", "Ducato L3H2", "ducato", True),
        ("Fiat Scudo", "Scudo", "ducato", False),
    ],
)
def test_ogloszenie_dostawczego_wg_slow(
    tytul: str, model: str, fraza: str, pasuje: bool
) -> None:
    rekord = otomoto.Rekord(
        tytul=tytul,
        opis="",
        url="https://x",
        cena=1,
        is_gross=True,
        odznaki=(),
        parametry={"model": (model, model)},
    )
    assert otomoto.ten_sam_dostawczy(rekord, fraza) is pasuje


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


async def test_dostawczy_bez_taksonomii_i_bez_pytania_modelu_ai() -> None:
    """Strona `/dostawcze` nie ma modeli, a `model` ogłoszenia to wolny tekst."""

    def transit(i: int, tytul: str = "Ford Transit Kasten") -> dict[str, Any]:
        return _ogloszenie(i, 60_000 + 500 * i, model=tytul, rok=2019, tytul=tytul)

    krawedzie = [transit(i) for i in range(10)]
    krawedzie += [transit(50, "Ford Transit Connect"), transit(51, "Ford Ranger")]
    zadania: list[str] = []

    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        zadania.append(zadanie.url.path)
        if zadanie.url.path == "/dostawcze":
            return httpx.Response(200, text=_strona([MARKI]))
        return httpx.Response(200, text=_wyniki(krawedzie))

    async def ai(pojazd: PojazdDoPorownania, modele: Any) -> str | None:
        raise AssertionError("dostawcze nie mają listy modeli do wyboru")

    usluga = Otomoto(klient=httpx.AsyncClient(transport=httpx.MockTransport(obsluz)))
    wynik = await usluga.porownaj(FORD, wybieracz=ai)
    await usluga.porownaj(FORD, wybieracz=ai)

    assert wynik.liczba == 10
    assert wynik.mediana is not None
    assert wynik.model_otomoto == "Transit"
    assert zadania.count("/dostawcze") == 1, "marki są pamiętane jak taksonomia"
    assert "/dostawcze/ford/od-2018/q-transit" in zadania


async def test_nieznana_marka_dostawcza_nie_szuka_po_calej_kategorii() -> None:
    def obsluz(zadanie: httpx.Request) -> httpx.Response:
        assert zadanie.url.path == "/dostawcze", "nie wolno szukać bez marki"
        return httpx.Response(200, text=_strona([MARKI]))

    usluga = Otomoto(klient=httpx.AsyncClient(transport=httpx.MockTransport(obsluz)))
    with pytest.raises(BladOtomoto, match="nie zna marki Informujemy,"):
        await usluga.porownaj(PojazdDoPorownania("Informujemy,", "że", dostawczy=True))


def _usluga_odpowiadajaca(kod: int) -> Otomoto:
    def obsluz(_: httpx.Request) -> httpx.Response:
        return httpx.Response(kod)

    return Otomoto(klient=httpx.AsyncClient(transport=httpx.MockTransport(obsluz)))


@pytest.mark.parametrize("kod", [403, 429, 500, 503])
async def test_blokada_i_awaria_dotycza_calego_otomoto(kod: int) -> None:
    with pytest.raises(BladDostepuOtomoto):
        await _usluga_odpowiadajaca(kod).modele(BMW)


async def test_blad_transportu_dotyczy_calego_otomoto() -> None:
    def obsluz(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("brak trasy", request=request)

    usluga = Otomoto(klient=httpx.AsyncClient(transport=httpx.MockTransport(obsluz)))
    with pytest.raises(BladDostepuOtomoto):
        await usluga.modele(BMW)


async def test_404_dotyczy_tylko_tej_aukcji() -> None:
    with pytest.raises(BladOtomoto) as blad:
        await _usluga_odpowiadajaca(404).modele(BMW)
    assert not isinstance(blad.value, BladDostepuOtomoto)


def test_strona_w_innym_ksztalcie_dotyczy_calego_otomoto() -> None:
    with pytest.raises(BladDostepuOtomoto):
        otomoto.wyniki("<html>strona bez danych</html>")


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
