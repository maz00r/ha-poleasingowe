"""Enumeracje domenowe.

SPEC.md §6.3 zabrania magicznych stringów. SPEC.md §8.2 zabrania typów enum
PostgreSQL („bo ich migracje bolą") — w bazie są to `text` z `CHECK`, a lista
dozwolonych wartości pochodzi stąd. Migracja generuje `CHECK` z tych wartości,
żeby nie rozjechały się dwa źródła prawdy.
"""

from __future__ import annotations

from enum import StrEnum


class AuctionStatus(StrEnum):
    """Stan aukcji widziany przez nas, nie przez serwis."""

    ACTIVE = "ACTIVE"
    ENDING = "ENDING"
    """Po `ends_at`, w trakcie domykania (SPEC.md §11.5)."""
    ENDED = "ENDED"
    DISAPPEARED = "DISAPPEARED"
    """Zniknęła z serwisu bez pokazania ceny końcowej."""


class FinalPriceState(StrEnum):
    """Jakość pomiaru ceny końcowej (SPEC.md §11.5).

    Rozróżnienie jest kluczowe dla `v_market_stats` (§9): mediana licząca
    `LAST_SEEN` jako pewne to zafałszowany obraz rynku.
    """

    UNKNOWN = "UNKNOWN"
    """Aukcja jeszcze trwa albo domykanie się nie zaczęło."""
    CONFIRMED = "CONFIRMED"
    """Cena odczytana ze strony PO zakończeniu."""
    LAST_SEEN = "LAST_SEEN"
    """Ostatnia obserwacja przed zamknięciem. Dolne oszacowanie."""


class PollTier(StrEnum):
    """Kubełek harmonogramu (SPEC.md §11.2). Interwały są w `PollingPolicy`."""

    IDLE = "IDLE"
    """Nieobserwowana — nie odpytywana pojedynczo w ogóle."""
    FAR = "FAR"
    NEAR = "NEAR"
    ENDGAME = "ENDGAME"
    CLOSING = "CLOSING"
    """Po `ends_at`, faza czujki dogrywki lub drabinki (SPEC.md §11.5)."""


class AuthState(StrEnum):
    """Stan uwierzytelnienia źródła (SPEC.md §10.2)."""

    ANONYMOUS = "ANONYMOUS"
    """Źródło nie wymaga logowania do odczytu."""
    OK = "OK"
    EXPIRED = "EXPIRED"
    LOCKED = "LOCKED"
    """Przekroczony twardy limit 3 nieudanych logowań. Wymaga resetu z UI."""


class Currency(StrEnum):
    """Waluty, w których serwisy podają kwoty (RECON.md §4.2)."""

    PLN = "PLN"
    EUR = "EUR"
