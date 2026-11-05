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

import asyncio
import logging
import os
import pathlib
import sys

import uvicorn

from app.application.ports import FabrykaKontekstu
from app.application.use_cases.rejestracja import zarejestruj_zrodla
from app.infrastructure import dziennik
from app.infrastructure.ai_klienci import Szczyt, szczyt_dostawcy, utworz_klienta
from app.infrastructure.auto_wycena import AutoWycena
from app.infrastructure.dokumenty import Dokumenty
from app.infrastructure.kopia import KopiaZapasowa
from app.infrastructure.migracja import EksporterMigracji
from app.infrastructure.otomoto import Otomoto
from app.infrastructure.persistence.migrations import Migracja
from app.infrastructure.persistence.polaczenie import polacz_i_zmigruj
from app.infrastructure.persistence.pula import PgFabrykaKontekstu
from app.infrastructure.redakcja import Redakcja
from app.infrastructure.scheduler.budzik import BudzikOdroczony
from app.infrastructure.scheduler.dispatcher import Dispatcher
from app.infrastructure.sources import registry
from app.infrastructure.sources.parametry import zbuduj_source
from app.infrastructure.supervisor.options import (
    SCIEZKA_NADPISAN,
    SCIEZKA_OPCJI,
    BladKonfiguracji,
    Opcje,
    wczytaj_opcje,
)
from app.infrastructure.wycena_ai import WycenaAI
from app.infrastructure.wycena_otomoto import wycen_z_otomoto
from app.infrastructure.zadania_wyceny import Postep, ZadaniaWyceny
from app.infrastructure.zdjecia import GaleriaZdjec
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


LIMIT_ZAMKNIECIA_S = 5
"""Ile czekamy na otwarte połączenia przy restarcie z Diagnostyki."""


def _skonfiguruj_logi(opcje: Opcje) -> None:
    format_logu = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, opcje.log_level),
        format=format_logu,
        stream=sys.stdout,
    )
    # pypdf zgłasza każdy wpis fontu z mapowaniem na rzadkie znaki fonetyczne
    # (U+10780, U+1DF00), których nie ma w polskich dokumentach. Dziesiątki
    # linii na dokument zasłaniały prawdziwe błędy, a plik nie do odczytania
    # i tak zgłasza `tekst_pdf`.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    sekrety = [
        opcje.db_password,
        opcje.ai_api_key,
        opcje.openai_api_key,
        opcje.apify_token,
        *(p.password for p in opcje.credentials),
    ]
    filtr = _FiltrRedakcji(sekrety)
    for nazwa in ("", "poleasingowe", "app", "uvicorn", "uvicorn.error"):
        logging.getLogger(nazwa).addFilter(filtr)
    # Kopia logu na dysku — do podglądu w Diagnostyce (0.32.0). `docker logs`
    # nie jest dostępny z wnętrza kontenera, a znika przy każdym deployu.
    plik = dziennik.dodaj_plik(format_logu, filtr)
    if plik is not None:
        log.info("log zapisywany także do %s", plik)


def _wycena(opcje: Opcje) -> WycenaAI | None:
    """Wycena AI, jeśli użytkownik podał klucz. Bez klucza — po prostu jej nie ma.

    Zły dostawca albo brak adresu NIE zatrzymuje add-onu: zbieranie danych
    jest tu funkcją główną, a wycena dodatkiem. Powód ląduje w logu i na
    karcie aukcji (SPEC.md §2 pkt 8).
    """
    if not opcje.klucz_ai:
        return None
    try:
        klient = utworz_klienta(
            opcje.ai_provider,
            opcje.klucz_ai,
            model=opcje.ai_model,
            base_url=opcje.ai_base_url,
        )
    except ValueError as exc:
        log.error("wycena AI wyłączona — %s", exc)
        return None
    log.info("wycena AI: dostawca %s, model %s", opcje.ai_provider, klient.model)
    return WycenaAI(klient)


def _auto_wycena(
    opcje: Opcje,
    fabryka: FabrykaKontekstu,
    zadania: ZadaniaWyceny,
    usluga: WycenaAI | None,
    otomoto: Otomoto,
    dokumenty: Dokumenty,
    szczyt: Szczyt | None = None,
) -> AutoWycena | None:
    """Pracownik wyceny w tle albo `None`, gdy nie ma czym albo nie wolno."""
    if usluga is None or not opcje.wycena_auto:
        return None

    async def praca(auction_id: int, postep: Postep) -> None:
        await wycen_z_otomoto(fabryka, usluga, otomoto, dokumenty, auction_id, postep)

    return AutoWycena(
        fabryka,
        zadania,
        praca,
        limit_dzienny=opcje.wycena_auto_limit_dzienny,
        szczyt=szczyt,
    )


def main() -> int:
    sciezka = pathlib.Path(os.environ.get("POLEASINGOWE_OPTIONS", SCIEZKA_OPCJI))
    try:
        opcje = wczytaj_opcje(sciezka, SCIEZKA_NADPISAN)
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

        # Ta sama mapa obiektow, ktora dostala galeria — uzupelniamy ja
        # w miejscu, zeby interfejs i dispatcher chodzily po jednym kliencie
        # HTTP na zrodlo, a nie po dwoch.
        adaptery_ui.update(adaptery)

        if not adaptery:
            log.warning("żadne źródło nie jest włączone — dispatcher nie startuje")
            return
        petla = Dispatcher(fabryka, adaptery, kopia=kopia, galeria=galeria)
        # Galeria korzysta z tej samej blokady i kubełka, co listy oraz
        # szczegóły. Wejście na kartę nie może ominąć limitu źródła.
        galeria.ustaw_bramke(petla.bramka_sieci)
        # Dokumenty (ekspertyzy) idą tą samą bramką co listy i zdjęcia.
        dokumenty.ustaw_bramke(petla.bramka_sieci)
        # Od tej chwili karta aukcji moze poprosic o wczesniejszy obrot.
        budzik.podepnij(petla)
        prace = [petla.uruchom(), galeria.uruchom_archiwum()]
        if auto_wycena is not None:
            prace.append(auto_wycena.uruchom())
        await asyncio.gather(*prace)

    # Galeria dzieli adaptery z dispatcherem — jeden `AsyncClient` per
    # zrodlo, tworzony raz (SPEC.md §11.3).
    adaptery_ui: dict[str, object] = {}
    budzik = BudzikOdroczony()
    galeria = GaleriaZdjec(adaptery_ui, fabryka=fabryka)  # type: ignore[arg-type]
    dokumenty = Dokumenty(adaptery_ui)
    # Wycena z Otomoto z przycisku na karcie i z automatu; od 0.32 bez Apify.
    otomoto = Otomoto()
    # Kopia zapasowa WYLACZNIE wlasnej bazy (SPEC.md §7.1) — snapshot HA
    # obejmuje tez TeslaMate, wiec nie da sie z niego odtworzyc samej naszej.
    kopia = KopiaZapasowa(opcje.dsn)
    eksporter_migracji = EksporterMigracji(kopia)
    wycena_ai = _wycena(opcje)
    # Jedno na proces: karta aukcji i automat mają widzieć te same zadania.
    zadania_wyceny = ZadaniaWyceny()
    szczyt = szczyt_dostawcy(opcje.ai_base_url)
    auto_wycena = _auto_wycena(
        opcje, fabryka, zadania_wyceny, wycena_ai, otomoto, dokumenty, szczyt
    )

    aplikacja = utworz_aplikacje(
        opcje,
        zadania_tla=[_polaczenie_w_tle],
        fabryka=fabryka,
        galeria=galeria,
        wycena_ai=wycena_ai,
        kopia=kopia,
        eksporter_migracji=eksporter_migracji,
        budzik=budzik,
    )
    aplikacja.state.stan = stan_wstepny
    aplikacja.state.dokumenty = dokumenty
    aplikacja.state.otomoto = otomoto
    aplikacja.state.zadania_wyceny = zadania_wyceny
    aplikacja.state.auto_wycena = auto_wycena
    aplikacja.state.szczyt_dostawcy = szczyt

    serwer = uvicorn.Server(
        uvicorn.Config(
            aplikacja,
            # Tylko siec dodatkow: `ports` w config.yaml jest puste, wiec nic nie
            # jest publikowane poza Home Assistant (SPEC.md §7.1).
            host="0.0.0.0",
            port=PORT_INGRESS,
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=LIMIT_ZAMKNIECIA_S,
        )
    )
    restart_zadany = False

    def _zadaj_restartu() -> None:
        nonlocal restart_zadany
        restart_zadany = True
        serwer.should_exit = True

    aplikacja.state.restart = _zadaj_restartu
    aplikacja.state.sciezka_nadpisan = SCIEZKA_NADPISAN
    serwer.run()
    if restart_zadany:
        # W tym samym procesie: w HA wyjście kończy się `halt` w skrypcie
        # `finish`, czyli zatrzymaniem dodatku, a nie jego restartem.
        log.info("restart po zmianie konfiguracji")
        os.execv(sys.executable, [sys.executable, "-m", "app.interfaces.main"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
