"""Wycena AI: format żądania, walidacja odpowiedzi i cache."""

from __future__ import annotations

import json
import pathlib

import httpx

from app.application.read_models import PorownanieRynkowe, PozycjaListy, Szczegoly
from app.domain.enums import AuctionStatus
from app.infrastructure.wycena_ai import WycenaAI


def _dane() -> Szczegoly:
    return Szczegoly(
        pozycja=PozycjaListy(
            id=7,
            source_key="efl",
            external_id="tajny-identyfikator",
            url="https://example.test/7",
            status=AuctionStatus.ACTIVE,
            make="Škoda",
            model="Octavia",
            variant="Style",
            year=2022,
            mileage_km=120_000,
            fuel="Diesel",
            gearbox="Automatyczna",
        ),
        vin="TMBTA7NE0N0123456",
        seller="Jan Kowalski",
        engine_ccm=1968,
        engine_hp=150,
        body="Kombi",
    )


async def test_wycena_ma_strukturalny_wynik_i_nie_wysyla_vin(
    tmp_path: pathlib.Path,
) -> None:
    zadania: list[dict[str, object]] = []

    def odpowiedz(request: httpx.Request) -> httpx.Response:
        zadania.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "wartosc": 78000,
                                        "minimum": 72000,
                                        "maksimum": 84000,
                                        "pewnosc": "średnia",
                                        "uzasadnienie": "Dwie podobne aukcje.",
                                        "zalozenia": ["Brak poważnych szkód."],
                                    }
                                ),
                            }
                        ],
                    }
                ]
            },
        )

    klient = httpx.AsyncClient(transport=httpx.MockTransport(odpowiedz))
    usluga = WycenaAI(
        "sekretny-klucz", model="model-testowy", katalog=tmp_path, klient=klient
    )
    porownania = (PorownanieRynkowe(2022, 77_000, 2, None, 0),)

    wynik = await usluga.wycen(_dane(), porownania)
    ponownie = await usluga.wycen(_dane(), porownania)

    assert wynik.wartosc == 78_000
    assert ponownie == wynik
    assert len(zadania) == 1, "drugi odczyt powinien trafić do cache'u"
    wyslane = json.dumps(zadania, ensure_ascii=False)
    assert "TMBTA7NE0N0123456" not in wyslane
    assert "Jan Kowalski" not in wyslane
    assert zadania[0]["store"] is False
    assert zadania[0]["text"]["format"]["type"] == "json_schema"  # type: ignore[index]

    await klient.aclose()
