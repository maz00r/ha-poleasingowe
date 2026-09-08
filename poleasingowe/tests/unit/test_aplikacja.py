"""Aplikacja wystawiana przez Ingress (SPEC.md §7.1)."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from fastapi.testclient import TestClient

from app.infrastructure.supervisor.options import Opcje
from app.interfaces.app import utworz_aplikacje

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


def _atrybut(html: str, tag: str, atrybut: str) -> str:
    dopasowanie = re.search(rf"<{tag}[^>]*\b{atrybut}=\"([^\"]+)\"", html)
    assert dopasowanie is not None, f"brak {tag}[{atrybut}]"
    return dopasowanie.group(1)


def test_przegladarka_trafia_do_statyk_przez_prefiks_bez_naglowka() -> None:
    """Odtwarza błąd: HTML działał, ale CSS i HTMX nie dochodziły.

    Aplikacja nie może polegać na ``X-Ingress-Path``. Liczy się adres, który
    przeglądarka wyliczy z publicznego URL-a dokumentu, ``<base>`` i względnego
    ``href``. Core i Supervisor zdejmą potem prefiks i podadzą aplikacji zwykłe
    ``/static/...``.
    """
    publiczny_dokument = "https://ha.test/api/hassio_ingress/abc123/diagnostyka"
    with klient() as c:
        strona = c.get("/diagnostyka")  # celowo bez X-Ingress-Path

    baza = urljoin(publiczny_dokument, _atrybut(strona.text, "base", "href"))
    styl = urljoin(baza, _atrybut(strona.text, "link", "href"))
    skrypt = urljoin(baza, _atrybut(strona.text, "script", "src"))

    prefiks = "https://ha.test/api/hassio_ingress/abc123/"
    assert styl == prefiks + "static/styl.css"
    assert skrypt == prefiks + "static/htmx.min.js"


def test_base_z_karty_aukcji_wraca_do_korzenia_ingressu() -> None:
    """Zagnieżdżona karta potrzebuje ``../``, nie korzenia domeny."""
    publiczny_dokument = "https://ha.test/api/hassio_ingress/abc123/aukcja/7"
    with klient() as c:
        strona = c.get("/aukcja/7")
    assert strona.status_code == 503  # brak bazy, ale pełny szkielet strony działa
    baza = urljoin(publiczny_dokument, _atrybut(strona.text, "base", "href"))
    assert baza == "https://ha.test/api/hassio_ingress/abc123/"


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
    wewnetrzny_host = "172.30.33.5:8099"
    with klient() as c:
        odp = c.get("/diagnostyka", headers={"host": wewnetrzny_host})

    assert odp.status_code == 200
    assert (
        wewnetrzny_host not in odp.text
    ), "adres z wewnętrznym hostem kontenera — przeglądarka tam nie trafi"
    assert "http://testserver" not in odp.text
    assert 'href="static/styl.css"' in odp.text
    assert 'src="static/htmx.min.js"' in odp.text
    assert 'href="/static' not in odp.text
    assert 'src="/static' not in odp.text
