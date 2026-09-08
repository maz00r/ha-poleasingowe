"""Aplikacja wystawiana przez Ingress (SPEC.md §7.1)."""

from __future__ import annotations

from fastapi import Request
from fastapi.testclient import TestClient

from app.infrastructure.supervisor.options import Opcje
from app.interfaces.app import utworz_aplikacje
from app.interfaces.web.ingress import NAGLOWEK_INGRESS

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


def test_prefiks_ingressu_nie_trafia_do_root_path() -> None:
    """Scope zostaje NIETKNIĘTY — i to jest cała poprawka (SPEC.md §7.1).

    Wpisywanie prefiksu do `scope["root_path"]` wygląda naturalnie i tak to
    tu początkowo działało. Łamie jednak umowę ASGI: `root_path` ma być
    **początkiem** `scope["path"]`, a Home Assistant prefiks już zdjął.
    Starlette 0.38 liczy trasę jako `path` minus `root_path` i przekazuje
    `root_path` do podaplikacji, więc `StaticFiles` szukał pliku pod
    `<katalog>/static/styl.css` i oddawał **404 na CSS i na HTMX-a**, choć
    zwykłe trasy odpowiadały normalnie.

    Objaw był myląco szeroki: „GUI nie działa" — strona bez stylów,
    gwiazdka obserwacji martwa, bo HTMX-a w ogóle nie było.
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
    assert zapamietane == [""], "scope ma zostać nietknięty"


def test_pliki_statyczne_dzialaja_takze_pod_ingressem() -> None:
    """Dokładnie ta regresja, która zepsuła panel.

    `/static/styl.css` oddawało 404, gdy w żądaniu był nagłówek Ingressu,
    a 200 bez niego. Żaden test tego nie sprawdzał, bo wszystkie pytały
    o statyki **bez** nagłówka.
    """
    prefiks = "/api/hassio_ingress/abc123"
    with klient() as c:
        for sciezka in ("/static/styl.css", "/static/htmx.min.js"):
            bez = c.get(sciezka)
            z_ingressem = c.get(sciezka, headers={NAGLOWEK_INGRESS: prefiks + "/"})
            assert bez.status_code == 200, sciezka
            assert z_ingressem.status_code == 200, f"{sciezka} pod Ingressem"
            assert bez.content == z_ingressem.content


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


def test_bez_bazy_strona_mowi_dlaczego_zamiast_wywalac_sie() -> None:
    """SPEC.md §2 pkt 8, §12 — brak bazy to komunikat, nie crash ani 500.

    Po restarcie Home Assistanta Postgres bywa wolniejszy niż add-on. Ten
    stan jest normalny i ma być czytelny z interfejsu.
    """
    with klient() as c:
        odp = c.get("/")
    assert odp.status_code == 503
    assert "Baza jest niedostępna" in odp.text
    assert "poleasingowe_app@db21ed7f-postgres-latest" in odp.text
    assert "tajne-haslo" not in odp.text, "hasło nie ma prawa wyciec na stronę błędu"


def test_panel_diagnostyczny_dziala_takze_bez_bazy() -> None:
    """To jedyna strona, która ma sens akurat wtedy, gdy bazy nie ma."""
    with klient() as c:
        odp = c.get("/diagnostyka")
    assert odp.status_code == 200
    assert "Stan procesu" in odp.text
    assert "niedostępna" in odp.text


def test_htmx_jedzie_z_lokalnego_pliku_a_nie_z_cdn() -> None:
    """SPEC.md §12 — żadnych CDN-ów."""
    with klient() as c:
        strona = c.get("/diagnostyka")
        skrypt = c.get("/static/htmx.min.js")
    assert skrypt.status_code == 200
    assert skrypt.headers["content-type"].startswith("text/javascript")
    assert "cdn" not in strona.text.lower()
    assert "//unpkg" not in strona.text


def test_styl_jest_serwowany() -> None:
    with klient() as c:
        odp = c.get("/static/styl.css")
    assert odp.status_code == 200
    assert odp.headers["content-type"].startswith("text/css")


def test_wszystkie_adresy_w_stronie_maja_prefiks_ingressu() -> None:
    """SPEC.md §7.1 — ścieżka na sztywno prowadzi donikąd po instalacji."""
    prefiks = "/api/hassio_ingress/abc123"
    with klient() as c:
        odp = c.get("/diagnostyka", headers={NAGLOWEK_INGRESS: prefiks + "/"})
    assert odp.status_code == 200
    for adres in ('href="/static', 'src="/static', 'href="/diagnostyka'):
        assert adres not in odp.text, f"{adres} pomija prefiks Ingressu"
    assert f"{prefiks}/static/styl.css" in odp.text


def test_adresy_sa_wzgledne_wobec_origin_a_nie_bezwzgledne() -> None:
    """Najdroższy błąd tego interfejsu — cały panel wyglądał na zepsuty.

    `url_for` buduje adres BEZWZGLĘDNY i bierze host z żądania widzianego
    przez add-on, czyli wewnętrzny adres kontenera (`172.30.33.5:8099`).
    Home Assistant proxuje Ingress i nie przekazuje zewnętrznego hosta, więc
    przeglądarka dostawała odsyłacze do hosta, do którego nie ma dostępu:
    arkusz stylów się nie wczytywał, HTMX też — a bez HTMX-a gwiazdka
    obserwacji i doładowanie kolejnej strony po prostu nic nie robiły.

    Objawiało się to jako „GUI nie działa", więc test celuje w przyczynę:
    w wygenerowanej stronie nie ma prawa być adresu z naszym własnym hostem.
    """
    prefiks = "/api/hassio_ingress/abc123"
    wewnetrzny_host = "172.30.33.5:8099"
    with klient() as c:
        odp = c.get(
            "/diagnostyka",
            headers={NAGLOWEK_INGRESS: prefiks + "/", "host": wewnetrzny_host},
        )

    assert odp.status_code == 200
    assert (
        wewnetrzny_host not in odp.text
    ), "adres z wewnętrznym hostem kontenera — przeglądarka tam nie trafi"
    assert "http://testserver" not in odp.text
    # Ścieżka zaczynająca się od `/` rozwiązuje się względem origin strony.
    assert f'href="{prefiks}/static/styl.css"' in odp.text
    assert f'src="{prefiks}/static/htmx.min.js"' in odp.text
