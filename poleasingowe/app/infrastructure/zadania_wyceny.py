"""Wycena z Otomoto w tle, z postępem dla paska na karcie (0.32.0).

Wycena z Otomoto trwa do kilku minut (taksonomia, dwie strony wyników,
ekspertyza, model AI). Trzymanie na tym jednego żądania HTTP kończyło się
pustym ekranem i timeoutem proxy; teraz kliknięcie uruchamia zadanie,
a karta co chwilę pyta o etap.

Rejestr jest w pamięci procesu — proces jest jeden (SPEC.md §11.1), a zadanie
przerwane restartem po prostu trzeba kliknąć jeszcze raz.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

Postep = Callable[[str, float], None]


@dataclass(slots=True)
class StanZadania:
    etap: str = "Uruchamianie"
    udzial: float = 0.0
    gotowe: bool = False
    blad: str | None = None
    zadanie: asyncio.Task[None] | None = field(default=None, repr=False)


class ZadaniaWyceny:
    """Najwyżej jedno zadanie na aukcję; ponowne kliknięcie zwraca to samo."""

    def __init__(self) -> None:
        self._zadania: dict[int, StanZadania] = {}

    def stan(self, auction_id: int) -> StanZadania | None:
        return self._zadania.get(auction_id)

    def zakoncz(self, auction_id: int) -> StanZadania | None:
        """Zdejmuje zakończone zadanie z rejestru (po pokazaniu wyniku)."""
        stan = self._zadania.get(auction_id)
        if stan is not None and stan.gotowe:
            return self._zadania.pop(auction_id)
        return None

    def uruchom(
        self, auction_id: int, praca: Callable[[Postep], Awaitable[None]]
    ) -> StanZadania:
        biezacy = self._zadania.get(auction_id)
        if biezacy is not None and not biezacy.gotowe:
            return biezacy
        stan = StanZadania()

        def postep(etap: str, udzial: float) -> None:
            stan.etap = etap
            stan.udzial = max(stan.udzial, min(udzial, 1.0))

        async def wykonaj() -> None:
            try:
                await praca(postep)
            except Exception as exc:
                log.exception("wycena z Otomoto aukcji %s nie powiodła się", auction_id)
                stan.blad = str(exc) or type(exc).__name__
            finally:
                stan.udzial = 1.0
                stan.gotowe = True

        stan.zadanie = asyncio.create_task(wykonaj(), name=f"wycena-{auction_id}")
        self._zadania[auction_id] = stan
        return stan
