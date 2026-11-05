"""Pomiar czasu etapów wyceny."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from app.infrastructure.otomoto import PojazdDoPorownania
from app.infrastructure.wycena_otomoto import Stoper


def _zegar(chwile: list[float]) -> Iterator[float]:
    return iter(chwile)


def test_etap_trwa_do_poczatku_nastepnego_i_przekazuje_postep() -> None:
    chwile = _zegar([0.0, 2.0, 12.0, 100.0, 100.0])
    zgloszone: list[tuple[str, float]] = []
    stoper = Stoper(lambda e, u: zgloszone.append((e, u)), lambda: next(chwile))

    stoper("Otomoto", 0.1)
    stoper("Model AI", 0.75)

    assert zgloszone == [("Otomoto", 0.1), ("Model AI", 0.75)]
    assert stoper.opis() == (
        "razem 100.0 s — Dane z bazy: 2.0 s, Otomoto: 10.0 s, Model AI: 88.0 s"
    )


def test_dopasowanie_jest_podane_osobno_takze_po_bledzie() -> None:
    chwile = _zegar([0.0, 1.0, 4.0, 5.0, 5.0])
    stoper = Stoper(lambda _e, _u: None, lambda: next(chwile))

    async def zepsuty(_p: PojazdDoPorownania, _m: object) -> str | None:
        raise RuntimeError("awaria")

    with pytest.raises(RuntimeError):
        asyncio.run(stoper.mierz_dopasowanie(zepsuty)(None, ()))  # type: ignore[arg-type]

    assert "dopasowanie modelu przez AI: 3.0 s" in stoper.opis()
