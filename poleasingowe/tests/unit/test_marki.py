"""Rozpoznawanie marki i modelu (SPEC.md §6.2).

Heurystyka wspólna dla adapterów, więc i testy są wspólne — serwisy sklejają
markę z modelem tak samo bezradnie.
"""

from __future__ import annotations

import pytest

from app.infrastructure.sources import marki


@pytest.mark.parametrize(
    "wejscie,oczekiwane",
    [
        ("Skoda  Superb  Style DSG", ("Skoda", "Superb", "Style DSG")),
        (
            "Audi A3 30 TDI S tronic Hatchback",
            ("Audi", "A3", "30 TDI S tronic Hatchback"),
        ),
        ("Volkswagen Passat", ("Volkswagen", "Passat", None)),
        ("Opel", ("Opel", None, None)),
        ("", (None, None, None)),
        # Marka dwuwyrazowa rozpoznawana z listy — bez niej "Land" trafiloby
        # w marke, a "Rover" w model.
        ("Land Rover Discovery Sport", ("Land Rover", "Discovery", "Sport")),
    ],
)
def test_podzial_marki_i_modelu(
    wejscie: str, oczekiwane: tuple[str | None, str | None, str | None]
) -> None:
    assert marki.podziel_marke_model(wejscie) == oczekiwane


def test_nieznana_marka_dwuwyrazowa_to_znane_ograniczenie() -> None:
    """Udokumentowany sposób, w jaki heurystyka zawodzi.

    Serwis nie oddziela marki od modelu niczym, czemu można zaufać
    (podwójna spacja jest tylko w części wartości), więc marka spoza listy
    `MARKI_DWUWYRAZOWE` rozjedzie się na markę i model. Test pilnuje, żeby to
    zachowanie było świadome, a nie odkryte kiedyś na produkcji.
    """
    assert marki.podziel_marke_model("Great Wall Haval") == ("Great", "Wall", "Haval")
    assert "great wall" not in marki.MARKI_DWUWYRAZOWE
