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

from app.application.use_cases.rejestracja import zarejestruj_zrodla
from app.infrastructure.persistence.migrations import Migracja
from app.infrastructure.persistence.polaczenie import polacz_i_zmigruj
from app.infrastructure.persistence.pula import PgFabrykaKontekstu
from app.infrastructure.redakcja import Redakcja
from app.infrastructure.scheduler.dispatcher import Dispatcher
from app.infrastructure.sources import registry
from app.infrastructure.sources.parametry import zbuduj_source
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

    Sama reguła siedzi w `infrastructure/redakcja.py`, wspólnie ze zrzutami
    diagnostycznymi — spec wymaga, żeby zrzuty przechodziły przez **ten sam**
    filtr co logi, a dwie osobne listy wzorców rozjeżdżają się po cichu.

    Filtr stoi na loggerze, nie w miejscach wywołań, więc obejmuje też
    komunikaty, o których autor nie pomyślał, że mogą coś ujawnić — łącznie
    z tymi z bibliotek.
    """

    def __init__(self, sekrety: list[str]) -> None:
        super().__init__()
        self._redakcja = Redakcja(sekrety)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redakcja.zastosuj(record.getMessage())
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
    fabryka = PgFabrykaKontekstu(opcje.dsn, opis=opcje.bezpieczny_opis())

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
        # Pula otwiera sie DOPIERO po udanej migracji. Otwarta wczesniej
        # dobijalaby sie do bazy rownolegle z petla ponowien, czyli dokladnie
        # tak, jak SPEC.md §2 pkt 8 zabrania traktowac wspoldzielony serwer.
        await fabryka.otworz()
        await _wystartuj_dispatcher()

    async def _wystartuj_dispatcher() -> None:
        """Rejestruje źródła i uruchamia pętlę harmonogramu (SPEC.md §11.1).

        Dopiero po migracjach i po otwarciu puli — dispatcher od pierwszego
        obrotu potrzebuje i schematu, i połączeń.
        """
        wlaczone = [z for z in opcje.sources if z.enabled]
        adaptery = {}
        for wpis in wlaczone:
            try:
                adaptery[wpis.key] = registry.utworz(wpis.key)
            except KeyError as exc:
                # Zła nazwa źródła w opcjach nie ma zatrzymywać add-onu —
                # pozostałe źródła mają pracować dalej (SPEC.md §13).
                log.error("%s", exc)

        async with fabryka() as kontekst, kontekst.uow as uow:
            await zarejestruj_zrodla(
                uow,
                [
                    zbuduj_source(
                        wpis.key,
                        enabled=True,
                        rate_limit_per_minute=wpis.rate_limit_per_minute,
                        floor_seconds=wpis.floor_seconds,
                    )
                    for wpis in wlaczone
                    if wpis.key in adaptery
                ],
            )

        if not adaptery:
            log.warning("żadne źródło nie jest włączone — dispatcher nie startuje")
            return
        await Dispatcher(fabryka, adaptery).uruchom()

    aplikacja = utworz_aplikacje(
        opcje, zadania_tla=[_polaczenie_w_tle], fabryka=fabryka
    )
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
