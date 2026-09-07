"""Niezmienniki domknięcia aukcji w domenie (SPEC.md §11.5, §11.8).

Te same reguły stoją w bazie jako `CHECK` (migracja 003). Domena ma je
powtarzać, a nie na nich polegać: adapter buduje `Source` zanim cokolwiek
dotknie bazy, więc błędna drabinka ma się ujawnić w miejscu, w którym
powstała, a nie dopiero przy zapisie.
"""

from __future__ import annotations

import pytest

from app.domain.entities import Source
from app.domain.enums import AuthState, BidCountSemantics


def zrodlo(**nadpisz: object) -> Source:
    domyslne: dict[str, object] = {
        "key": "test",
        "name": "Test",
        "enabled": True,
        "sweep_interval_seconds": 21600,
        "rate_limit_per_minute": 30,
        "floor_seconds": 60,
        "auth_state": AuthState.ANONYMOUS,
        "consecutive_auth_failures": 0,
        "overtime_window_seconds": 0,
        "overtime_extension_seconds": 0,
        "overtime_cap_seconds": None,
    }
    return Source(**{**domyslne, **nadpisz})  # type: ignore[arg-type]


def test_domyslna_drabinka_jest_ta_z_pierwotnego_spec() -> None:
    """Niezmierzone źródło dostaje siatkę wyjściową, a nie żadnej (§11.5)."""
    assert zrodlo().closing_ladder_seconds == (2, 5, 10, 20, 40)


@pytest.mark.parametrize(
    "drabinka",
    [(5, 2), (2, 2, 5), (0, 5), (2, -5), (2, 5, 5)],
)
def test_drabinka_musi_rosnac_i_byc_dodatnia(drabinka: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="closing_ladder_seconds"):
        zrodlo(closing_ladder_seconds=drabinka)


def test_pusta_drabinka_przechodzi() -> None:
    """Pusta znaczy „jeden odpyt i koniec", a nie błąd konfiguracji (§8.2)."""
    assert zrodlo(closing_ladder_seconds=()).closing_ladder_seconds == ()


def test_zmierzone_siatki_z_rekonesansu_sa_poprawne() -> None:
    """RECON.md §3.6 — wartości, które faktycznie trafią do `source`."""
    assert zrodlo(closing_ladder_seconds=(2, 5, 8, 11, 14))  # autoprzetarg
    assert zrodlo(closing_ladder_seconds=(2, 30))  # EFL, poleasingowe


def test_ttl_historii_ofert_dodatni_albo_none() -> None:
    assert zrodlo(bid_history_ttl_seconds=None).bid_history_ttl_seconds is None
    assert zrodlo(bid_history_ttl_seconds=120).bid_history_ttl_seconds == 120
    with pytest.raises(ValueError, match="bid_history_ttl_seconds"):
        zrodlo(bid_history_ttl_seconds=0)


def test_bid_gap_liczymy_tylko_gdy_licznik_liczy_oferty() -> None:
    """SPEC.md §11.8 — dla licytacji proxy `bid_gap` mierzyłby co innego.

    RECON.md §3.5: w EFL cena poszła z 48 600 na 51 600 zł przy niezmienionym
    `bid_count`, bo wiersz uczestnika aktualizuje się w miejscu.
    """
    assert zrodlo(bid_count_semantics=BidCountSemantics.OFFERS).liczy_oferty
    assert not zrodlo(bid_count_semantics=BidCountSemantics.PARTICIPANTS).liczy_oferty


def test_brak_dowodu_znaczy_nie_liczymy() -> None:
    """Domyślne UNKNOWN ma dawać `NULL`, nie zero czytane jak „komplet"."""
    domyslne = zrodlo()
    assert domyslne.bid_count_semantics is BidCountSemantics.UNKNOWN
    assert not domyslne.liczy_oferty
