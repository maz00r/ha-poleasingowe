"""Punkt wejścia add-onu — kompozycja, nie logika.

Ta warstwa wywołuje `application` i `infrastructure`, ale sama nie dotyka
bazy ani sieci (SPEC.md §6, §6.3). Sterownik bazy żyje w
`infrastructure/persistence/polaczenie.py`; pilnuje tego import-linter.

**Nigdy crash-loop kontenera** (SPEC.md §2 pkt 8). Jedyny przypadek, w którym
add-on się zatrzymuje, to błędna konfiguracja (§7.1) — tego nie da się
naprawić czekaniem, a działanie z wartościami domyślnymi, których użytkownik
nie ustawił, byłoby gorsze niż zatrzymanie.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys

import uvicorn

from app.infrastructure.persistence.migrations import Migracja
from app.infrastructure.persistence.polaczenie import polacz_i_zmigruj
from app.infrastructure.supervisor.options import (
    SCIEZKA_OPCJI,
    BladKonfiguracji,
    Opcje,
    wczytaj_opcje,
)
from app.interfaces.app import StanAplikacji, utworz_aplikacje

log = logging.getLogger("poleasingowe")

MIGRACJE = pathlib.Path(__file__).resolve().parents[1] / "migrations"
PORT_INGRESS = 8099


class _FiltrRedakcji(logging.Filter):
    """Redakcja w logach (SPEC.md §10.2).

    Hasła, ciasteczka, `Authorization`, `Set-Cookie` i tokeny zastępowane
    `***`. Filtr siedzi na loggerze, więc obejmuje też komunikaty, których
    autor nie przewidział, że mogą coś ujawnić.
    """

    WRAZLIWE = ("password=", "Authorization:", "Set-Cookie:", "XSRF-TOKEN")

    def __init__(self, sekrety: list[str]) -> None:
        super().__init__()
        self._sekrety = [s for s in sekrety if s]

    def filter(self, record: logging.LogRecord) -> bool:
        tresc = record.getMessage()
        for sekret in self._sekrety:
            if sekret in tresc:
                tresc = tresc.replace(sekret, "***")
        for wzorzec in self.WRAZLIWE:
            if wzorzec in tresc:
                tresc = tresc.split(wzorzec)[0] + wzorzec + " ***"
        record.msg = tresc
        record.args = ()
        return True


def _skonfiguruj_logi(opcje: Opcje) -> None:
    logging.basicConfig(
        level=getattr(logging, opcje.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    sekrety = [opcje.db_password, *(p.password for p in opcje.credentials)]
    filtr = _FiltrRedakcji(sekrety)
    for nazwa in ("", "poleasingowe", "app", "uvicorn", "uvicorn.error"):
        logging.getLogger(nazwa).addFilter(filtr)


def main() -> int:
    sciezka = pathlib.Path(os.environ.get("POLEASINGOWE_OPTIONS", SCIEZKA_OPCJI))
    try:
        opcje = wczytaj_opcje(sciezka)
    except BladKonfiguracji as exc:
        logging.basicConfig(level=logging.ERROR, stream=sys.stdout)
        log.error("%s", exc)
        return 1

    _skonfiguruj_logi(opcje)

    stan_wstepny = StanAplikacji(opcje)

    def _udalo_sie(_: list[Migracja]) -> None:
        stan_wstepny.baza_dostepna = True
        stan_wstepny.ostatni_blad_bazy = None

    def _nie_udalo_sie(powod: str) -> None:
        stan_wstepny.baza_dostepna = False
        stan_wstepny.ostatni_blad_bazy = powod

    async def _polaczenie_w_tle() -> None:
        # W tle, zeby interfejs wstal od razu i pokazal w panelu
        # diagnostycznym, DLACZEGO bazy nie ma (SPEC.md §12).
        await polacz_i_zmigruj(
            opcje.dsn,
            MIGRACJE,
            opis_polaczenia=opcje.bezpieczny_opis(),
            na_sukces=_udalo_sie,
            na_blad=_nie_udalo_sie,
        )

    aplikacja = utworz_aplikacje(opcje, zadania_tla=[_polaczenie_w_tle])
    aplikacja.state.stan = stan_wstepny

    uvicorn.run(
        aplikacja,
        # Tylko siec dodatkow: `ports` w config.yaml jest puste, wiec nic nie
        # jest publikowane poza Home Assistant (SPEC.md §7.1).
        host="0.0.0.0",
        port=PORT_INGRESS,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
