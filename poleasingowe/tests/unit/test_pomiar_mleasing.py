"""Reguły bezpieczeństwa i harmonogram pomiaru mLeasing."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
from types import ModuleType

import pytest


def _modul() -> ModuleType:
    sciezka = pathlib.Path(__file__).resolve().parents[3] / "tools/pomiar_mleasing.py"
    spec = importlib.util.spec_from_file_location("pomiar_mleasing", sciezka)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_siatka_obejmuje_gesta_koncowke_i_dziesiec_minut_po() -> None:
    modul = _modul()
    assert modul.SIATKA[0] == -180
    assert modul.SIATKA[-1] == 600
    assert {0, 2, 5, 10, 20, 40}.issubset(modul.SIATKA)


def test_publiczne_loginy_i_dane_pojazdu_sa_redagowane() -> None:
    modul = _modul()
    wynik = modul._zredaguj(
        [{"hiddenLogin": "a***z", "amount": 1000, "userName": "Jan"}]
    )
    assert wynik == [{"hiddenLogin": "***", "amount": 1000, "userName": "***"}]


def test_czas_bez_strefy_jest_odrzucany() -> None:
    modul = _modul()
    with pytest.raises(modul.BladPomiaru, match="bez strefy"):
        modul._czas("2026-09-14T12:00:00")
    assert modul._czas("2026-09-14T12:00:00+02:00") == dt.datetime(
        2026, 9, 14, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))
    )
