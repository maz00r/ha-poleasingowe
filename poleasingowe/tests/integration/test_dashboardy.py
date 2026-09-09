"""Dashboardy Grafany kontra kształt widoków (SPEC.md §9, §14 pkt 11).

Widoki `reporting.*` są **kontraktem publicznym**, a dashboard to jedyny
konsument, którego nie widać z kodu Pythona. Rozjazd nie wywala niczego przy
starcie — objawia się pustym wykresem tygodnie później.

Dlatego test nie porównuje napisów, tylko **wykonuje zapytania dashboardów na
prawdziwej bazie**. Zapytanie z nieistniejącą kolumną nie ma prawa przejść.
"""

from __future__ import annotations

import json
import pathlib
import re

import psycopg
import pytest

from tests.conftest import wymaga_postgresa

pytestmark = wymaga_postgresa

KATALOG = pathlib.Path(__file__).resolve().parents[2] / "grafana" / "dashboardy"

# Zmienne szablonu Grafany podstawiamy wartościami tego samego typu, jaki
# wstawiłaby Grafana — inaczej sprawdzalibyśmy inne zapytanie niż to, które
# naprawdę leci do bazy.
PODSTAWIENIA = {
    "$auction_id": "1",
    "'$make'": "'Audi'",
    "'$model'": "'A4'",
}


def _zapytania() -> list[tuple[str, str, str]]:
    """(plik, tytuł panelu, SQL) dla każdego zapytania w każdym dashboardzie."""
    wynik: list[tuple[str, str, str]] = []
    for plik in sorted(KATALOG.glob("*.json")):
        dashboard = json.loads(plik.read_text(encoding="utf-8"))
        for panel in dashboard["panels"]:
            for cel in panel.get("targets", []):
                if cel.get("rawSql"):
                    wynik.append((plik.name, panel["title"], cel["rawSql"]))
    return wynik


def test_sa_jakies_dashboardy() -> None:
    """Bez tego reszta testów przechodziłaby na pustym zbiorze."""
    assert len(_zapytania()) >= 4


@pytest.mark.parametrize("plik,tytul,sql", _zapytania())
async def test_zapytanie_dashboardu_wykonuje_sie_na_widokach(
    pusta_baza: psycopg.AsyncConnection, plik: str, tytul: str, sql: str
) -> None:
    """`EXPLAIN` sprawdza nazwy kolumn i tabel, nie licząc niczego.

    Gdyby migracja zmieniła nazwę kolumny w `reporting.*`, to jest jedyne
    miejsce w projekcie, w którym boli od razu.
    """
    gotowe = sql
    for zmienna, wartosc in PODSTAWIENIA.items():
        gotowe = gotowe.replace(zmienna, wartosc)
    assert "$" not in gotowe, f"{plik}/{tytul}: niepodstawiona zmienna szablonu"

    async with pusta_baza.cursor() as cur:
        await cur.execute(f"EXPLAIN {gotowe}")


def test_uidy_zgadzaja_sie_z_linkami_w_interfejsie() -> None:
    """UID jest częścią umowy — karta aukcji linkuje do niego wprost.

    Zmiana UID-u nie psuje dashboardu, tylko **link z dodatku**, i to bez
    żadnego komunikatu: użytkownik dostaje pustą stronę Grafany.
    """
    szablony = pathlib.Path(__file__).resolve().parents[2] / "app/interfaces/templates"
    tresc = "\n".join(p.read_text(encoding="utf-8") for p in szablony.rglob("*.html"))
    linkowane = set(re.findall(r"/d/(poleasingowe-[a-z-]+)", tresc))
    assert linkowane, "interfejs nie linkuje do żadnego dashboardu"

    dostepne = {
        json.loads(p.read_text(encoding="utf-8"))["uid"] for p in KATALOG.glob("*.json")
    }
    assert linkowane <= dostepne, (
        f"interfejs linkuje do dashboardów, których nie ma w repo: "
        f"{sorted(linkowane - dostepne)}"
    )
