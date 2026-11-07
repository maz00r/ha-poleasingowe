"""Automatyczna wycena z Otomoto — pracownik w tle (0.34.0).

Ranking okazji ma sens tylko wtedy, gdy wycenione są wszystkie aukcje, a nie
te, na które ktoś kliknął. Każda wycena to płatne żądanie do modelu, a koszt
ogranicza dobowy limit prób (`wycena_auto_limit_dzienny`).

**Równolegle (0.40.0).** Najdłużej trwa odpowiedź modelu, więc kilka wycen
idzie naraz (`wycena_auto_rownolegle`). Otomoto to cudzy serwis czytany bez
API, a jego strony pobiera się na początku wyceny — dlatego przerwa
(`wycena_auto_przerwa_s`) dzieli **starty** kolejnych wycen, a nie ich końce.
Tempo pytań do Otomoto zostaje takie, jak ustawi przerwa.

**Dwa rodzaje porażki.** Auto nieznane Otomoto dotyczy tej jednej aukcji:
próba zostaje zapisana i kolejka idzie dalej. Blokada Otomoto albo awaria
dostawcy AI dotyczy wszystkich — wtedy cały automat czeka (Otomoto
`wycena_pauza_otomoto_minuty`, dostawca AI `wycena_pauza_minuty`), zamiast
utrwalać blokadę
i oznaczać kolejne auta jako nie do wyceny. Próba zapisuje
się także wtedy: inaczej aukcja, która sama wywołuje taki błąd, stałaby na
początku kolejki bez końca.

W droższych godzinach dostawcy (`ai_klienci.szczyt_dostawcy`) automat czeka:
każdą z tych aukcji można wycenić kilka godzin później za połowę ceny.

Wyceny idą przez wspólne `ZadaniaWyceny`, więc karta aukcji pokazuje ten sam
pasek postępu, a kliknięcie w trakcie nie startuje drugiej płatnej wyceny.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.application.ports import FabrykaKontekstu
from app.infrastructure.ai_klienci import Szczyt
from app.infrastructure.otomoto import BladDostepuOtomoto
from app.infrastructure.wycena_ai import BladDostawcyAI
from app.infrastructure.zadania_wyceny import Postep, ZadaniaWyceny

log = logging.getLogger(__name__)

PRZERWA_S = 45.0
"""Między startami wycen — około 2-3 strony wyników Otomoto na minutę.
W aplikacji ustawiane opcją `wycena_auto_przerwa_s`."""
ROWNOLEGLE = 3
"""Ile wycen naraz; w aplikacji opcja `wycena_auto_rownolegle`."""
PAUZA_S = 3600.0
"""Po blokadzie Otomoto; w aplikacji ustawiane opcją
`wycena_pauza_otomoto_minuty`."""
PAUZA_AI_S = 300.0
"""Po awarii dostawcy AI; w aplikacji ustawiane opcją `wycena_pauza_minuty`."""
PUSTA_KOLEJKA_S = 300.0
LIMIT_WYCZERPANY_S = 900.0
PO_AWARII_KROKU_S = 60.0
OKNO_KOLEJKI = 20
"""Ile aukcji pobrać naraz: te, które są właśnie wyceniane (przez automat albo
z karty), są pomijane."""
MAKS_DLUGOSC_BLEDU = 500

Praca = Callable[[int, Postep], Awaitable[None]]


def _teraz() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(slots=True)
class StanAutoWyceny:
    """Co automat robi teraz — dla strony okazji."""

    pauza_do: dt.datetime | None = None
    powod_pauzy: str | None = None
    szczyt_do: dt.datetime | None = None
    limit_wyczerpany: bool = False


class AutoWycena:
    """Pracownik w tle z kilkoma wycenami naraz; startuje obok dispatchera."""

    def __init__(
        self,
        fabryka: FabrykaKontekstu,
        zadania: ZadaniaWyceny,
        praca: Praca,
        *,
        limit_dzienny: int,
        przerwa_s: float = PRZERWA_S,
        rownolegle: int = ROWNOLEGLE,
        pauza_ai_s: float = PAUZA_AI_S,
        pauza_otomoto_s: float = PAUZA_S,
        rodzaje: Sequence[str] | None = None,
        zegar: Callable[[], dt.datetime] = _teraz,
        szczyt: Szczyt | None = None,
    ) -> None:
        self._fabryka = fabryka
        self._zadania = zadania
        self._praca = praca
        self._limit_dzienny = limit_dzienny
        self._przerwa_s = przerwa_s
        self._rownolegle = max(1, rownolegle)
        self._pauza_ai_s = pauza_ai_s
        self._pauza_otomoto_s = pauza_otomoto_s
        self._rodzaje = rodzaje
        self._zegar = zegar
        self._szczyt = szczyt
        self._w_toku: dict[int, asyncio.Task[None]] = {}
        self._ostatni_start: dt.datetime | None = None
        self._zwolnione = asyncio.Event()
        self.stan = StanAutoWyceny()

    @property
    def limit_dzienny(self) -> int:
        return self._limit_dzienny

    @property
    def w_toku(self) -> int:
        return len(self._w_toku)

    async def uruchom(self) -> None:
        log.info(
            "automatyczna wycena wystartowała (limit %s, naraz %s)",
            self._limit_dzienny,
            self._rownolegle,
        )
        try:
            while True:
                try:
                    czekaj = await self.krok()
                except Exception:
                    # Awaria automatu nie może zatrzymać dispatchera ani UI.
                    log.exception("krok automatycznej wyceny nie powiódł się")
                    czekaj = PO_AWARII_KROKU_S
                await self._czekaj(czekaj)
        except asyncio.CancelledError:
            log.info("automatyczna wycena zatrzymana")
            for zadanie in self._w_toku.values():
                zadanie.cancel()
            raise

    async def _czekaj(self, sekundy: float) -> None:
        """Śpi, ale budzi się wcześniej, gdy któraś wycena się skończy."""
        if sekundy <= 0:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._zwolnione.wait(), sekundy)

    async def dokoncz(self) -> None:
        """Czeka na wyceny w toku (testy, zamknięcie)."""
        while self._w_toku:
            await asyncio.gather(*self._w_toku.values(), return_exceptions=True)

    async def krok(self) -> float:
        """Startuje najwyżej jedną wycenę. Zwraca sekundy do następnego kroku.

        Wycena biegnie dalej w tle; jej wynik zapisuje `_wykonaj`.
        """
        # Czyścimy tu, nie przed snem: koniec wyceny między krokiem a snem
        # i tak ma obudzić pętlę.
        self._zwolnione.clear()
        stan = self.stan
        if stan.pauza_do is not None:
            zostalo = (stan.pauza_do - self._zegar()).total_seconds()
            if zostalo > 0:
                return zostalo
            stan.pauza_do = None
            stan.powod_pauzy = None

        teraz = self._zegar()
        stan.szczyt_do = None if self._szczyt is None else self._szczyt(teraz)
        if stan.szczyt_do is not None:
            return (stan.szczyt_do - teraz).total_seconds()

        if len(self._w_toku) >= self._rownolegle:
            # Obudzi nas koniec którejkolwiek z nich.
            return PUSTA_KOLEJKA_S
        if self._ostatni_start is not None:
            zostalo = (
                self._ostatni_start + dt.timedelta(seconds=self._przerwa_s) - teraz
            ).total_seconds()
            if zostalo > 0:
                return zostalo

        async with self._fabryka() as kontekst:
            # Wyceny w toku jeszcze nie zapisały próby, a też się liczą.
            stan.limit_wyczerpany = (
                await kontekst.zapytania.proby_wycen_24h() + len(self._w_toku)
                >= self._limit_dzienny
            )
            if stan.limit_wyczerpany:
                return LIMIT_WYCZERPANY_S
            kolejka = await (
                kontekst.zapytania.kolejka_wycen(OKNO_KOLEJKI)
                if self._rodzaje is None
                else kontekst.zapytania.kolejka_wycen(OKNO_KOLEJKI, self._rodzaje)
            )

        auction_id = next((i for i in kolejka if self._zadania.stan(i) is None), None)
        if auction_id is None:
            return PUSTA_KOLEJKA_S

        self._ostatni_start = teraz
        self._w_toku[auction_id] = asyncio.create_task(
            self._wykonaj(auction_id), name=f"auto-wycena-{auction_id}"
        )
        return self._przerwa_s

    async def _wykonaj(self, auction_id: int) -> None:
        try:
            await self._wycen(auction_id)
        except Exception:
            log.exception("automatyczna wycena aukcji %s nie powiodła się", auction_id)
        finally:
            self._w_toku.pop(auction_id, None)
            self._zwolnione.set()

    async def _wycen(self, id_aukcji: int) -> None:
        async def praca(postep: Postep) -> None:
            await self._praca(id_aukcji, postep)

        zadanie = self._zadania.uruchom(id_aukcji, praca)
        assert zadanie.zadanie is not None
        await zadanie.zadanie
        self._zadania.zakoncz(id_aukcji)

        blad = zadanie.blad
        async with self._fabryka() as kontekst, kontekst.uow as uow:
            await uow.wycena.zapisz_probe(
                id_aukcji,
                self._zegar(),
                None if blad is None else blad[:MAKS_DLUGOSC_BLEDU],
            )
        if isinstance(zadanie.wyjatek, BladDostepuOtomoto | BladDostawcyAI):
            pauza = (
                self._pauza_ai_s
                if isinstance(zadanie.wyjatek, BladDostawcyAI)
                else self._pauza_otomoto_s
            )
            # Wyceny już w toku kończą się same; nowe nie startują.
            self.stan.pauza_do = self._zegar() + dt.timedelta(seconds=pauza)
            self.stan.powod_pauzy = blad
            log.warning("automatyczna wycena wstrzymana na %.0f s: %s", pauza, blad)
