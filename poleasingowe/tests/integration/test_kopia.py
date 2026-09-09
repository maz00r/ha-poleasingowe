"""Kopia zapasowa bazy na realnym `pg_dump` (SPEC.md §7.1, §13).

Test **uruchamia prawdziwy `pg_dump`** na bazie testowej. Atrapa
sprawdzałaby wyłącznie to, czy umiem złożyć listę argumentów — a tu psuje
się co innego: niezgodność wersji klienta i serwera, brak uprawnień do
katalogu, hasło nieprzekazane do procesu potomnego.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import pathlib
import shutil
import uuid

import psycopg
import pytest

from app.infrastructure.kopia import BladKopii, KopiaZapasowa
from tests.conftest import _dsn, wymaga_postgresa

pytestmark = [
    wymaga_postgresa,
    pytest.mark.skipif(
        shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
        reason="brak `pg_dump` lub `pg_restore` w PATH — obraz dodatku ma "
        "pakiet postgresql17-client",
    ),
]

TERAZ = dt.datetime(2026, 9, 9, 3, 0, tzinfo=dt.UTC)


def _kopia(
    baza: psycopg.AsyncConnection, katalog: pathlib.Path, **kw: object
) -> KopiaZapasowa:
    nazwa = baza.info.dbname
    return KopiaZapasowa(_dsn(nazwa), katalog=katalog, **kw)  # type: ignore[arg-type]


async def test_kopia_powstaje_i_daje_sie_odczytac(
    pusta_baza: psycopg.AsyncConnection, tmp_path: pathlib.Path
) -> None:
    """Plik ma istnieć, mieć rozmiar i format `custom`.

    Nagłówek `PGDMP` jest jedynym sprawdzeniem, które odróżnia poprawny
    zrzut od pustego pliku — a pusty plik wygląda jak udana kopia aż do dnia,
    w którym trzeba z niego odtworzyć bazę.
    """
    kopia = _kopia(pusta_baza, tmp_path)
    assert kopia.ostatnia() is None
    assert kopia.czas_na_kopie(TERAZ) is True, "brak kopii to najwyższy czas"

    wynik = await kopia.wykonaj(TERAZ)

    assert wynik.sciezka.exists()
    assert wynik.bajtow > 0
    assert wynik.sciezka.read_bytes()[:5] == b"PGDMP", "to nie jest format custom"
    assert wynik.sciezka.name == "poleasingowe-20260909-030000.dump"
    # Plik częściowy nie ma prawa zostać — przerwana kopia wyglądałaby na dobrą.
    assert list(tmp_path.glob("*.czesciowy")) == []


async def test_kopie_daje_sie_odtworzyc_do_osobnej_bazy(
    pusta_baza: psycopg.AsyncConnection, tmp_path: pathlib.Path
) -> None:
    """Prawdziwy restore sprawdza więcej niż nagłówek `PGDMP`.

    Baza celu ma losową nazwę i powstaje na lokalnym PostgreSQL testowym;
    nigdy na instancji Home Assistant. Porównujemy dane po odtworzeniu, nie
    wyłącznie kod wyjścia narzędzia.
    """
    await pusta_baza.execute(
        "INSERT INTO app.source (key, name) VALUES ('restore-test', 'Restore test')"
    )
    await pusta_baza.commit()
    kopia = _kopia(pusta_baza, tmp_path)
    plik = await kopia.wykonaj(TERAZ)
    cel = f"poleasingowe_restore_{uuid.uuid4().hex[:12]}"

    with psycopg.connect(_dsn("postgres"), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{cel}"')
    try:
        srodowisko = {
            **os.environ,
            "PGPASSWORD": os.environ.get(
                "POLEASINGOWE_TEST_PASSWORD", "poleasingowe_test"
            ),
        }
        proces = await asyncio.create_subprocess_exec(
            "pg_restore",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            _dsn(cel),
            str(plik.sciezka),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=srodowisko,
        )
        _, blad = await proces.communicate()
        assert proces.returncode == 0, blad.decode("utf-8", "replace")
        async with await psycopg.AsyncConnection.connect(_dsn(cel)) as odtworzona:  # noqa: SIM117
            async with odtworzona.cursor() as cur:
                await cur.execute("SELECT key, name FROM app.source")
                assert await cur.fetchall() == [("restore-test", "Restore test")]
    finally:
        with psycopg.connect(_dsn("postgres"), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (cel,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{cel}"')


async def test_trzymamy_siedem_kopii_i_kasujemy_najstarsze(
    pusta_baza: psycopg.AsyncConnection, tmp_path: pathlib.Path
) -> None:
    """SPEC.md §7.1 — siedem kopii. `/share` nie jest workiem bez dna."""
    kopia = _kopia(pusta_baza, tmp_path, ile_trzymac=3)
    for godzina in range(5):
        await kopia.wykonaj(TERAZ + dt.timedelta(hours=godzina))

    pliki = sorted(p.name for p in tmp_path.glob("poleasingowe-*.dump"))
    assert len(pliki) == 3
    assert pliki[0] == "poleasingowe-20260909-050000.dump", "zostają NAJNOWSZE"


async def test_dobowy_rytm(
    pusta_baza: psycopg.AsyncConnection, tmp_path: pathlib.Path
) -> None:
    kopia = _kopia(pusta_baza, tmp_path)
    await kopia.wykonaj(TERAZ)

    ostatnia = kopia.ostatnia()
    assert ostatnia is not None
    # `czas_na_kopie` porównuje z czasem PLIKU, a ten powstał przed chwilą.
    teraz = dt.datetime.now(dt.UTC)
    assert kopia.czas_na_kopie(teraz) is False
    assert kopia.czas_na_kopie(teraz + dt.timedelta(hours=25)) is True


async def test_blad_pg_dump_nie_zostawia_smieci(
    pusta_baza: psycopg.AsyncConnection, tmp_path: pathlib.Path
) -> None:
    """Nieudana kopia ma zniknąć bez śladu i powiedzieć, co się stało."""
    kopia = KopiaZapasowa(
        "postgresql://nikt:nic@localhost:5432/nie-ma-takiej-bazy",
        katalog=tmp_path,
    )
    with pytest.raises(BladKopii) as blad:
        await kopia.wykonaj(TERAZ)

    assert str(blad.value), "komunikat od pg_dump ma dotrzeć do logu"
    assert list(tmp_path.iterdir()) == []


def test_haslo_nie_trafia_do_linii_polecen(tmp_path: pathlib.Path) -> None:
    """SPEC.md §10.2 — linia poleceń procesu jest widoczna w `ps`.

    Hasło ma iść środowiskiem procesu potomnego, nigdy argumentem.
    """
    kopia = KopiaZapasowa(
        "postgresql://rola:bardzo-tajne@baza:5433/poleasingowe", katalog=tmp_path
    )
    srodowisko = kopia._srodowisko()
    assert srodowisko["PGPASSWORD"] == "bardzo-tajne"
    assert srodowisko["PGHOST"] == "baza"
    assert srodowisko["PGPORT"] == "5433"
    assert srodowisko["PGDATABASE"] == "poleasingowe"
