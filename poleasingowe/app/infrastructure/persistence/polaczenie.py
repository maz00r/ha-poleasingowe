"""Nawiązywanie połączenia z bazą i stosowanie migracji przy starcie.

Sterownik bazy żyje wyłącznie w tej warstwie (SPEC.md §6, §6.3) — `interfaces/`
ma prawo to wywołać, ale nie ma prawa importować `psycopg`. Pilnuje tego
kontrakt import-lintera.

**Nigdy crash-loop kontenera** (SPEC.md §2 pkt 8): restartująca się usługa
dobijająca się do Postgresa dzielonego z inną aplikacją jest gorsza niż usługa
wyłączona. Brak bazy kończy się ponawianiem z backoffem i czytelnym
komunikatem, a nie wyjściem z procesu.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
from collections.abc import Callable

import psycopg

from app.infrastructure.persistence.migrations import Migracja, zastosuj_migracje

log = logging.getLogger(__name__)

# SPEC.md §2 pkt 8 — backoff DO 5 MINUT. Rosnie, ale nigdy nie przekracza tej
# wartosci: dobijanie sie co sekunde do wspoldzielonego serwera jest dokladnie
# tym, czego spec zabrania.
BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 300.0


async def polacz_i_zmigruj(
    dsn: str,
    katalog_migracji: pathlib.Path,
    *,
    opis_polaczenia: str,
    na_sukces: Callable[[list[Migracja]], None],
    na_blad: Callable[[str], None],
    backoff_start_s: float = BACKOFF_START_S,
    backoff_max_s: float = BACKOFF_MAX_S,
) -> None:
    """Łączy się z bazą i stosuje migracje, ponawiając aż do skutku.

    `opis_polaczenia` to bezpieczny opis bez hasła — trafia do logów, więc
    **nigdy nie przekazuj tu DSN-u** (SPEC.md §10.2).

    Wywołania zwrotne pozwalają warstwie wyżej odnotować stan bez wiedzy
    o sterowniku: `na_sukces` dostaje listę zastosowanych migracji,
    `na_blad` — opis ostatniego niepowodzenia do panelu diagnostycznego.
    """
    odstep = backoff_start_s
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(
                dsn, connect_timeout=10
            ) as conn:
                zastosowane = await zastosuj_migracje(conn, katalog_migracji)
                if zastosowane:
                    log.info(
                        "zastosowano migracje: %s",
                        ", ".join(f"{m.version:03d}_{m.name}" for m in zastosowane),
                    )
                log.info("baza dostępna: %s", opis_polaczenia)
                na_sukces(list(zastosowane))
                return
        except Exception as exc:
            # Lapiemy szeroko celowo: kazdy blad ma skonczyc sie ponowieniem,
            # nie wyjsciem z procesu. Powod ladu je w panelu diagnostycznym.
            na_blad(f"{type(exc).__name__}: {exc}")
            log.warning(
                "brak dostępu do bazy (%s), ponowna próba za %.0f s: %s",
                opis_polaczenia,
                odstep,
                exc,
            )
            await asyncio.sleep(odstep)
            odstep = min(odstep * 2, backoff_max_s)
