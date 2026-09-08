"""Rejestr adapterów (SPEC.md §6.2 — Registry).

Mapa `key -> fabryka adaptera`, wypełniana deklaratywnie. Włączanie
i wyłączanie źródeł z opcji add-onu **bez zmiany kodu**.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.application.ports import AuctionSource
from app.infrastructure.sources.autoprzetarg.source import AutoprzetargSource
from app.infrastructure.sources.efl.source import EflSource
from app.infrastructure.sources.poleasingowe.source import PoleasingoweSource

REJESTR: Mapping[str, Callable[[], AuctionSource]] = {
    AutoprzetargSource.key: AutoprzetargSource.utworz,
    EflSource.key: EflSource.utworz,
    PoleasingoweSource.key: PoleasingoweSource.utworz,
}


def utworz(key: str) -> AuctionSource:
    """Tworzy adapter o podanym kluczu."""
    fabryka = REJESTR.get(key)
    if fabryka is None:
        znane = ", ".join(sorted(REJESTR)) or "(brak)"
        raise KeyError(f"nieznane źródło {key!r}; zarejestrowane: {znane}")
    return fabryka()
