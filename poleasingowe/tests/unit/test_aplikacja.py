"""Aplikacja wystawiana przez Ingress (SPEC.md §7.1)."""

from __future__ import annotations

from fastapi import Request
from fastapi.testclient import TestClient

from app.infrastructure.supervisor.options import Opcje
from app.interfaces.app import NAGLOWEK_INGRESS, utworz_aplikacje

OPCJE = Opcje(
    db_password="tajne-haslo",
    sources=[{"key": "efl"}, {"key": "leasygroup", "enabled": False}],  # type: ignore[list-item]
)


def klient() -> TestClient:
    return TestClient(utworz_aplikacje(OPCJE))


def test_zdrowie_odpowiada() -> None:
    with klient() as c:
        odp = c.get("/zdrowie")
    assert odp.status_code == 200
    assert odp.json()["baza"]["dostepna"] is False


def test_zdrowie_nie_ujawnia_hasla() -> None:
    """SPEC.md §10.2 — panel diagnostyczny pokazuje stan, nie poświadczenia."""
    with klient() as c:
        odp = c.get("/zdrowie")
    assert "tajne-haslo" not in odp.text
    assert odp.json()["baza"]["polaczenie"] == (
        "poleasingowe_app@db21ed7f-postgres-latest:5432/poleasingowe"
    )


def test_zdrowie_pokazuje_tylko_wlaczone_zrodla() -> None:
    with klient() as c:
        odp = c.get("/zdrowie")
    assert odp.json()["zrodla"] == ["efl"]


def test_prefiks_ingress_trafia_do_root_path() -> None:
    """SPEC.md §7.1 — prefiks jest dynamiczny i przychodzi w nagłówku.

    Home Assistant montuje add-on pod losowym prefiksem, innym po każdym
    restarcie, więc nie da się go skonfigurować z góry. Bez przepisania go
    do `root_path` `url_for` budowałby adresy prowadzące donikąd.
    """
    app = utworz_aplikacje(OPCJE)
    zapamietane: list[str] = []

    @app.get("/echo-prefiksu")
    async def echo(request: Request) -> dict[str, str]:
        # Adnotacja `Request` jest konieczna: bez niej FastAPI uzna parametr
        # za zapytanie w URL-u, odrzuci żądanie z kodem 422 i ciało trasy
        # nigdy się nie wykona.
        zapamietane.append(request.scope.get("root_path", ""))
        return {}

    with TestClient(app) as c:
        c.get(
            "/echo-prefiksu", headers={NAGLOWEK_INGRESS: "/api/hassio_ingress/abc123/"}
        )
    assert zapamietane == [
        "/api/hassio_ingress/abc123"
    ], "prefiks ma trafić do root_path, bez końcowego ukośnika"


def test_brak_naglowka_zostawia_pusty_prefiks() -> None:
    """Uruchomienie poza Ingressem ma działać, a nie wywalać się."""
    app = utworz_aplikacje(OPCJE)
    zapamietane: list[str] = []

    @app.get("/echo-prefiksu")
    async def echo(request: Request) -> dict[str, str]:
        # Adnotacja `Request` jest konieczna: bez niej FastAPI uzna parametr
        # za zapytanie w URL-u, odrzuci żądanie z kodem 422 i ciało trasy
        # nigdy się nie wykona.
        zapamietane.append(request.scope.get("root_path", ""))
        return {}

    with TestClient(app) as c:
        c.get("/echo-prefiksu")
    assert zapamietane == [""]


def test_dokumentacja_api_jest_wylaczona() -> None:
    """Ingress uwierzytelnia, ale i tak nie publikujemy schematu API."""
    with klient() as c:
        for sciezka in ("/docs", "/redoc", "/openapi.json"):
            assert c.get(sciezka).status_code == 404, sciezka
