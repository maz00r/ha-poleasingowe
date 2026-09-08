"""Interfejs od żądania do HTML-a, na realnej bazie (SPEC.md §12).

`httpx.ASGITransport` zamiast `TestClient`, bo `TestClient` kręci własną
pętlę zdarzeń w osobnym wątku, a połączenie z fikstury należy do pętli
pytest-asyncio. Mieszanie ich kończy się zawieszeniem, nie błędem — a to
najgorszy rodzaj testu.

Fabryka kontekstu podstawia to jedno połączenie zamiast puli. Pula ma własne
testy przy starcie procesu; tutaj sprawdzamy trasy, nie zarządzanie
połączeniami.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest

from app.infrastructure.persistence.pula import KontekstPg
from app.infrastructure.persistence.queries import PgZapytania
from app.infrastructure.persistence.repositories import PgUnitOfWork
from app.infrastructure.supervisor.options import Opcje
from app.interfaces.app import utworz_aplikacje
from tests.conftest import wymaga_postgresa
from tests.integration.test_zapytania import _dane

pytestmark = wymaga_postgresa

OPCJE = Opcje(
    db_password="tajne-haslo",
    grafana_base_url="https://grafana.example/",
)


class FabrykaNaPolaczeniu:
    """Fabryka kontekstu podstawiająca jedno połączenie z fikstury."""

    def __init__(self, conn: psycopg.AsyncConnection) -> None:
        self._conn = conn

    @contextlib.asynccontextmanager
    async def __call__(self) -> AsyncIterator[KontekstPg]:
        yield KontekstPg(
            uow=PgUnitOfWork(self._conn), zapytania=PgZapytania(self._conn)
        )

    async def zamknij(self) -> None:
        """Połączeniem zarządza fikstura, nie ten obiekt."""

    def stan_puli(self) -> dict[str, int]:
        return {"pool_size": 1, "pool_available": 1}


@pytest.fixture
async def klient(
    pusta_baza: psycopg.AsyncConnection,
) -> AsyncIterator[httpx.AsyncClient]:
    aplikacja = utworz_aplikacje(OPCJE, fabryka=FabrykaNaPolaczeniu(pusta_baza))
    transport = httpx.ASGITransport(app=aplikacja)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as klient_http:
        yield klient_http


async def test_lista_pokazuje_aukcje_i_zapamietuje_wizyte(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    await _dane(pusta_baza)
    odp = await klient.get("/")
    assert odp.status_code == 200
    assert "Audi A6" in odp.text
    assert "Opel Insignia" not in odp.text, "domyślnie tylko aktywne"
    # Znacznik wizyty musi zostać ustawiony, inaczej widok „nowe od
    # ostatniej wizyty" nigdy nie miałby punktu odniesienia (§12).
    assert "poleasingowe_ostatnia_wizyta" in odp.cookies
    assert "miniatura=1" in odp.text


async def test_filtr_z_adresu_dziala_na_liscie(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    await _dane(pusta_baza)
    odp = await klient.get("/", params={"marka": "Volkswagen"})
    assert "Volkswagen Passat" in odp.text
    assert "Audi A6" not in odp.text


async def test_archiwum_pokazuje_znacznik_pewnosci_ceny(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """SPEC.md §12 — cena końcowa bez znacznika pewności wprowadza w błąd."""
    await _dane(pusta_baza)
    odp = await klient.get("/", params={"status": "zakonczone"})
    assert "Opel Insignia" in odp.text
    assert "CONFIRMED" in odp.text


async def test_paginacja_htmx_doklada_kolejne_wiersze(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Fragment ma zwracać SAME wiersze — trafia do środka `<tbody>`."""
    await _dane(pusta_baza)
    pierwsza = await klient.get("/lista", params={"kursor": ""})
    assert "<html" not in pierwsza.text.lower(), "fragment nie jest cała stroną"
    assert pierwsza.text.lstrip().startswith("<tr")


async def test_szczegoly_linkuja_do_oferty_i_do_grafany(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """SPEC.md §12 — link do dashboardu z `?var-auction_id=`, bez wykresu."""
    identyfikatory = await _dane(pusta_baza)
    odp = await klient.get(f"/aukcja/{identyfikatory['audi-za-godzine']}")
    assert odp.status_code == 200
    assert "WAUZZZ4G7KN123456" in odp.text
    assert "https://przyklad.test/audi-za-godzine" in odp.text
    assert "Wycena AI" in odp.text
    assert f"aukcja/{identyfikatory['audi-za-godzine']}/wycena" in odp.text
    assert (
        f"https://grafana.example/d/poleasingowe-aukcja?var-auction_id="
        f"{identyfikatory['audi-za-godzine']}" in odp.text
    )


async def test_nieistniejaca_aukcja_daje_404_a_nie_500(
    klient: httpx.AsyncClient,
) -> None:
    odp = await klient.get("/aukcja/999999")
    assert odp.status_code == 404
    assert "Nie ma takiej aukcji" in odp.text


async def test_obserwowanie_zapisuje_notatke_i_prog(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """SPEC.md §12 — dodaj/usuń, notatka, cena docelowa, wyróżnienie progu."""
    identyfikatory = await _dane(pusta_baza)
    identyfikator = identyfikatory["audi-za-tydzien"]

    odp = await klient.post(
        f"/aukcja/{identyfikator}/obserwuj",
        data={"notatka": "sprawdzić opony", "cena_docelowa": "95 000,50"},
    )
    assert odp.status_code == 200
    assert "sprawdzić opony" in odp.text
    assert "Przestań obserwować" in odp.text
    # 90 000 zł jest poniżej progu 95 000,50 zł — wyróżnienie ma się zapalić.
    assert "poniżej progu" in odp.text

    async with FabrykaNaPolaczeniu(pusta_baza)() as kontekst:
        wpis = await kontekst.uow.watchlist.wpis(identyfikator)
    assert wpis is not None
    assert wpis.note == "sprawdzić opony"
    assert wpis.target_price is not None
    assert str(wpis.target_price.amount) == "95000.50"


async def test_zaprzestanie_obserwacji_usuwa_wpis(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    identyfikatory = await _dane(pusta_baza)
    identyfikator = identyfikatory["vw-za-dwie-godziny"]

    odp = await klient.post(f"/aukcja/{identyfikator}/przestan-obserwowac")
    assert odp.status_code == 200
    assert "Przestań obserwować" not in odp.text

    async with FabrykaNaPolaczeniu(pusta_baza)() as kontekst:
        assert not await kontekst.uow.watchlist.obserwowana(identyfikator)


async def test_bezsensowna_cena_docelowa_nie_wywala_zapisu(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Wpisane „tanio" ma znaczyć „bez progu", a nie zerwać obserwację."""
    identyfikatory = await _dane(pusta_baza)
    identyfikator = identyfikatory["audi-za-tydzien"]

    odp = await klient.post(
        f"/aukcja/{identyfikator}/obserwuj",
        data={"notatka": "", "cena_docelowa": "tanio"},
    )
    assert odp.status_code == 200

    async with FabrykaNaPolaczeniu(pusta_baza)() as kontekst:
        wpis = await kontekst.uow.watchlist.wpis(identyfikator)
    assert wpis is not None and wpis.target_price is None


async def test_zapisany_filtr_wraca_na_liste(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """SPEC.md §12 — zapisane filtry."""
    await _dane(pusta_baza)
    odp = await klient.post(
        "/filtry?marka=Audi&sort=cena", data={"nazwa": "Audi po cenie"}
    )
    assert odp.status_code == 303

    lista = await klient.get("/")
    assert "Audi po cenie" in lista.text
    assert "marka=Audi" in lista.text


async def test_panel_diagnostyczny_pokazuje_zrodla_i_pule(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    await _dane(pusta_baza)
    odp = await klient.get("/diagnostyka")
    assert odp.status_code == 200
    assert "EFL" in odp.text
    assert "pool_size" in odp.text
    assert (
        "Backup jeszcze nie istnieje" in odp.text
    ), "brak backupu ma być widoczny jako brak, a nie przemilczany (§14 pkt 11)"


async def test_odblokowanie_zrodla_zeruje_licznik_i_nie_daje_od_razu_ok(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """SPEC.md §10.2 — po resecie sesji jeszcze nie ma, więc `EXPIRED`."""
    from dataclasses import replace

    from app.domain.enums import AuthState

    await _dane(pusta_baza)
    async with FabrykaNaPolaczeniu(pusta_baza)() as kontekst:
        zrodlo = await kontekst.uow.source.po_kluczu("efl")
        assert zrodlo is not None
        await kontekst.uow.source.zapisz(
            replace(zrodlo, auth_state=AuthState.LOCKED, consecutive_auth_failures=3)
        )

    odp = await klient.post("/zrodlo/efl/odblokuj")
    assert odp.status_code == 303

    async with FabrykaNaPolaczeniu(pusta_baza)() as kontekst:
        po_resecie = await kontekst.uow.source.po_kluczu("efl")
    assert po_resecie is not None
    assert po_resecie.auth_state is AuthState.EXPIRED
    assert po_resecie.consecutive_auth_failures == 0


async def test_gwiazdka_na_liscie_przelacza_obserwacje_i_odpyt(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """SPEC.md §12 — decyzja „obserwuję to" zapada przy przeglądaniu listy.

    Razem z obserwacją włącza się pojedynczy odpyt (§11.2): bez tego
    watchlista byłaby etykietą, a nie zmianą zachowania.
    """
    identyfikatory = await _dane(pusta_baza)
    identyfikator = identyfikatory["audi-za-tydzien"]

    odp = await klient.post(f"/aukcja/{identyfikator}/przelacz")
    assert odp.status_code == 200
    assert "★" in odp.text
    assert 'aria-pressed="true"' in odp.text

    async with FabrykaNaPolaczeniu(pusta_baza)() as kontekst:
        assert await kontekst.uow.watchlist.obserwowana(identyfikator)
        dane = await kontekst.zapytania.szczegoly(identyfikator)
    assert dane is not None and dane.next_poll_at is not None

    odp = await klient.post(f"/aukcja/{identyfikator}/przelacz")
    assert "☆" in odp.text
    async with FabrykaNaPolaczeniu(pusta_baza)() as kontekst:
        assert not await kontekst.uow.watchlist.obserwowana(identyfikator)
        dane = await kontekst.zapytania.szczegoly(identyfikator)
    assert dane is not None and dane.next_poll_at is None


async def test_pusta_lista_rozroznia_brak_danych_od_filtrow(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Te dwie sytuacje wymagają od użytkownika zupełnie różnych działań.

    „Jeszcze nic nie zebrano" znaczy „poczekaj albo sprawdź diagnostykę".
    „Nic nie pasuje" znaczy „popraw filtry". Jeden komunikat na oba przypadki
    kazałby zgadywać, który to.
    """
    pusta = await klient.get("/")
    assert "Jeszcze nic nie zebrano" in pusta.text

    await _dane(pusta_baza)
    bez_trafien = await klient.get("/", params={"marka": "Trabant"})
    assert "Nic nie pasuje do tych filtrów" in bez_trafien.text
    assert "Jeszcze nic nie zebrano" not in bez_trafien.text


async def test_naglowek_kolumny_odwraca_kierunek_sortowania(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Klik w aktywną kolumnę ma odwracać kierunek, a filtry mają zostać."""
    await _dane(pusta_baza)
    odp = await klient.get("/", params={"marka": "Audi", "sort": "cena"})

    assert (
        "sort=cena-desc" in odp.text
    ), "aktywna kolumna prowadzi do odwrotnego kierunku"
    assert "marka=Audi" in odp.text, "sortowanie nie ma prawa gubić filtrów"


async def test_wiecej_filtrow_otwiera_sie_gdy_cos_w_srodku_dziala(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Zwinięty filtr, który cicho zawęża listę, to najgorszy rodzaj filtra."""
    await _dane(pusta_baza)

    zwiniete = await klient.get("/")
    assert '<details class="wiecej-filtrow" >' in zwiniete.text.replace("  ", " ")

    rozwiniete = await klient.get("/", params={"przebieg_do": "150000"})
    assert "open" in rozwiniete.text.split("wiecej-filtrow")[1][:40]
