"""Tempo żądań i bezpiecznik per źródło (SPEC.md §13, §10.2).

Dwie rzeczy, które chronią kogo innego:

- **Kubełek tokenów** chroni serwis. Limit bierze się z `source`, a nie ze
  stałej — poleasingowe.pl deklaruje `x-ratelimit-limit` różny per trasa
  (RECON.md §4.2), a reszta nie deklaruje nic i wtedy sami wybieramy
  ostrożnie.
- **Bezpiecznik** chroni nas. Serwis, który przestał odpowiadać, ma zostać
  odstawiony z narastającą przerwą, żeby jedna awaria nie zjadła całego
  przebiegu — pozostałe źródła pracują dalej (§13).

Zegar wchodzi wstrzyknięciem, nie przez `time.monotonic()` w środku: inaczej
test tempa musiałby naprawdę spać, a test bezpiecznika czekać pół godziny.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

Zegar = Callable[[], float]
Jitter = Callable[[], float]

MAKS_JITTER_S = 0.5
"""SPEC.md §13 — rate limiting **z jitterem**.

Bez niego kilka aukcji kończących się o tej samej sekundzie (a poleasingowe.pl
zamyka je partiami, RECON.md §4.2) uderza w serwis idealnie równo, co wygląda
jak atak i marnuje przepustowość kubełka na kolizje.
"""


class KubelekTokenow:
    """Klasyczny token bucket: pojemność = limit na minutę, dolewanie ciągłe.

    Pojemność równa limitowi pozwala na krótki zryw po okresie ciszy — tak
    właśnie wygląda przemiat listy po godzinach bezczynności — a średnia
    w dłuższym oknie i tak nie przekracza limitu.
    """

    def __init__(
        self,
        na_minute: int,
        *,
        zegar: Zegar = time.monotonic,
        jitter: Jitter | None = None,
    ) -> None:
        if na_minute <= 0:
            raise ValueError(f"limit tempa musi być dodatni, dostałem {na_minute}")
        self._pojemnosc = float(na_minute)
        self._tempo_na_s = na_minute / 60.0
        self._zegar = zegar
        self._jitter = jitter if jitter is not None else _domyslny_jitter
        self._tokeny = float(na_minute)
        self._ostatnie_dolanie = zegar()

    def _dolej(self) -> None:
        teraz = self._zegar()
        uplynelo = max(0.0, teraz - self._ostatnie_dolanie)
        self._ostatnie_dolanie = teraz
        self._tokeny = min(self._pojemnosc, self._tokeny + uplynelo * self._tempo_na_s)

    @property
    def dostepne(self) -> float:
        self._dolej()
        return self._tokeny

    def ile_czekac(self) -> float:
        """Ile sekund do najbliższego wolnego tokenu. `0.0` znaczy „teraz".

        Jitter doliczamy **tylko wtedy, gdy i tak czekamy**. Doklejanie go do
        żądania, które mogło pójść od razu, spowalniałoby końcówkę aukcji bez
        żadnego zysku dla serwisu.
        """
        self._dolej()
        if self._tokeny >= 1.0:
            return 0.0
        brakuje = 1.0 - self._tokeny
        return brakuje / self._tempo_na_s + self._jitter() * MAKS_JITTER_S

    def zuzyj(self, *, pozycz: bool = False) -> None:
        """Zabiera token; domknięcie może zapisać dług do przyszłego tempa.

        Poprzednia implementacja obcinała saldo do zera. W praktyce wyjątek
        dla końcówki aukcji przepuszczał żądanie, ale kolejne nie czekało za
        nie, więc limit nie był rozliczony. Ujemne saldo jest tym długiem.
        """
        self._dolej()
        self._tokeny = self._tokeny - 1.0 if pozycz else max(0.0, self._tokeny - 1.0)


def _domyslny_jitter() -> float:
    # Losowość wyłącznie do rozsuwania żądań w czasie — nic tu nie zależy
    # od nieprzewidywalności, więc `random` w zupełności wystarcza.
    return random.random()


@dataclass(slots=True)
class Bezpiecznik:
    """Circuit breaker per źródło (SPEC.md §10.2, §13).

    Stan żyje w pamięci procesu, nie w bazie — i to jest świadoma różnica
    wobec licznika `AUTH_LOCKED`. Tamten musi przeżyć restart, bo chroni
    **konto w serwisie** i pętla restartów obeszłaby limit. Ten chroni tylko
    bieżący przebieg: po restarcie i tak zaczynamy od zera, a odstawianie
    źródła na podstawie awarii sprzed tygodnia byłoby zgadywaniem.
    """

    prog_bledow: int = 3
    backoff_start_s: float = 30.0
    backoff_maks_s: float = 1800.0
    kolejne_bledy: int = field(default=0, init=False)
    pauza_do: float = field(default=0.0, init=False)

    def zglos_sukces(self) -> None:
        self.kolejne_bledy = 0
        self.pauza_do = 0.0

    def zglos_blad(self, teraz: float) -> None:
        """Po progu błędów źródło pauzuje z backoffem wykładniczym."""
        self.kolejne_bledy += 1
        if self.kolejne_bledy < self.prog_bledow:
            return
        ponad_prog = self.kolejne_bledy - self.prog_bledow
        odstep = min(self.backoff_start_s * (2**ponad_prog), self.backoff_maks_s)
        self.pauza_do = teraz + odstep

    def otwarty(self, teraz: float) -> bool:
        """Czy źródło jest odstawione. Otwarty bezpiecznik wstrzymuje odpyty."""
        return teraz < self.pauza_do

    def ile_pauzy(self, teraz: float) -> float:
        return max(0.0, self.pauza_do - teraz)
