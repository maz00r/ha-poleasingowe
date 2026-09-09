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

import asyncio
import contextlib
import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import psycopg
import pytest

from app.domain.entities import OfertaUczestnika, PriceSnapshot
from app.domain.enums import Currency
from app.domain.value_objects import Money
from app.infrastructure.persistence.pula import KontekstPg
from app.infrastructure.persistence.queries import PgZapytania
from app.infrastructure.persistence.repositories import PgUnitOfWork
from app.infrastructure.supervisor.options import Opcje
from app.interfaces.app import utworz_aplikacje
from app.interfaces.web.widoki import CIASTECZKO_WIZYTY
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
        self._blokada = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def __call__(self) -> AsyncIterator[KontekstPg]:
        async with self._blokada:
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
    assert "Ford Focus" not in odp.text, "archiwum nie pokazuje nieobserwowanych"
    assert (
        "Toyota Yaris" in odp.text
    ), "zniknięta obserwowana aukcja też jest archiwalna"
    assert "CONFIRMED" in odp.text


async def test_paginacja_htmx_doklada_kolejne_kafelki(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Fragment ma zwracać SAME kafelki — trafia do środka siatki `.lista`."""
    await _dane(pusta_baza)
    pierwsza = await klient.get("/lista", params={"kursor": ""})
    assert "<html" not in pierwsza.text.lower(), "fragment nie jest cała stroną"
    assert pierwsza.text.lstrip().startswith('<article class="oferta')


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


async def test_karta_pokazuje_historie_ceny_biezacej(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Karta pokazuje przebieg CENY BIEŻĄCEJ i nic poza tym (SPEC.md §12).

    Tabela ofert EFL bywała tu wcześniej i wprowadzała w błąd: pokazywała
    kwoty, których nie da się ułożyć w chronologię licytacji, bo poniżej ceny
    bieżącej nikt zalicytować nie może. Dane zbieramy dalej (`app.offer`),
    ale na kartę nie wracają, dopóki nie wiadomo, co dokładnie znaczą.
    """
    identyfikatory = await _dane(pusta_baza)
    aukcja_id = identyfikatory["audi-za-godzine"]
    uow = PgUnitOfWork(pusta_baza)
    teraz = dt.datetime.now(dt.UTC)

    for minuty, kwota in ((30, "52800.00"), (2, "57210.00")):
        await uow.snapshot.zapisz_jesli_zmienil_sie(
            PriceSnapshot(
                auction_id=aukcja_id,
                ts=teraz - dt.timedelta(minutes=minuty),
                price=Money(Decimal(kwota), Currency.PLN),
                bid_count=2,
            )
        )

    odp = await klient.get(f"/aukcja/{aukcja_id}")

    assert odp.status_code == 200
    assert "Przebieg licytacji" in odp.text
    assert "57" in odp.text and "52" in odp.text, "obie ceny w historii"


async def test_oferty_widac_tylko_tam_gdzie_sa_chronologia_licytacji(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """SPEC.md §11.8 razem z RECON.md §3.5a — bramka po semantyce licznika.

    Dla `OFFERS` lista ofert jest przebiegiem licytacji i karta ją pokazuje.
    Dla `PARTICIPANTS` (EFL) nie jest — pokazana raz, wprowadzała w błąd,
    bo niższa kwota z późniejszą datą wygląda na przekłamane dane.
    """
    identyfikatory = await _dane(pusta_baza)
    aukcja_id = identyfikatory["audi-za-godzine"]
    uow = PgUnitOfWork(pusta_baza)
    teraz = dt.datetime.now(dt.UTC)
    await uow.oferta.zapisz_nowe(
        [
            OfertaUczestnika(
                auction_id=aukcja_id,
                uczestnik="u...k",
                amount=Money(Decimal(kwota), Currency.PLN),
                placed_at=teraz - dt.timedelta(minutes=minuty),
                first_seen_at=teraz,
                external_offer_id=identyfikator,
            )
            for kwota, minuty, identyfikator in (
                ("332400.00", 10, "896390"),
                ("332500.00", 2, "896397"),
            )
        ]
    )

    async with pusta_baza.cursor() as cur:
        await cur.execute("UPDATE app.source SET bid_count_semantics = 'PARTICIPANTS'")
    bez_ofert = await klient.get(f"/aukcja/{aukcja_id}")
    assert "332" not in bez_ofert.text, "przy PARTICIPANTS lista ofert się nie pokazuje"

    async with pusta_baza.cursor() as cur:
        await cur.execute("UPDATE app.source SET bid_count_semantics = 'OFFERS'")
    z_ofertami = await klient.get(f"/aukcja/{aukcja_id}")

    assert "Oferty" in z_ofertami.text
    assert "332" in z_ofertami.text
    assert "najwyższa" in z_ofertami.text, "widać, która oferta prowadzi"


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
    assert "mieści się w limicie" in odp.text

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
        "Kopii jeszcze nie ma" in odp.text
    ), "brak kopii ma być widoczny jako brak, a nie przemilczany (§7.1)"


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


async def test_wiecej_filtrow_zostaje_zwiniete_ale_liczy_co_dziala(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Sekcja jest **zawsze** zwinięta, a mimo to nie ukrywa zawężenia.

    Otwieranie jej zależnie od zawartości sprawiało, że pasek filtrów skakał
    między zakładkami i nie dało się zapamiętać, gdzie co stoi. Ale zwinięty
    filtr, który cicho zawęża listę, to najgorszy rodzaj filtra — dlatego
    zamiast otwierać sekcję, pokazujemy przy niej licznik.
    """
    await _dane(pusta_baza)

    czysta = await klient.get("/")
    assert "open" not in czysta.text.split("wiecej-filtrow")[1][:40]

    z_filtrem = await klient.get("/", params={"przebieg_do": "150000"})
    assert "open" not in z_filtrem.text.split("wiecej-filtrow")[1][:40]
    podsumowanie = z_filtrem.text.split("wiecej-filtrow")[1][:260]
    assert "licznik" in podsumowanie, "zwinięty filtr ma się ogłosić licznikiem"


async def test_biezaca_zakladka_jest_wyrozniona(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Zgłoszenie z użytkowania: „nie wiem, którą zakładkę przeglądam".

    Podświetlenie liczy się ze **znormalizowanych kryteriów**, nie z napisu
    w adresie — dołożenie marki albo zmiana sortowania nie gasi go, bo to
    wciąż ta sama zakładka, tylko zawężona.
    """
    await _dane(pusta_baza)

    def aktywna(html: str) -> str:
        pasek = html.split("<nav>")[1].split("</nav>")[0]
        kawalki = [k for k in pasek.split("<a ") if 'class="aktywna"' in k]
        assert len(kawalki) == 1, "dokładnie jedna zakładka ma być wyróżniona"
        return kawalki[0].split(">")[-2].split("<")[0].strip()

    assert aktywna((await klient.get("/", params={"status": "aktywne"})).text) == (
        "Aktywne"
    )
    assert (
        aktywna(
            (
                await klient.get("/", params={"obserwowane": "1", "status": "aktywne"})
            ).text
        )
        == "Obserwowane"
    )
    # Zawezenie marka nie ma prawa zgasic podswietlenia.
    assert (
        aktywna(
            (
                await klient.get(
                    "/",
                    params={"obserwowane": "1", "status": "aktywne", "marka": "Audi"},
                )
            ).text
        )
        == "Obserwowane"
    )
    assert aktywna((await klient.get("/diagnostyka")).text) == "Diagnostyka"


async def test_eksport_csv_bierze_te_same_filtry_co_widok(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Eksport ma oddać to, co widać na ekranie — nie całą bazę.

    Plik z innym zakresem niż lista jest gorszy niż brak eksportu: nikt go
    nie sprawdzi, a wnioski wyciągnie.
    """
    await _dane(pusta_baza)
    odp = await klient.get("/eksport.csv", params={"marka": "Audi"})

    assert odp.status_code == 200
    assert "text/csv" in odp.headers["content-type"]
    assert "attachment" in odp.headers["content-disposition"]
    assert ".csv" in odp.headers["content-disposition"]

    linie = [w for w in odp.text.splitlines() if w.strip()]
    assert linie[0].startswith("﻿"), "BOM dla Excela"
    assert len(linie) == 3, "nagłówek i dwie Audi — reszta odfiltrowana"
    assert "Volkswagen" not in odp.text


async def test_znacznik_wizyty_nie_przesuwa_sie_przy_kazdym_kliknieciu(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Zgłoszenie z użytkowania: „nowe od ostatniej wizyty nie działa".

    Ciasteczko trzymało samo „teraz" i było nadpisywane przy **każdym**
    wyświetleniu listy — łącznie z tym, na którym stał ten filtr. Znacznik
    cofał się więc o kilka sekund przed samego siebie i widok był pusty
    zawsze, niezależnie od tego, ile aukcji naprawdę doszło.

    Test odtwarza dokładnie tę sekwencję: dwa wejścia pod rząd nie mają prawa
    ruszyć znacznika, bo to wciąż ta sama wizyta.
    """
    await _dane(pusta_baza)

    pierwsze = await klient.get("/", params={"status": "aktywne"})
    znacznik_1 = pierwsze.cookies[CIASTECZKO_WIZYTY].split("|")[0]

    drugie = await klient.get("/", params={"status": "aktywne", "nowe": "1"})
    znacznik_2 = drugie.cookies[CIASTECZKO_WIZYTY].split("|")[0]

    assert znacznik_1 == znacznik_2, "ta sama wizyta — znacznik stoi w miejscu"


async def test_nowa_wizyta_przesuwa_znacznik_na_koniec_poprzedniej(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Po przerwie „nowe" ma znaczyć „od końca poprzedniej wizyty".

    Nie „od teraz" — wtedy widok byłby pusty — i nie „od pierwszego wejścia
    kiedykolwiek", bo wtedy rósłby bez końca.
    """
    await _dane(pusta_baza)
    koniec_poprzedniej = dt.datetime.now(dt.UTC) - dt.timedelta(hours=3)
    dawno = koniec_poprzedniej - dt.timedelta(hours=1)

    odp = await klient.get(
        "/",
        params={"status": "aktywne"},
        cookies={
            CIASTECZKO_WIZYTY: f"{dawno.isoformat()}|{koniec_poprzedniej.isoformat()}"
        },
    )

    znacznik = dt.datetime.fromisoformat(odp.cookies[CIASTECZKO_WIZYTY].split("|")[0])
    assert znacznik == koniec_poprzedniej


async def test_przelacznik_stanu_stoi_w_widokach_zawezonych_i_niesie_filtry(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """SPEC.md §12 — „obserwowane" i „wystawione ponownie" dzielą się na
    trwające i wygasłe.

    Najważniejsze jest to, że przełączenie **nie gubi reszty filtrów**:
    inaczej klik w „Wygasłe" cichcem kasowałby zawężenie, po którym
    użytkownik tam trafił.
    """
    await _dane(pusta_baza)

    odp = await klient.get(
        "/", params={"obserwowane": "1", "status": "aktywne", "marka": "Audi"}
    )
    assert odp.status_code == 200
    assert "Wygasłe" in odp.text
    assert "marka=Audi" in odp.text, "przełącznik niesie komplet filtrów"

    zwykla = await klient.get("/", params={"status": "aktywne"})
    assert "Wygasłe" not in zwykla.text, "na zwykłej liście przełącznika nie ma"


class BudzikAtrapa:
    def __init__(self) -> None:
        self.pobudki = 0

    def obudz(self) -> None:
        self.pobudki += 1


async def test_wejscie_na_karte_prosi_o_swiezy_odczyt(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """SPEC.md §11.2 — otwarcie karty planuje odpyt i budzi pętlę.

    Sprawdzamy skutek w bazie (`next_poll_at`), a nie żądanie do serwisu —
    bo karta świadomie **nie** wysyła niczego sama. Prośba idzie przez
    dyspozytora i to on decyduje, czy kubełek tokenów na nią pozwala.
    """
    identyfikatory = await _dane(pusta_baza)
    aukcja_id = identyfikatory["audi-za-godzine"]
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "UPDATE app.auction SET last_seen_at = now() - interval '2 hours',"
            " next_poll_at = NULL WHERE id = %s",
            (aukcja_id,),
        )

    budzik = BudzikAtrapa()
    klient._transport.app.state.budzik = budzik  # type: ignore[attr-defined]
    odp = await klient.get(f"/aukcja/{aukcja_id}")

    assert odp.status_code == 200
    assert "Pobieram świeże dane" in odp.text
    assert budzik.pobudki == 1, "pętla ma zostać obudzona, a nie czekać na sen"

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT next_poll_at FROM app.auction WHERE id = %s", (aukcja_id,)
        )
        wiersz = await cur.fetchone()
    assert wiersz is not None and wiersz[0] is not None, "odpyt zaplanowany"


async def test_swieze_dane_nie_generuja_prosby(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Karta nie ma prawa generować ruchu gęstszego niż harmonogram.

    Odświeżanie z przeglądarki to jedyna ścieżka, w której o tempie decyduje
    człowiek — bez tego progu wciśnięty F5 byłby furtką dookoła §11.3.
    """
    identyfikatory = await _dane(pusta_baza)
    aukcja_id = identyfikatory["audi-za-godzine"]
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "UPDATE app.auction SET last_seen_at = now(), next_poll_at = NULL"
            " WHERE id = %s",
            (aukcja_id,),
        )

    budzik = BudzikAtrapa()
    klient._transport.app.state.budzik = budzik  # type: ignore[attr-defined]
    odp = await klient.get(f"/aukcja/{aukcja_id}")

    assert "Pobieram świeże dane" not in odp.text
    assert budzik.pobudki == 0


async def test_pasek_odswiezania_sam_sie_konczy(
    klient: httpx.AsyncClient, pusta_baza: psycopg.AsyncConnection
) -> None:
    """Po wyczerpaniu prób fragment wraca BEZ atrybutów HTMX.

    Decyzję podejmuje serwer, więc nie ma licznika w JavaScripcie, który
    trzeba by utrzymywać zgodny z regułą po stronie Pythona.
    """
    identyfikatory = await _dane(pusta_baza)
    aukcja_id = identyfikatory["audi-za-godzine"]
    od = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat()

    trwa = await klient.get(f"/aukcja/{aukcja_id}/karta", params={"od": od, "proba": 1})
    # Sam `hx-get` nie wystarcza jako sprawdzenie — galeria w tej samej karcie
    # tez go uzywa. Rozstrzyga klasa paska odswiezania.
    assert "odswiezanie" in trwa.text
    assert "Pobieram świeże dane" in trwa.text

    koniec = await klient.get(
        f"/aukcja/{aukcja_id}/karta", params={"od": od, "proba": 99}
    )
    assert "odswiezanie" not in koniec.text
    assert "Pobieram świeże dane" not in koniec.text
