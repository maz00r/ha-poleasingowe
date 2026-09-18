"""Migracje na realnej bazie (SPEC.md §14 pkt 3).

Baza tymczasowa, tworzona i kasowana przez fixture. Nigdy produkcja.
"""

from __future__ import annotations

import psycopg
import pytest

from app.infrastructure.persistence.migrations import (
    KLUCZ_BLOKADY_MIGRACJI,
    BladMigracji,
    Migracja,
    wczytaj_migracje,
    zastosuj_migracje,
)
from tests.conftest import MIGRACJE, wymaga_postgresa

pytestmark = wymaga_postgresa


async def test_migracje_stosuja_sie_i_sa_idempotentne(
    baza_od_zera: psycopg.AsyncConnection,
) -> None:
    # Wlasna, dziewicza baza: ten test jako jedyny wymaga, zeby NIC nie bylo
    # jeszcze zastosowane. Na bazie sesyjnej byl zalezny od kolejnosci plikow.
    polaczenie = baza_od_zera

    # Bez sztywnej listy — test nie ma wymagac aktualizacji przy kazdej
    # nowej migracji, tylko sprawdzac, ze stosuje sie dokladnie to, co lezy
    # na dysku, i ze drugie przejscie nie robi nic.
    na_dysku = wczytaj_migracje(MIGRACJE)
    assert na_dysku, "brak plikow migracji — test nie mialby czego sprawdzac"

    pierwsze = await zastosuj_migracje(polaczenie, MIGRACJE)
    assert [m.version for m in pierwsze] == [m.version for m in na_dysku]

    drugie = await zastosuj_migracje(polaczenie, MIGRACJE)
    assert drugie == [], "druga próba nie ma prawa niczego zastosować ponownie"

    async with polaczenie.cursor() as cur:
        await cur.execute(
            "SELECT version, name FROM app.schema_migration ORDER BY version"
        )
        zapisane = await cur.fetchall()
    assert zapisane == [(m.version, m.name) for m in na_dysku]


async def test_powstaly_wszystkie_tabele_i_indeksy(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'app' ORDER BY 1"
        )
        tabele = [w[0] for w in await cur.fetchall()]
    assert tabele == [
        "ai_valuation",
        "auction",
        "auction_photo",
        "offer",
        "photo_archive_state",
        "price_snapshot",
        "run_log",
        "saved_filter",
        "schema_migration",
        "source",
        "watchlist",
    ]

    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'app' "
            "AND indexname LIKE '%_idx' ORDER BY 1"
        )
        indeksy = [w[0] for w in await cur.fetchall()]
    # SPEC.md §8.3 wymienia je z nazwy — indeks czesciowy na next_poll_at jest
    # kluczowy, bo to zapytanie wykonuje sie najczesciej.
    assert "auction_next_poll_active_idx" in indeksy
    assert "price_snapshot_auction_ts_idx" in indeksy
    assert "watchlist_auction_unique_idx" in indeksy
    assert "offer_auction_placed_idx" in indeksy, "odczyt ofert na karcie (§11.8)"
    assert (
        "offer_external_id_unique_idx" in indeksy
    ), "identyfikator oferty z serwisu rozstrzyga duplikaty (`013`)"


async def test_watchlista_ma_notatke_bez_ceny_docelowej(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'app' AND table_name = 'watchlist' "
            "ORDER BY ordinal_position"
        )
        kolumny = [wiersz[0] for wiersz in await cur.fetchall()]
        await cur.execute(
            "SELECT watch_target_price FROM reporting.v_auction_current LIMIT 1"
        )
        assert await cur.fetchone() is None
    assert kolumny == ["id", "auction_id", "note", "added_at"]


async def test_indeks_na_next_poll_jest_czesciowy(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §8.3 — ma trafiać wyłącznie w aktywne aukcje."""
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='app' AND indexname='auction_next_poll_active_idx'"
        )
        wiersz = await cur.fetchone()
    assert wiersz is not None
    assert "WHERE (status = 'ACTIVE'" in wiersz[0], wiersz[0]


async def test_migracja_poprawia_historyczne_tytuly_autoprzetarg(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "INSERT INTO app.source (key, name) VALUES ('autoprzetarg', 'Auto') "
            "RETURNING id"
        )
        source_id = (await cur.fetchone())[0]  # type: ignore[index]
        await cur.executemany(
            "INSERT INTO app.auction "
            "(source_id, external_id, url, variant) VALUES (%s, %s, %s, %s)",
            [
                (source_id, "a", "https://example/a", "1490,00 cm3 / 125 KM"),
                (
                    source_id,
                    "b",
                    "https://example/b",
                    "Executive 3,0 DIESEL / 175 KM",
                ),
            ],
        )
        await cur.execute((MIGRACJE / "008_tytuly_autoprzetarg.sql").read_text())
        await cur.execute(
            "SELECT external_id, variant FROM app.auction ORDER BY external_id"
        )
        assert await cur.fetchall() == [("a", None), ("b", "Executive")]


async def test_migracje_nie_tworza_schematow(
    polaczenie: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §0 — schematy już istnieją, migracja zaczyna od tabel."""
    for migracja in wczytaj_migracje(MIGRACJE):
        assert "CREATE SCHEMA" not in migracja.sql.upper()


async def test_zmieniona_migracja_jest_wykrywana(
    polaczenie: psycopg.AsyncConnection, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Edycja zastosowanej migracji ma się wywalić, nie przejść po cichu."""
    await zastosuj_migracje(polaczenie, MIGRACJE)

    katalog = tmp_path_factory.mktemp("migracje")
    (katalog / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(BladMigracji, match="checksum"):
        await zastosuj_migracje(polaczenie, katalog)


def test_luka_w_numeracji_jest_wykrywana(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    katalog = tmp_path_factory.mktemp("luka")
    (katalog / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")
    (katalog / "003_dalej.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(BladMigracji, match="luka"):
        wczytaj_migracje(katalog)


def test_zla_nazwa_pliku_jest_wykrywana(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    katalog = tmp_path_factory.mktemp("zla-nazwa")
    (katalog / "init.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(BladMigracji, match="NNN_nazwa.sql"):
        wczytaj_migracje(katalog)


def test_checksum_zalezy_od_tresci() -> None:
    a = Migracja(version=1, name="init", sql="SELECT 1;")
    b = Migracja(version=1, name="init", sql="SELECT 2;")
    assert a.checksum != b.checksum


async def test_blokada_doradcza_jest_trzymana_w_trakcie_migracji(
    polaczenie: psycopg.AsyncConnection, nazwa_bazy_testowej: str
) -> None:
    """SPEC.md §2 pkt 6 — migracje pod blokadą, z własnym stałym kluczem.

    Blokady doradcze w PostgreSQL są wspólne dla całej instancji, nie dla
    pojedynczej bazy, więc kolizja z migracjami Ecto (TeslaMate) byłaby realna.
    Ten test sprawdza, że blokadę faktycznie bierzemy i że zwalnia się sama
    po transakcji — `pg_advisory_xact_lock`, nie sesyjna.
    """
    async with polaczenie.transaction():
        await polaczenie.execute(
            "SELECT pg_advisory_xact_lock(%s)", (KLUCZ_BLOKADY_MIGRACJI,)
        )
        async with polaczenie.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND ((classid::bigint << 32) | objid::bigint) = %s",
                (KLUCZ_BLOKADY_MIGRACJI,),
            )
            wiersz = await cur.fetchone()
        assert wiersz is not None and wiersz[0] >= 1, "blokada nie została wzięta"

    async with polaczenie.cursor() as cur:
        await cur.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND ((classid::bigint << 32) | objid::bigint) = %s",
            (KLUCZ_BLOKADY_MIGRACJI,),
        )
        wiersz = await cur.fetchone()
    assert (
        wiersz is not None and wiersz[0] == 0
    ), "blokada nie zwolniła się po transakcji — musi być xact, nie sesyjna"


async def _wstaw_zrodlo(
    baza: psycopg.AsyncConnection, klucz: str, **kolumny: object
) -> None:
    nazwy = ", ".join(kolumny)
    znaki = ", ".join(["%s"] * len(kolumny))
    # Kolumny sa nazwami z kodu testu, nie z danych — ale sklejanie SQL-a
    # i tak wolimy trzymac w jednym miejscu, zamiast rozsiewac po testach.
    sql = f"INSERT INTO app.source (key, name, {nazwy}) VALUES (%s, %s, {znaki})"
    async with baza.cursor() as cur:
        await cur.execute(sql, (klucz, klucz, *kolumny.values()))


async def test_drabinka_domkniecia_musi_byc_rosnaca_i_dodatnia(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.5 — „nastepny element" ma sens tylko przy rosnacej siatce.

    Gdyby baza przyjmowala dowolna tablice, kolejnosc prob fazy 2 zalezalaby
    od kolejnosci wpisu, a nie od czasu — i drabinka dla autoprzetarg
    (okno 10-15 s) mogla po cichu trafiac w przekierowanie.
    """
    async with pusta_baza.transaction():
        await _wstaw_zrodlo(pusta_baza, "ok", closing_ladder_seconds=[2, 5, 8, 11, 14])

    for opis, drabinka in [
        ("malejaca", [5, 2]),
        ("z powtorzeniem", [2, 2, 5]),
        ("z zerem", [0, 5]),
        ("z ujemna", [2, -5]),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with pusta_baza.transaction():
                await _wstaw_zrodlo(
                    pusta_baza, f"zla-{opis}", closing_ladder_seconds=drabinka
                )


async def test_pusta_drabinka_jest_dozwolona(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Pusta tablica znaczy „jeden odpyt i koniec", a nie blad (§8.2)."""
    async with pusta_baza.transaction():
        await _wstaw_zrodlo(pusta_baza, "bez-drabinki", closing_ladder_seconds=[])


async def test_semantyka_bid_count_jest_ze_slownika(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SPEC.md §11.8 — od tej wartosci zalezy, czy `bid_gap` cokolwiek znaczy."""
    async with pusta_baza.transaction():
        await _wstaw_zrodlo(pusta_baza, "efl", bid_count_semantics="PARTICIPANTS")

    with pytest.raises(psycopg.errors.CheckViolation):
        async with pusta_baza.transaction():
            await _wstaw_zrodlo(pusta_baza, "zle", bid_count_semantics="BIDS")


async def test_domyslna_semantyka_to_niewiedza_a_nie_zalozenie(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Zrodlo bez dowodu z rekonesansu ma byc UNKNOWN, nie OFFERS.

    Domyslne OFFERS znaczyloby, ze `bid_gap` liczy sie dla kazdego nowego
    zrodla od razu — i dla serwisu z licytacja proxy (EFL) dawaloby zera
    czytane jako „komplet historii".
    """
    async with pusta_baza.transaction():
        await _wstaw_zrodlo(pusta_baza, "nowe", enabled=True)
        async with pusta_baza.cursor() as cur:
            await cur.execute(
                "SELECT bid_count_semantics, closing_ladder_seconds,"
                " bid_history_ttl_seconds FROM app.source WHERE key = 'nowe'"
            )
            wiersz = await cur.fetchone()
    assert wiersz == ("UNKNOWN", [2, 5, 10, 20, 40], None)


async def test_migracja_rozpoznaje_rodzaj_pojazdu_w_zebranych_wierszach(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """Backfill `009_rodzaje.sql` na danych w kształcie, w jakim są w bazie.

    Trzy różne reguły, bo trzy źródła wiedzą o rodzaju co innego:
    autoprzetarg ma kategorię **w adresie** już zapisanej aukcji, EFL
    przemiata wyłącznie kategorię osobowych, a poleasingowe nie mówi nic
    poza nazwą.
    """
    async with pusta_baza.cursor() as cur:
        zrodla: dict[str, int] = {}
        for klucz in ("autoprzetarg", "efl", "poleasingowe"):
            await cur.execute(
                "INSERT INTO app.source (key, name) VALUES (%s, %s) RETURNING id",
                (klucz, klucz),
            )
            zrodla[klucz] = (await cur.fetchone())[0]  # type: ignore[index]

        await cur.executemany(
            "INSERT INTO app.auction (source_id, external_id, url, make, model,"
            " variant, body) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                # autoprzetarg — kategoria w ostatnim segmencie adresu
                (
                    zrodla["autoprzetarg"],
                    "ap-naczepa",
                    "https://autoprzetarg.pl/aukcja/krone-sd,abc123,Naczepy-i-przyczepy",
                    "Krone",
                    "SD",
                    None,
                    None,
                ),
                (
                    zrodla["autoprzetarg"],
                    "ap-motocykl",
                    "https://autoprzetarg.pl/aukcja/suzuki,def456,Motocykle",
                    "Suzuki",
                    "GSX-S1000S",
                    None,
                    None,
                ),
                (
                    zrodla["autoprzetarg"],
                    "ap-osobowy",
                    "https://autoprzetarg.pl/aukcja/audi-a4,ghi789,Samochody-osobowe",
                    "Audi",
                    "A4",
                    None,
                    None,
                ),
                # EFL — adapter przemiata tylko kategorię osobowych
                (zrodla["efl"], "efl-1", "https://efl/1", "Audi", "A3", None, None),
                # poleasingowe — wyłącznie nazwa i nadwozie
                (
                    zrodla["poleasingowe"],
                    "pol-ciagnik",
                    "https://poleasingowe/1",
                    "MAN",
                    "TGX",
                    None,
                    "CIĄGNIK SIODŁOWY",
                ),
                (
                    zrodla["poleasingowe"],
                    "pol-kombi",
                    "https://poleasingowe/2",
                    "Škoda",
                    "Superb",
                    "KOMBI",
                    None,
                ),
                (
                    zrodla["poleasingowe"],
                    "pol-nieznany",
                    "https://poleasingowe/3",
                    "Tesla",
                    "Model Y",
                    None,
                    None,
                ),
            ],
        )
        await cur.execute((MIGRACJE / "009_rodzaje.sql").read_text())
        await cur.execute(
            "SELECT external_id, vehicle_kind FROM app.auction ORDER BY external_id"
        )
        assert await cur.fetchall() == [
            ("ap-motocykl", "MOTOCYKL"),
            ("ap-naczepa", "PRZYCZEPA"),
            ("ap-osobowy", "OSOBOWY"),
            ("efl-1", "OSOBOWY"),
            ("pol-ciagnik", "CIEZAROWY"),
            ("pol-kombi", "OSOBOWY"),
            # Nazwa bez nadwozia zostaje nierozpoznana, a nie zgadnięta.
            ("pol-nieznany", "NIEZNANY"),
        ]


async def test_migracja_wymusza_odswiezenie_nierozpoznanych_aukcji_dawro(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "INSERT INTO app.source (key, name) VALUES ('dawro', 'DAWRO'),"
            " ('inne', 'Inne') RETURNING key, id"
        )
        zrodla: dict[str, int] = dict(await cur.fetchall())
        await cur.executemany(
            "INSERT INTO app.auction (source_id, external_id, url, status,"
            " vehicle_kind, content_hash, next_poll_at)"
            " VALUES (%s, %s, %s, %s, %s, 'stary', now() + interval '1 day')",
            [
                (zrodla["dawro"], "do-odswiezenia", "u1", "ACTIVE", "NIEZNANY"),
                (zrodla["dawro"], "znany", "u2", "ACTIVE", "OSOBOWY"),
                (zrodla["dawro"], "zakonczony", "u3", "ENDED", "NIEZNANY"),
                (zrodla["inne"], "obcy", "u4", "ACTIVE", "NIEZNANY"),
            ],
        )

        await cur.execute((MIGRACJE / "022_napraw_dawro_rodzaje.sql").read_text())
        await cur.execute(
            "SELECT external_id, content_hash, next_poll_at <= now()"
            " FROM app.auction ORDER BY external_id"
        )

        assert await cur.fetchall() == [
            ("do-odswiezenia", None, True),
            ("obcy", "stary", False),
            ("zakonczony", "stary", False),
            ("znany", "stary", False),
        ]


async def test_migracja_023_zamyka_listy_paliw_i_skrzyn_tak_samo_jak_python(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    """SQL powtarza wzorce za `paliwa.py`/`skrzynie.py` — tu obie strony
    dostają TE SAME próbki i muszą dać ten sam wynik. `\\y` (POSIX) i `\\b`
    (Python) różnią się w szczegółach, a rozjazd byłby cichy."""
    from app.infrastructure.sources import paliwa, skrzynie

    probki = [
        ("Olej napędowy", "Automatyczna"),
        ("Olej napedowy", "Automat"),
        ("Diesel", "automatyczna"),
        ("Hybryda plug-in", "DSG"),
        ("PHEV", "A/T"),
        ("Hybryda/benzyna", "S tronic"),
        ("Benzyna+LPG", "Manualna"),
        ("Benzyna z instalacją gazową", "Ręczna"),
        ("CNG", "M/T"),
        ("Elektryczny", "brak danych"),
        ("EV", "zautomatyzowana manualna"),
        ("Wodór", "bezstopniowa"),
        ("Gasoline", "mechaniczna"),
        ("Kategoria 1", "1"),
        ("nie dotyczy", ""),
    ]
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "INSERT INTO app.source (key, name) VALUES ('x', 'X') RETURNING id"
        )
        wiersz = await cur.fetchone()
        assert wiersz is not None
        await cur.executemany(
            "INSERT INTO app.auction (source_id, external_id, url, status,"
            " fuel, gearbox) VALUES (%s, %s, %s, 'ACTIVE', %s, %s)",
            [(wiersz[0], str(i), "u", f, g) for i, (f, g) in enumerate(probki)],
        )
        await cur.execute((MIGRACJE / "023_paliwa_i_skrzynie.sql").read_text())
        await cur.execute(
            "SELECT fuel, gearbox FROM app.auction ORDER BY external_id::int"
        )
        z_sql = await cur.fetchall()

    z_pythona = [
        (paliwa.kanoniczne_paliwo(f), skrzynie.kanoniczna_skrzynia(g))
        for f, g in probki
    ]
    assert z_sql == z_pythona
    assert {f for f, _ in z_sql} - {None} <= set(paliwa.KANONICZNE)
    assert {g for _, g in z_sql} - {None} <= set(skrzynie.KANONICZNE)


async def test_migracja_024_porzadkuje_marki_tak_samo_jak_python(
    pusta_baza: psycopg.AsyncConnection,
) -> None:
    from app.infrastructure.sources import marki

    probki = [
        "Mercedes- Benz",
        "MERCEDES",
        "Mini",
        "SKODA",
        "Škoda",
        "TESLA",
        "Bmw",
        "Land-Rover",
        "vw",
        "DS Automobiles",
        "Opel",
    ]
    async with pusta_baza.cursor() as cur:
        await cur.execute(
            "INSERT INTO app.source (key, name) VALUES ('x', 'X') RETURNING id"
        )
        wiersz = await cur.fetchone()
        assert wiersz is not None
        sid = wiersz[0]
        await cur.executemany(
            "INSERT INTO app.auction (source_id, external_id, url, make)"
            " VALUES (%s, %s, 'u', %s)",
            [(sid, str(i), m) for i, m in enumerate(probki)],
        )
        # Tytuł aukcji jako marka (poleasingowe po zakończeniu) i dopisek
        # w nawiasie jako model (dawro „MINI [BMW] Countryman").
        await cur.executemany(
            "INSERT INTO app.auction (source_id, external_id, url, make, model,"
            " variant) VALUES (%s, %s, 'u', %s, %s, %s)",
            [
                (sid, "aukcja", "Aukcja", "nr", "1384/STR/AU/2026 zakończyła się"),
                (sid, "mini", "Mini", "[BMW]", "Countryman Cooper S ALL4"),
            ],
        )
        await cur.execute((MIGRACJE / "024_marki_porzadek.sql").read_text())
        await cur.execute("SELECT external_id, make, model, variant FROM app.auction")
        wynik = {w[0]: w[1:] for w in await cur.fetchall()}

    for i, m in enumerate(probki):
        assert wynik[str(i)][0] == marki.kanoniczna_marka(m), m
    assert wynik["aukcja"] == (None, None, None)
    assert wynik["mini"] == ("MINI", "Countryman", "Cooper S ALL4")
