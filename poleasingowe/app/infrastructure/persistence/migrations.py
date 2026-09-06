"""Uruchamianie migracji pod blokadą doradczą (SPEC.md §2 pkt 6, §5).

Bez Alembica: numerowane pliki `.sql` plus tabela `app.schema_migration`.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
from dataclasses import dataclass

from psycopg import AsyncConnection

# ---------------------------------------------------------------------------
# Klucz blokady doradczej.
#
# SPEC.md §2 pkt 6 wymaga wlasnej, stalej, udokumentowanej wartosci, ktora nie
# koliduje z blokadami migracji Ecto uzywanymi przez TeslaMate. Blokady
# doradcze w PostgreSQL sa wspoldzielone w obrebie CALEJ instancji, a nie
# pojedynczej bazy — dlatego kolizja z sasiadem jest realnym ryzykiem,
# a nie teoretycznym.
#
# Wartosc wyprowadzona raz i zapisana na stale:
#     int.from_bytes(sha256(b"poleasingowe.migrations.v1")[:8], "big", signed=True)
#
# Ta sama wartosc jest udokumentowana w DOCS.md.
# ---------------------------------------------------------------------------
KLUCZ_BLOKADY_MIGRACJI = 5211429344620168909

_NAZWA_PLIKU = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class BladMigracji(RuntimeError):
    """Migracja nie da się zastosować albo stan bazy jest niespójny z plikami."""


@dataclass(slots=True, frozen=True)
class Migracja:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def wczytaj_migracje(katalog: pathlib.Path) -> list[Migracja]:
    """Wczytuje i sortuje pliki migracji, pilnując numeracji bez luk."""
    znalezione: list[Migracja] = []
    for plik in sorted(katalog.glob("*.sql")):
        dopasowanie = _NAZWA_PLIKU.match(plik.name)
        if dopasowanie is None:
            raise BladMigracji(
                f"{plik.name}: nazwa musi mieć postać NNN_nazwa.sql "
                "(trzycyfrowy numer, potem małe litery i podkreślenia)"
            )
        znalezione.append(
            Migracja(
                version=int(dopasowanie.group(1)),
                name=dopasowanie.group(2),
                sql=plik.read_text(encoding="utf-8"),
            )
        )
    numery = [m.version for m in znalezione]
    if len(numery) != len(set(numery)):
        raise BladMigracji(f"zduplikowane numery migracji: {numery}")
    if numery and numery != list(range(1, len(numery) + 1)):
        raise BladMigracji(f"luka w numeracji migracji: {numery}")
    return znalezione


async def _zastosowane(conn: AsyncConnection) -> dict[int, str]:
    """Zwraca `{wersja: checksum}`. Pusty słownik, gdy tabeli jeszcze nie ma."""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT to_regclass('app.schema_migration') IS NOT NULL AS istnieje"
        )
        wiersz = await cur.fetchone()
        if wiersz is None or not wiersz[0]:
            return {}
        await cur.execute("SELECT version, checksum FROM app.schema_migration")
        return {int(w[0]): str(w[1]) for w in await cur.fetchall()}


async def zastosuj_migracje(
    conn: AsyncConnection, katalog: pathlib.Path
) -> list[Migracja]:
    """Stosuje brakujące migracje pod blokadą doradczą. Zwraca zastosowane.

    Blokada jest transakcyjna (`pg_advisory_xact_lock`), więc zwalnia się sama
    przy commicie albo rollbacku — proces ubity w połowie nie zostawia
    zawieszonej blokady na współdzielonej instancji.
    """
    do_zrobienia = wczytaj_migracje(katalog)
    zastosowane_teraz: list[Migracja] = []

    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(%s)", (KLUCZ_BLOKADY_MIGRACJI,)
        )
        juz = await _zastosowane(conn)

        for migracja in do_zrobienia:
            poprzedni = juz.get(migracja.version)
            if poprzedni is not None:
                if poprzedni != migracja.checksum:
                    raise BladMigracji(
                        f"migracja {migracja.version:03d}_{migracja.name} została "
                        "zmieniona po zastosowaniu — checksum się nie zgadza. "
                        "Nie edytuj zastosowanych migracji; dopisz nową."
                    )
                continue

            await conn.execute(migracja.sql)
            await conn.execute(
                "INSERT INTO app.schema_migration (version, name, checksum) "
                "VALUES (%s, %s, %s)",
                (migracja.version, migracja.name, migracja.checksum),
            )
            zastosowane_teraz.append(migracja)

    return zastosowane_teraz
