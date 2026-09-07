"""Kubełek tokenów i bezpiecznik (SPEC.md §13, §10.2).

Zegar jest wstrzykiwany, więc żaden z tych testów nie śpi. Gdyby spał,
sprawdzenie backoffu do pół godziny trwałoby pół godziny — a taki test
i tak zostałby wyłączony przy pierwszym pośpiechu.
"""

from __future__ import annotations

import pytest

from app.infrastructure.scheduler.tempo import (
    MAKS_JITTER_S,
    Bezpiecznik,
    KubelekTokenow,
)


class ZegarSterowany:
    """Monotoniczny zegar, który przesuwa się tylko na żądanie."""

    def __init__(self) -> None:
        self.teraz = 1000.0

    def __call__(self) -> float:
        return self.teraz

    def przesun(self, o_sekund: float) -> None:
        self.teraz += o_sekund


def kubelek(
    na_minute: int, zegar: ZegarSterowany, jitter: float = 0.0
) -> KubelekTokenow:
    return KubelekTokenow(na_minute, zegar=zegar, jitter=lambda: jitter)


# --------------------------------------------------------------------------
# Kubełek tokenów
# --------------------------------------------------------------------------


def test_swiezy_kubelek_przepuszcza_od_razu() -> None:
    """Pojemność równa limitowi pozwala na zryw po okresie ciszy.

    Tak wygląda przemiat listy po godzinach bezczynności — i tak ma wyglądać.
    """
    zegar = ZegarSterowany()
    k = kubelek(60, zegar)
    assert k.ile_czekac() == 0.0
    assert k.dostepne == 60


def test_zuzyty_kubelek_kaze_czekac() -> None:
    zegar = ZegarSterowany()
    k = kubelek(60, zegar)  # 60/min = 1 token na sekundę
    for _ in range(60):
        assert k.ile_czekac() == 0.0
        k.zuzyj()

    assert k.ile_czekac() == pytest.approx(1.0, abs=0.01)


def test_tokeny_dolewaja_sie_z_uplywem_czasu() -> None:
    zegar = ZegarSterowany()
    k = kubelek(60, zegar)
    for _ in range(60):
        k.zuzyj()
    assert k.dostepne == pytest.approx(0.0, abs=0.01)

    zegar.przesun(10)
    assert k.dostepne == pytest.approx(10.0, abs=0.01)
    assert k.ile_czekac() == 0.0


def test_kubelek_nie_przelewa_sie_ponad_pojemnosc() -> None:
    """Doba ciszy nie ma dawać doby kredytu — to byłby zryw, nie limit."""
    zegar = ZegarSterowany()
    k = kubelek(30, zegar)
    zegar.przesun(86_400)
    assert k.dostepne == 30


def test_limit_jest_przestrzegany_w_dluzszym_oknie() -> None:
    """Sto żądań przy limicie 60/min ma zająć co najmniej ~40 s czekania."""
    zegar = ZegarSterowany()
    k = kubelek(60, zegar)
    laczne_czekanie = 0.0
    for _ in range(100):
        czekaj = k.ile_czekac()
        laczne_czekanie += czekaj
        zegar.przesun(czekaj)
        k.zuzyj()

    assert laczne_czekanie == pytest.approx(40.0, abs=0.5)


def test_jitter_doliczamy_tylko_gdy_i_tak_czekamy() -> None:
    """SPEC.md §13 — jitter rozsuwa żądania, nie spowalnia końcówki aukcji.

    poleasingowe.pl zamyka aukcje partiami o tej samej sekundzie (RECON.md
    §4.2), więc bez jittera uderzają w serwis idealnie równo. Ale doklejenie
    go do żądania, które mogło pójść od razu, byłoby czystą stratą.
    """
    zegar = ZegarSterowany()
    k = kubelek(60, zegar, jitter=1.0)
    assert k.ile_czekac() == 0.0, "wolny token — bez opóźnienia"

    for _ in range(60):
        k.zuzyj()
    assert k.ile_czekac() == pytest.approx(1.0 + MAKS_JITTER_S, abs=0.01)


def test_zerowy_limit_jest_bledem_konfiguracji() -> None:
    with pytest.raises(ValueError, match="dodatni"):
        KubelekTokenow(0)


# --------------------------------------------------------------------------
# Bezpiecznik
# --------------------------------------------------------------------------


def test_pojedynczy_blad_nie_odstawia_zrodla() -> None:
    """Jeden timeout to nie awaria serwisu — to zwykły dzień w internecie."""
    b = Bezpiecznik()
    b.zglos_blad(1000.0)
    b.zglos_blad(1000.0)
    assert not b.otwarty(1000.0)


def test_seria_bledow_otwiera_bezpiecznik() -> None:
    b = Bezpiecznik(prog_bledow=3, backoff_start_s=30.0)
    for _ in range(3):
        b.zglos_blad(1000.0)

    assert b.otwarty(1000.0)
    assert b.ile_pauzy(1000.0) == pytest.approx(30.0)
    assert not b.otwarty(1031.0), "po przerwie źródło wraca do pracy"


def test_backoff_rosnie_wykladniczo_i_ma_sufit() -> None:
    """SPEC.md §13 — backoff wykładniczy. Sufit, żeby źródło kiedyś wróciło."""
    b = Bezpiecznik(prog_bledow=3, backoff_start_s=30.0, backoff_maks_s=300.0)
    pauzy = []
    for _ in range(8):
        b.zglos_blad(1000.0)
        if b.otwarty(1000.0):
            pauzy.append(b.ile_pauzy(1000.0))

    assert pauzy[:4] == [30.0, 60.0, 120.0, 240.0]
    assert all(p == 300.0 for p in pauzy[4:]), "sufit trzyma"


def test_sukces_zamyka_bezpiecznik_i_zeruje_licznik() -> None:
    b = Bezpiecznik(prog_bledow=3)
    for _ in range(3):
        b.zglos_blad(1000.0)
    assert b.otwarty(1000.0)

    b.zglos_sukces()
    assert not b.otwarty(1000.0)
    assert b.kolejne_bledy == 0


def test_bezpiecznik_jest_per_zrodlo() -> None:
    """SPEC.md §13 — awaria jednego źródła nie przerywa przebiegu.

    Dwa niezależne obiekty to nie jest wielkie odkrycie, ale to właśnie ta
    niezależność jest wymaganiem: pozostałe źródła mają pracować dalej.
    """
    padnięte = Bezpiecznik(prog_bledow=1)
    zdrowe = Bezpiecznik(prog_bledow=1)
    padnięte.zglos_blad(1000.0)

    assert padnięte.otwarty(1000.0)
    assert not zdrowe.otwarty(1000.0)
