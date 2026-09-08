"""Dispatcher na realnej bazie (SPEC.md §11.1, §14 pkt 9).

„Weryfikacja endgame na sztucznej aukcji z `ends_at` za 20 minut" — dokładnie
to robi `test_endgame_na_sztucznej_aukcji`. Reszta pilnuje rzeczy, które
w produkcji ujawniłyby się dopiero po tygodniach: taniego odpytu, reguły
zapisu snapshotów i izolacji awarii źródła.

Adapter jest atrapą, bo test ma sprawdzać **pętlę**, a nie serwis po drugiej
stronie sieci. Parsery mają własne testy na fixtures.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import psycopg
import pytest

from app.application.ports import SurowaOferta
from app.domain.entities import Auction, Source, WatchlistEntry
from app.domain.enums import AuctionStatus, AuthState, Currency, PollTier
from app.domain.errors import SourceUnavailable
from app.domain.value_objects import Money
from app.infrastructure.persistence.repositories import PgUnitOfWork
from app.infrastructure.scheduler.dispatcher import MAKS_SEN_S, Dispatcher
from tests.conftest import wymaga_postgresa
from tests.integration.test_interfejs_e2e import FabrykaNaPolaczeniu

pytestmark = wymaga_postgresa


class ZrodloAtrapa:
    """Atrapa adaptera: oddaje z góry ustawione odczyty, liczy wywołania."""

    def __init__(self, key: str = "atrapa") -> None:
        self.key = key
        self.cena = Decimal("40000")
        self.ends_at: dt.datetime | None = None
        self.hash_tresci = "hash-1"
        self.blad: Exception | None = None
        self.pobrania = 0
        self.sparsowane = 0
        self.przemiaty = 0
        self.podane_hashe: list[str | None] = []
        self.na_liscie: list[str] = []
        self.ends_at_na_liscie: dt.datetime | None = None

    async def przemiec_liste(self) -> list[SurowaOferta]:
        self.przemiaty += 1
        if self.blad is not None:
            raise self.blad
        return [
            SurowaOferta(
                external_id=eid,
                url=f"https://atrapa.test/{eid}",
                pola={"z_listy": "1"},
            )
            for eid in self.na_liscie
        ]

    async def pobierz_szczegoly(
        self, external_id: str, znany_hash: str | None = None
    ) -> SurowaOferta | None:
        self.pobrania += 1
        self.podane_hashe.append(znany_hash)
        if self.blad is not None:
            raise self.blad
        if znany_hash is not None and znany_hash == self.hash_tresci:
            # SPEC.md §11.3 krok 2 — treść bez zmian, parsowania nie ma.
            return None
        self.sparsowane += 1
        return SurowaOferta(
            external_id=external_id,
            url=f"https://atrapa.test/{external_id}",
            pola={},
            content_hash=self.hash_tresci,
        )

    def na_aukcje(
        self, surowa: SurowaOferta, source_id: int, teraz: dt.datetime
    ) -> Auction:
        # Pozycja z listy wie mniej niż strona szczegółów — dokładnie tak,
        # jak poleasingowe.pl, gdzie lista nie podaje godziny zakończenia.
        z_listy = surowa.pola.get("z_listy") == "1"
        return Auction(
            source_id=source_id,
            external_id=surowa.external_id,
            url=surowa.url,
            status=AuctionStatus.ACTIVE,
            first_seen_at=teraz,
            last_seen_at=teraz,
            price_current=Money(self.cena, Currency.PLN),
            bid_count=1,
            ends_at=self.ends_at_na_liscie if z_listy else self.ends_at,
            content_hash=surowa.content_hash,
        )


def zrodlo(key: str, **nadpisz: object) -> Source:
    dane: dict[str, object] = {
        "key": key,
        "name": key.upper(),
        "enabled": True,
        "sweep_interval_seconds": 21_600,
        "rate_limit_per_minute": 600,  # bez czekania — tempo ma własne testy
        "floor_seconds": 15,
        "auth_state": AuthState.ANONYMOUS,
        "consecutive_auth_failures": 0,
        # poleasingowe.pl: okno 30 s, +30 s, sufit 30 min (RECON.md §3.2)
        "overtime_window_seconds": 30,
        "overtime_extension_seconds": 30,
        "overtime_cap_seconds": 1800,
    }
    dane.update(nadpisz)
    return Source(**dane)  # type: ignore[arg-type]


async def przygotuj(
    baza: psycopg.AsyncConnection, adapter: ZrodloAtrapa, do_konca_min: float
) -> tuple[int, dt.datetime]:
    """Zakłada źródło i jedną aukcję zaległą do odpytu. Zwraca id i `ends_at`."""
    uow = PgUnitOfWork(baza)
    async with uow:
        zapisane = await uow.source.zapisz(zrodlo(adapter.key))
        assert zapisane.id is not None
        teraz = await _czas_bazy(baza)
        koniec = teraz + dt.timedelta(minutes=do_konca_min)
        adapter.ends_at = koniec
        aukcja = await uow.auction.zapisz(
            Auction(
                source_id=zapisane.id,
                external_id="sztuczna-1",
                url="https://atrapa.test/sztuczna-1",
                status=AuctionStatus.ACTIVE,
                first_seen_at=teraz - dt.timedelta(days=1),
                last_seen_at=teraz - dt.timedelta(minutes=5),
                price_current=Money(Decimal("40000"), Currency.PLN),
                bid_count=1,
                ends_at=koniec,
                content_hash=None,
                next_poll_at=teraz - dt.timedelta(seconds=1),
                poll_tier=PollTier.NEAR,
            )
        )
        # Aukcja OBSERWOWANA — tylko takie są odpytywane pojedynczo w kółko
        # (SPEC.md §11.2). Nieobserwowana dostaje jeden odpyt po odkryciu
        # i wraca do trybu „wystarcza przemiat listy".
        assert aukcja.id is not None
        await uow.watchlist.dodaj(WatchlistEntry(auction_id=aukcja.id, added_at=teraz))
    assert aukcja.id is not None
    return aukcja.id, koniec


async def _czas_bazy(baza: psycopg.AsyncConnection) -> dt.datetime:
    async with baza.cursor() as cur:
        await cur.execute("SELECT now()")
        wiersz = await cur.fetchone()
    assert wiersz is not None
    czas: dt.datetime = wiersz[0]
    return czas


def dispatcher(baza: psycopg.AsyncConnection, *adaptery: ZrodloAtrapa) -> Dispatcher:
    return Dispatcher(
        FabrykaNaPolaczeniu(baza),
        {a.key: a for a in adaptery},
    )


async def wczytaj(baza: psycopg.AsyncConnection, auction_id: int) -> Auction:
    uow = PgUnitOfWork(baza)
    aukcja = await uow.auction.po_kluczu_naturalnym(
        (await _pierwsze_zrodlo(baza)), "sztuczna-1"
    )
    assert aukcja is not None and aukcja.id == auction_id
    return aukcja


async def _pierwsze_zrodlo(baza: psycopg.AsyncConnection) -> int:
    async with baza.cursor() as cur:
        await cur.execute("SELECT id FROM app.source ORDER BY id LIMIT 1")
        wiersz = await cur.fetchone()
    assert wiersz is not None
    identyfikator: int = wiersz[0]
    return identyfikator


# --------------------------------------------------------------------------
# SPEC.md §14 pkt 9 — weryfikacja endgame na sztucznej aukcji
# --------------------------------------------------------------------------


async def test_endgame_na_sztucznej_aukcji(pusta_baza: psycopg.AsyncConnection) -> None:
    """Aukcja kończąca się za 20 minut przechodzi przez progi §11.2.

    20 min → tier NEAR i odpyt co 3 min. Po skróceniu do 10 min → ENDGAME
    i floor źródła, czyli 15 s dla okna dogrywki 30 s.
    """
    adapter = ZrodloAtrapa()
    auction_id, _ = await przygotuj(pusta_baza, adapter, do_konca_min=20)

    disp = dispatcher(pusta_baza, adapter)
    await disp.jeden_obrot()

    po_pierwszym = await wczytaj(pusta_baza, auction_id)
    assert adapter.pobrania == 1
    assert po_pierwszym.poll_tier is PollTier.NEAR
    odstep = (po_pierwszym.next_poll_at - po_pierwszym.last_seen_at).total_seconds()  # type: ignore[operator]
    assert odstep == pytest.approx(180, abs=2), "20 min do końca to próg 3 minut"

    # Przesuwamy koniec na 10 minut — wchodzimy w endgame.
    teraz = await _czas_bazy(pusta_baza)
    adapter.ends_at = teraz + dt.timedelta(minutes=10)
    adapter.hash_tresci = "hash-2"
    async with PgUnitOfWork(pusta_baza) as uow:
        await uow.auction.zapisz(
            replace(po_pierwszym, next_poll_at=teraz - dt.timedelta(seconds=1))
        )

    await disp.jeden_obrot()

    w_endgame = await wczytaj(pusta_baza, auction_id)
    assert w_endgame.poll_tier is PollTier.ENDGAME
    odstep = (w_endgame.next_poll_at - w_endgame.last_seen_at).total_seconds()  # type: ignore[operator]
    assert odstep == pytest.approx(15, abs=2), "floor = połowa okna dogrywki 30 s"


async def test_dogrywka_przesuwa_termin_i_jest_widoczna_w_snapshotach(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.4 — przesunięcie `ends_at` to nie błąd parsowania.

    Aukcja `9mjrl4k9` przeszła z 12:00 na 12:18 (RECON.md §3.7). Świeży
    odczyt ma wygrywać z wartością w bazie, a zmiana `ends_at` sama w sobie
    jest powodem do zapisania snapshotu (§8.4).
    """
    adapter = ZrodloAtrapa()
    auction_id, koniec = await przygotuj(pusta_baza, adapter, do_konca_min=1)
    disp = dispatcher(pusta_baza, adapter)
    await disp.jeden_obrot()

    przedluzony = koniec + dt.timedelta(minutes=18)
    adapter.ends_at = przedluzony
    adapter.hash_tresci = "hash-po-dogrywce"
    teraz = await _czas_bazy(pusta_baza)
    async with PgUnitOfWork(pusta_baza) as uow:
        biezaca = await wczytaj(pusta_baza, auction_id)
        await uow.auction.zapisz(
            replace(biezaca, next_poll_at=teraz - dt.timedelta(seconds=1))
        )

    await disp.jeden_obrot()

    po = await wczytaj(pusta_baza, auction_id)
    assert po.ends_at == przedluzony, "świeży odczyt wygrywa z bazą"

    async with PgUnitOfWork(pusta_baza) as uow:
        historia = await uow.snapshot.historia(auction_id)
    assert [s.ends_at for s in historia][-1] == przedluzony


# --------------------------------------------------------------------------
# Tani odpyt i reguła zapisu snapshotów
# --------------------------------------------------------------------------


async def test_niezmieniona_tresc_nie_jest_parsowana_ani_zapisywana(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.3 krok 2 i §8.4 razem.

    Parsowanie jest najdroższą operacją CPU w aplikacji, a identyczny
    snapshot przy dogrywce potrafi wygenerować setki wierszy na aukcję —
    na bazie dzielonej z TeslaMate.
    """
    adapter = ZrodloAtrapa()
    auction_id, _ = await przygotuj(pusta_baza, adapter, do_konca_min=20)
    disp = dispatcher(pusta_baza, adapter)

    await disp.jeden_obrot()
    assert adapter.sparsowane == 1
    assert adapter.podane_hashe == [None], "pierwszy odpyt nie ma czego porównać"

    for _ in range(3):
        teraz = await _czas_bazy(pusta_baza)
        async with PgUnitOfWork(pusta_baza) as uow:
            biezaca = await wczytaj(pusta_baza, auction_id)
            await uow.auction.zapisz(
                replace(biezaca, next_poll_at=teraz - dt.timedelta(seconds=1))
            )
        await disp.jeden_obrot()

    assert adapter.pobrania == 4, "pobieramy za każdym razem — hash liczy adapter"
    assert adapter.sparsowane == 1, "ale parsujemy tylko raz"
    assert adapter.podane_hashe[1:] == ["hash-1"] * 3

    async with PgUnitOfWork(pusta_baza) as uow:
        historia = await uow.snapshot.historia(auction_id)
    assert len(historia) == 1, "SPEC.md §8.4 — snapshot wyłącznie przy zmianie"


async def test_zmiana_ceny_zapisuje_snapshot(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    adapter = ZrodloAtrapa()
    auction_id, _ = await przygotuj(pusta_baza, adapter, do_konca_min=20)
    disp = dispatcher(pusta_baza, adapter)
    await disp.jeden_obrot()

    adapter.cena = Decimal("41000")
    adapter.hash_tresci = "hash-2"
    teraz = await _czas_bazy(pusta_baza)
    async with PgUnitOfWork(pusta_baza) as uow:
        biezaca = await wczytaj(pusta_baza, auction_id)
        await uow.auction.zapisz(
            replace(biezaca, next_poll_at=teraz - dt.timedelta(seconds=1))
        )
    await disp.jeden_obrot()

    async with PgUnitOfWork(pusta_baza) as uow:
        historia = await uow.snapshot.historia(auction_id)
    assert [s.price.amount for s in historia] == [Decimal("40000"), Decimal("41000")]


async def test_pierwsza_obserwacja_nie_jest_nadpisywana(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """`first_seen_at` pochodzi z bazy — świeży odczyt go nie zna.

    Nadpisanie przesunęłoby datę pierwszej obserwacji na dziś i skasowało
    informację, jak długo aukcja jest w systemie.
    """
    adapter = ZrodloAtrapa()
    auction_id, _ = await przygotuj(pusta_baza, adapter, do_konca_min=20)
    przed = await wczytaj(pusta_baza, auction_id)

    await dispatcher(pusta_baza, adapter).jeden_obrot()

    assert (await wczytaj(pusta_baza, auction_id)).first_seen_at == przed.first_seen_at


# --------------------------------------------------------------------------
# Izolacja awarii i run_log (SPEC.md §13)
# --------------------------------------------------------------------------


async def test_awaria_zrodla_laduje_w_run_log_a_petla_zyje(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §13 — awaria jednego źródła nie przerywa przebiegu."""
    adapter = ZrodloAtrapa()
    await przygotuj(pusta_baza, adapter, do_konca_min=20)
    adapter.blad = SourceUnavailable("serwis nie odpowiada")

    sen = await dispatcher(pusta_baza, adapter).jeden_obrot()
    assert sen > 0, "pętla ma spać dalej, nie zatrzymać się"

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT error_count, errors, rss_bytes, database_bytes "
            "FROM app.run_log ORDER BY id DESC LIMIT 1"
        )
        wiersz = await cur.fetchone()
    assert wiersz is not None
    liczba_bledow, bledy, rss, rozmiar = wiersz
    assert liczba_bledow == 1
    assert "serwis nie odpowiada" in str(bledy)
    # SPEC.md §13 — budżet z §1.1 ma być mierzalny, nie deklaratywny.
    assert rss and rss > 0
    assert rozmiar and rozmiar > 0


async def test_bezpiecznik_odstawia_padniete_zrodlo(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Po serii błędów źródło pauzuje i przestaje być odpytywane (§10.2)."""
    adapter = ZrodloAtrapa()
    auction_id, _ = await przygotuj(pusta_baza, adapter, do_konca_min=20)
    adapter.blad = SourceUnavailable("timeout")
    disp = dispatcher(pusta_baza, adapter)

    for _ in range(3):
        teraz = await _czas_bazy(pusta_baza)
        async with PgUnitOfWork(pusta_baza) as uow:
            biezaca = await wczytaj(pusta_baza, auction_id)
            await uow.auction.zapisz(
                replace(biezaca, next_poll_at=teraz - dt.timedelta(seconds=1))
            )
        await disp.jeden_obrot()

    pobrania_przed = adapter.pobrania
    teraz = await _czas_bazy(pusta_baza)
    async with PgUnitOfWork(pusta_baza) as uow:
        biezaca = await wczytaj(pusta_baza, auction_id)
        await uow.auction.zapisz(
            replace(biezaca, next_poll_at=teraz - dt.timedelta(seconds=1))
        )
    await disp.jeden_obrot()

    assert adapter.pobrania == pobrania_przed, "odstawione źródło nie jest odpytywane"


async def test_zdrowe_zrodlo_pracuje_mimo_awarii_sasiada(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """To jest właściwe znaczenie „izolacji adapterów" z §13."""
    padniete = ZrodloAtrapa("padniete")
    zdrowe = ZrodloAtrapa("zdrowe")
    padniete.blad = SourceUnavailable("timeout")

    uow = PgUnitOfWork(pusta_baza)
    teraz = await _czas_bazy(pusta_baza)
    async with uow:
        for adapter in (padniete, zdrowe):
            zapisane = await uow.source.zapisz(zrodlo(adapter.key))
            assert zapisane.id is not None
            adapter.ends_at = teraz + dt.timedelta(minutes=20)
            await uow.auction.zapisz(
                Auction(
                    source_id=zapisane.id,
                    external_id=f"{adapter.key}-1",
                    url=f"https://atrapa.test/{adapter.key}",
                    status=AuctionStatus.ACTIVE,
                    first_seen_at=teraz,
                    last_seen_at=teraz,
                    price_current=Money(Decimal("1000"), Currency.PLN),
                    ends_at=adapter.ends_at,
                    next_poll_at=teraz - dt.timedelta(seconds=1),
                )
            )

    await dispatcher(pusta_baza, padniete, zdrowe).jeden_obrot()

    assert padniete.pobrania == 1
    assert zdrowe.sparsowane == 1, "awaria sąsiada nie ma prawa go zablokować"


async def test_brak_zaleglych_aukcji_konczy_sie_snem_bez_zadan(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.1 — w spoczynku CPU ma być zerowe."""
    adapter = ZrodloAtrapa()
    sen = await dispatcher(pusta_baza, adapter).jeden_obrot()

    assert adapter.pobrania == 0
    assert sen == MAKS_SEN_S, "pusta baza — śpimy pełne okno"


async def test_sen_skraca_sie_do_najblizszego_terminu(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """`min(next_due - now, 60)` — nie stały tick (§11.1)."""
    adapter = ZrodloAtrapa()
    auction_id, _ = await przygotuj(pusta_baza, adapter, do_konca_min=20)
    disp = dispatcher(pusta_baza, adapter)
    await disp.jeden_obrot()

    teraz = await _czas_bazy(pusta_baza)
    async with PgUnitOfWork(pusta_baza) as uow:
        biezaca = await wczytaj(pusta_baza, auction_id)
        await uow.auction.zapisz(
            replace(biezaca, next_poll_at=teraz + dt.timedelta(seconds=20))
        )

    sen = await disp.jeden_obrot()
    assert 15 < sen <= 21, f"termin za ~20 s, a śpimy {sen:.1f} s"


# --------------------------------------------------------------------------
# Rejestracja źródeł przy starcie (SPEC.md §8.1, §10.2)
# --------------------------------------------------------------------------


async def test_rejestracja_nie_kasuje_licznika_blokady(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §10.2 — licznik nieudanych logowań JEST trwały.

    `zapisz()` robi upsert nadpisujący wszystkie kolumny, więc rejestracja
    przy każdym starcie zerowałaby licznik. Pętla restartów kontenera
    obeszłaby wtedy twardy limit trzech prób i doprowadziła do zablokowania
    konta w serwisie — czyli dokładnie do tego, przed czym ten limit chroni.
    """
    from app.application.use_cases.rejestracja import zarejestruj_zrodla
    from app.infrastructure.sources.parametry import zbuduj_source

    zadane = [
        zbuduj_source(
            "autoprzetarg", enabled=True, rate_limit_per_minute=30, floor_seconds=60
        )
    ]

    async with PgUnitOfWork(pusta_baza) as uow:
        await zarejestruj_zrodla(uow, zadane)
        zapisane = await uow.source.po_kluczu("autoprzetarg")
        assert zapisane is not None
        await uow.source.zapisz(
            replace(
                zapisane,
                auth_state=AuthState.LOCKED,
                consecutive_auth_failures=3,
            )
        )

    # Restart add-onu: rejestracja idzie jeszcze raz, z tymi samymi danymi.
    async with PgUnitOfWork(pusta_baza) as uow:
        await zarejestruj_zrodla(uow, zadane)
        po_restarcie = await uow.source.po_kluczu("autoprzetarg")

    assert po_restarcie is not None
    assert po_restarcie.auth_state is AuthState.LOCKED
    assert po_restarcie.consecutive_auth_failures == 3


async def test_rejestracja_odswieza_fakty_o_serwisie_i_opcje(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Parametry z rekonesansu mieszkają w kodzie i to one są prawdą.

    Zmiana limitu tempa w opcjach ma wejść po restarcie, tak samo jak
    poprawiona wartość okna dogrywki.
    """
    from app.application.use_cases.rejestracja import zarejestruj_zrodla
    from app.infrastructure.sources.parametry import zbuduj_source

    async with PgUnitOfWork(pusta_baza) as uow:
        await zarejestruj_zrodla(
            uow,
            [
                zbuduj_source(
                    "poleasingowe",
                    enabled=True,
                    rate_limit_per_minute=30,
                    floor_seconds=15,
                )
            ],
        )
        await zarejestruj_zrodla(
            uow,
            [
                zbuduj_source(
                    "poleasingowe",
                    enabled=True,
                    rate_limit_per_minute=120,
                    floor_seconds=20,
                )
            ],
        )
        po = await uow.source.po_kluczu("poleasingowe")

    assert po is not None
    assert po.rate_limit_per_minute == 120
    assert po.floor_seconds == 20
    # Fakty z RECON.md §3.2 i §3.6 — okno 30 s, sufit 30 min, siatka {2,30}.
    assert po.overtime_window_seconds == 30
    assert po.overtime_cap_seconds == 1800
    assert po.closing_ladder_seconds == (2, 30)
    assert po.bid_history_ttl_seconds == 120


def test_stan_uwierzytelnienia_wynika_z_tego_czy_odczyt_wymaga_konta() -> None:
    """`ANONYMOUS` znaczy „czyta się bez konta", nie „jeszcze się nie logowałem".

    Dziś **żadne** z czterech źródeł nie wymaga sesji do odczytu — autoprzetarg
    też nie, choć bez zalogowania nie podaje liczby ofert (RECON.md §4.4).
    Sesja dokłada tam dane, ale nie warunkuje odczytu, więc `EXPIRED` byłoby
    nieprawdą i zapaliłoby w panelu stan wyglądający na awarię.

    Reguła jest jednak w kodzie i ma działać, gdy pojawi się źródło, które
    faktycznie wymaga konta — dlatego sprawdzamy ją na obu gałęziach.
    """
    from dataclasses import replace as podmien

    from app.infrastructure.sources import parametry

    assert all(not z.wymaga_logowania for z in parametry.ZNANE.values())

    for klucz in parametry.ZNANE:
        zbudowane = parametry.zbuduj_source(
            klucz, enabled=True, rate_limit_per_minute=30, floor_seconds=60
        )
        assert zbudowane.auth_state is AuthState.ANONYMOUS, klucz

    parametry.ZNANE["_test_z_logowaniem"] = podmien(
        parametry.ZNANE["efl"], wymaga_logowania=True
    )
    try:
        z_kontem = parametry.zbuduj_source(
            "_test_z_logowaniem",
            enabled=True,
            rate_limit_per_minute=30,
            floor_seconds=60,
        )
        assert z_kontem.auth_state is AuthState.EXPIRED
    finally:
        del parametry.ZNANE["_test_z_logowaniem"]


async def test_przemiat_wprowadza_aukcje_do_bazy(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Bez tego dispatcher odświeżałby wyłącznie to, co ktoś wstawił ręcznie.

    Dokładnie tak było po etapie 9: pętla działała, panel pokazywał pustą
    listę, bo `przemiec_liste` nie było wywoływane nigdzie.
    """
    adapter = ZrodloAtrapa()
    adapter.na_liscie = ["a1", "a2", "a3"]
    async with PgUnitOfWork(pusta_baza) as uow:
        await uow.source.zapisz(zrodlo(adapter.key))

    await dispatcher(pusta_baza, adapter).jeden_obrot()

    async with pusta_baza.cursor() as cur:
        await cur.execute("SELECT external_id FROM app.auction ORDER BY external_id")
        klucze = [w[0] for w in await cur.fetchall()]
    assert klucze == ["a1", "a2", "a3"]
    assert adapter.przemiaty == 1


async def test_przemiat_nie_powtarza_sie_przed_uplywem_interwalu(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.2 — „raz na kilka godzin", nie przy każdym obrocie pętli."""
    adapter = ZrodloAtrapa()
    adapter.na_liscie = ["a1"]
    async with PgUnitOfWork(pusta_baza) as uow:
        await uow.source.zapisz(zrodlo(adapter.key, sweep_interval_seconds=21_600))

    disp = dispatcher(pusta_baza, adapter)
    await disp.jeden_obrot()
    await disp.jeden_obrot()
    await disp.jeden_obrot()

    assert adapter.przemiaty == 1, "drugi i trzeci obrót są przed upływem interwału"


async def test_przemiat_nie_kasuje_dokladnego_konca_z_odpytu_szczegolow(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Najważniejsza reguła zapisu z przemiatu: **lista wie mniej**.

    poleasingowe.pl nie podaje na liście godziny zakończenia (RECON.md §4.2).
    Gdyby przemiat nadpisywał `ends_at`, kasowałby dokładny termin odczytany
    ze strony szczegółów — a na nim stoi cały harmonogram z §11.2.
    """
    adapter = ZrodloAtrapa()
    auction_id, koniec = await przygotuj(pusta_baza, adapter, do_konca_min=20)

    adapter.na_liscie = ["sztuczna-1"]
    adapter.ends_at_na_liscie = None  # lista nie zna godziny
    async with PgUnitOfWork(pusta_baza) as uow:
        biezace = await uow.source.po_kluczu(adapter.key)
        assert biezace is not None
        await uow.source.zapisz(replace(biezace, last_sweep_at=None))

    await dispatcher(pusta_baza, adapter).jeden_obrot()

    po = await wczytaj(pusta_baza, auction_id)
    assert po.ends_at == koniec, "przemiat nie ma prawa wyczyścić terminu"


async def test_przemiat_nie_przestawia_zakonczonej_aukcji_na_aktywna(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Serwis trzyma zakończone aukcje na liście (RECON.md §3.6).

    Status wie odpyt szczegółów (`auction_pending`), nie lista.
    """
    adapter = ZrodloAtrapa()
    auction_id, _ = await przygotuj(pusta_baza, adapter, do_konca_min=20)
    async with PgUnitOfWork(pusta_baza) as uow:
        biezaca = await wczytaj(pusta_baza, auction_id)
        await uow.auction.zapisz(replace(biezaca, status=AuctionStatus.ENDED))
        zr = await uow.source.po_kluczu(adapter.key)
        assert zr is not None
        await uow.source.zapisz(replace(zr, last_sweep_at=None))

    adapter.na_liscie = ["sztuczna-1"]
    await dispatcher(pusta_baza, adapter).jeden_obrot()

    async with pusta_baza.cursor() as cur:
        await cur.execute("SELECT status FROM app.auction WHERE id = %s", (auction_id,))
        wiersz = await cur.fetchone()
    assert wiersz is not None and wiersz[0] == "ENDED"


async def test_nowa_aukcja_dostaje_dokladnie_jeden_odpyt_i_wraca_do_przemiatu(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Kompromis wobec dosłownego §11.2, świadomy i ograniczony.

    Spec mówi, że nieobserwowane „nie są odpytywane pojedynczo w ogóle".
    Trzymanie się tego co do litery znaczyłoby jednak, że lista poleasingowe
    nie ma godziny zakończenia (serwis podaje na niej samą datę dzienną,
    RECON.md §4.2) — a bez niej nie da się zdecydować, co warto obserwować.

    Stąd: **jeden** odpyt po odkryciu, potem `next_poll_at = NULL`. Główna
    oszczędność zostaje nienaruszona — koszt stały rośnie z liczbą
    obserwowanych, a nie z liczbą ofert w serwisie.
    """
    adapter = ZrodloAtrapa()
    adapter.na_liscie = ["a1", "a2"]
    async with PgUnitOfWork(pusta_baza) as uow:
        await uow.source.zapisz(zrodlo(adapter.key))

    disp = dispatcher(pusta_baza, adapter)
    await disp.jeden_obrot()
    assert adapter.pobrania == 2, "każda nowa aukcja dostaje swój jeden odpyt"

    for _ in range(3):
        await disp.jeden_obrot()
    assert adapter.pobrania == 2, "i ani jednego więcej — nikt ich nie obserwuje"

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT count(*) FROM app.auction WHERE next_poll_at IS NOT NULL"
        )
        wiersz = await cur.fetchone()
    assert wiersz is not None and wiersz[0] == 0


async def test_obserwowana_aukcja_jest_odpytywana_dalej(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Odwrotna strona tej samej reguły: watchlista utrzymuje odpyt."""
    adapter = ZrodloAtrapa()
    auction_id, _ = await przygotuj(pusta_baza, adapter, do_konca_min=20)

    await dispatcher(pusta_baza, adapter).jeden_obrot()

    po = await wczytaj(pusta_baza, auction_id)
    assert po.next_poll_at is not None, "obserwowana ma zaplanowany kolejny odpyt"
    assert po.poll_tier is not PollTier.IDLE


async def test_migracja_ujednolica_marki_juz_zebrane(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Migracja 006 na realnej bazie — sam kod mappera tu nie wystarczy.

    Aukcje **zakończone** nie pojawiają się już na liście, więc przemiat ich
    nie dotknie i zostałyby z dawnym zapisem na zawsze. A to właśnie one
    niosą ceny końcowe, czyli wchodzą do `v_market_stats` (§8.4, §9).
    """
    adapter = ZrodloAtrapa()
    async with PgUnitOfWork(pusta_baza) as uow:
        zapisane = await uow.source.zapisz(zrodlo(adapter.key))
        assert zapisane.id is not None
        teraz = await _czas_bazy(pusta_baza)
        for numer, marka in enumerate(
            ["TESLA", "Tesla", "BMW", "bmw", "MERCEDES", "SKODA", "VW", "Peugeot"]
        ):
            await uow.auction.zapisz(
                Auction(
                    source_id=zapisane.id,
                    external_id=f"m{numer}",
                    url="https://atrapa.test/x",
                    status=AuctionStatus.ENDED,
                    first_seen_at=teraz,
                    last_seen_at=teraz,
                    make=marka,
                )
            )

    # Migracje sa juz zastosowane przez fikstury, wiec uruchamiamy sama 006
    # na danych wstawionych po fakcie — dokladnie tak, jak zadziala przy
    # aktualizacji dodatku na istniejacej bazie.
    from tests.conftest import MIGRACJE

    sql = (MIGRACJE / "006_marki.sql").read_text(encoding="utf-8")
    async with pusta_baza.transaction():
        await pusta_baza.execute(sql)

    async with pusta_baza.cursor() as cur:
        await cur.execute("SELECT DISTINCT make FROM app.auction")
        marki = {w[0] for w in await cur.fetchall()}

    # Zbiorem, nie lista: kolejnosc zalezy od locale bazy, a sprawdzamy
    # WARTOSCI — osiem wierszy w szesciu wariantach zapisu ma dac szesc marek.
    assert marki == {
        "BMW",
        "Mercedes-Benz",
        "Peugeot",
        "Tesla",
        "Volkswagen",
        "Škoda",
    }
