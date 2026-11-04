"""Automatyczna wycena z Otomoto — pracownik w tle (0.34.0).

Ranking okazji ma sens tylko wtedy, gdy wycenione są wszystkie aukcje, a nie
te, na które ktoś kliknął. Pracownik bierze je pojedynczo, z przerwą: Otomoto
to cudzy serwis czytany bez API, a każda wycena to płatne żądanie do modelu.
Koszt ogranicza dobowy limit prób (`wycena_auto_limit_dzienny`).

**Dwa rodzaje porażki.** Auto nieznane Otomoto dotyczy tej jednej aukcji:
próba zostaje zapisana i kolejka idzie dalej. Blokada Otomoto albo awaria
dostawcy AI dotyczy wszystkich — wtedy cały automat czeka godzinę, zamiast
utrwalać blokadę i oznaczać kolejne auta jako nie do wyceny. Próba zapisuje
się także wtedy: inaczej aukcja, która sama wywołuje taki błąd, stałaby na
początku kolejki bez końca.

W droższych godzinach dostawcy (`ai_klienci.szczyt_dostawcy`) automat czeka:
każdą z tych aukcji można wycenić kilka godzin później za połowę ceny.

Wyceny idą przez wspólne `ZadaniaWyceny`, więc karta aukcji pokazuje ten sam
pasek postępu, a kliknięcie w trakcie nie startuje drugiej płatnej wyceny.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.application.ports import FabrykaKontekstu
from app.infrastructure.ai_klienci import Szczyt
from app.infrastructure.otomoto import BladDostepuOtomoto
from app.infrastructure.wycena_ai import BladDostawcyAI
from app.infrastructure.zadania_wyceny import Postep, ZadaniaWyceny

log = logging.getLogger(__name__)

PRZERWA_S = 45.0
"""Między wycenami — około 2-3 strony wyników Otomoto na minutę."""
PAUZA_S = 3600.0
"""Po awarii dotyczącej wszystkich aukcji."""
PUSTA_KOLEJKA_S = 300.0
LIMIT_WYCZERPANY_S = 900.0
PO_AWARII_KROKU_S = 60.0
OKNO_KOLEJKI = 10
"""Ile aukcji pobrać naraz: te, które właśnie wycenia użytkownik, są pomijane."""
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
    """Pojedynczy pracownik; startuje obok dispatchera (`main.py`)."""

    def __init__(
        self,
        fabryka: FabrykaKontekstu,
        zadania: ZadaniaWyceny,
        praca: Praca,
        *,
        limit_dzienny: int,
        przerwa_s: float = PRZERWA_S,
        zegar: Callable[[], dt.datetime] = _teraz,
        szczyt: Szczyt | None = None,
    ) -> None:
        self._fabryka = fabryka
        self._zadania = zadania
        self._praca = praca
        self._limit_dzienny = limit_dzienny
        self._przerwa_s = przerwa_s
        self._zegar = zegar
        self._szczyt = szczyt
        self.stan = StanAutoWyceny()

    @property
    def limit_dzienny(self) -> int:
        return self._limit_dzienny

    async def uruchom(self) -> None:
        log.info("automatyczna wycena wystartowała (limit %s)", self._limit_dzienny)
        try:
            while True:
                try:
                    czekaj = await self.krok()
                except Exception:
                    # Awaria automatu nie może zatrzymać dispatchera ani UI.
                    log.exception("krok automatycznej wyceny nie powiódł się")
                    czekaj = PO_AWARII_KROKU_S
                await asyncio.sleep(czekaj)
        except asyncio.CancelledError:
            log.info("automatyczna wycena zatrzymana")
            raise

    async def krok(self) -> float:
        """Jedna wycena. Zwraca liczbę sekund do następnego kroku."""
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

        async with self._fabryka() as kontekst:
            stan.limit_wyczerpany = (
                await kontekst.zapytania.proby_wycen_24h() >= self._limit_dzienny
            )
            if stan.limit_wyczerpany:
                return LIMIT_WYCZERPANY_S
            kolejka = await kontekst.zapytania.kolejka_wycen(OKNO_KOLEJKI)

        auction_id = next((i for i in kolejka if self._zadania.stan(i) is None), None)
        if auction_id is None:
            return PUSTA_KOLEJKA_S

        async def praca(postep: Postep) -> None:
            await self._praca(auction_id, postep)

        zadanie = self._zadania.uruchom(auction_id, praca)
        assert zadanie.zadanie is not None
        await zadanie.zadanie
        self._zadania.zakoncz(auction_id)

        blad = zadanie.blad
        async with self._fabryka() as kontekst, kontekst.uow as uow:
            await uow.wycena.zapisz_probe(
                auction_id,
                self._zegar(),
                None if blad is None else blad[:MAKS_DLUGOSC_BLEDU],
            )
        if isinstance(zadanie.wyjatek, BladDostepuOtomoto | BladDostawcyAI):
            stan.pauza_do = self._zegar() + dt.timedelta(seconds=PAUZA_S)
            stan.powod_pauzy = blad
            log.warning("automatyczna wycena wstrzymana na godzinę: %s", blad)
            return PAUZA_S
        return self._przerwa_s
