"""Reguły uwierzytelniania (SPEC.md §10.2).

Tabelarycznie, bo to najostrzejsza reguła w projekcie: ruch jest imienny,
a pomyłka w liczeniu prób kończy się **zablokowanym kontem w serwisie**,
nie odrzuconym żądaniem. Tego nie da się sprawdzić inaczej niż na czystej
funkcji — trzykrotne wpisanie złego hasła na prawdziwym koncie jest właśnie
tym, czego ta reguła zabrania.
"""

from __future__ import annotations

import pytest

from app.domain.enums import AuthState
from app.domain.logowanie import (
    MAKS_NIEUDANYCH_LOGOWAN,
    StanLogowania,
    ZrodloZablokowane,
    po_nieudanym_logowaniu,
    po_recznym_odblokowaniu,
    po_udanym_logowaniu,
    po_wygasnieciu_sesji,
    wolno_probowac,
)

SWIEZE = StanLogowania(stan=AuthState.EXPIRED, nieudane_proby=0)


def test_limit_z_spec_to_trzy() -> None:
    """SPEC.md §10.2 mówi wprost: trzy. Stała nie ma prawa cicho urosnąć."""
    assert MAKS_NIEUDANYCH_LOGOWAN == 3


def test_trzecia_nieudana_proba_blokuje_zrodlo() -> None:
    """Blokada zapada PRZY trzeciej próbie, nie po czwartej."""
    stan = SWIEZE
    stany = []
    for _ in range(MAKS_NIEUDANYCH_LOGOWAN):
        stan = po_nieudanym_logowaniu(stan)
        stany.append((stan.stan, stan.nieudane_proby))

    assert stany == [
        (AuthState.EXPIRED, 1),
        (AuthState.EXPIRED, 2),
        (AuthState.LOCKED, 3),
    ]


def test_zablokowane_zrodlo_nie_wykonuje_zadnych_zadan() -> None:
    """SPEC.md §10.2 — `LOCKED` wstrzymuje wszystko, nie tylko logowanie."""
    zablokowane = StanLogowania(stan=AuthState.LOCKED, nieudane_proby=3)
    assert not wolno_probowac(zablokowane)
    assert wolno_probowac(SWIEZE)


def test_zablokowanego_zrodla_nie_da_sie_zalogowac_bez_resetu() -> None:
    """Bez tego pętla ponowień obeszłaby limit, bijąc w konto w serwisie."""
    zablokowane = StanLogowania(stan=AuthState.LOCKED, nieudane_proby=3)
    with pytest.raises(ZrodloZablokowane):
        po_udanym_logowaniu(zablokowane)
    with pytest.raises(ZrodloZablokowane):
        po_nieudanym_logowaniu(zablokowane)


def test_sukces_zeruje_licznik() -> None:
    """Dwie porażki i sukces to nie „dwie porażki" — inaczej licznik rósłby
    przez tygodnie i zablokował źródło, które działa poprawnie."""
    po_dwoch = StanLogowania(stan=AuthState.EXPIRED, nieudane_proby=2)
    wynik = po_udanym_logowaniu(po_dwoch)
    assert wynik == StanLogowania(stan=AuthState.OK, nieudane_proby=0)


@pytest.mark.parametrize("licznik", [0, 1, 2])
def test_wygasniecie_sesji_nie_rusza_licznika(licznik: int) -> None:
    """Kluczowe rozróżnienie: wygaśnięcie to nie odrzucone poświadczenia.

    Serwis ma prawo zamknąć sesję po swoim czasie. Doliczanie tego do limitu
    z §10.2 zablokowałoby źródło po trzech spokojnych dniach pracy.
    """
    stan = StanLogowania(stan=AuthState.OK, nieudane_proby=licznik)
    wynik = po_wygasnieciu_sesji(stan)
    assert wynik.stan is AuthState.EXPIRED
    assert wynik.nieudane_proby == licznik


def test_wygasniecie_nie_odblokowuje_zrodla() -> None:
    """Zablokowane zostaje zablokowane — tylko reset z UI to zmienia."""
    zablokowane = StanLogowania(stan=AuthState.LOCKED, nieudane_proby=3)
    assert po_wygasnieciu_sesji(zablokowane) == zablokowane


def test_reset_daje_expired_a_nie_ok() -> None:
    """SPEC.md §12 — reset kasuje blokadę, ale sesji nadal nie ma.

    `OK` znaczyłoby „jesteśmy zalogowani", a to byłaby nieprawda i pierwszy
    odpyt poszedłby bez sesji.
    """
    zablokowane = StanLogowania(stan=AuthState.LOCKED, nieudane_proby=3)
    wynik = po_recznym_odblokowaniu(zablokowane)
    assert wynik == StanLogowania(stan=AuthState.EXPIRED, nieudane_proby=0)
    assert wolno_probowac(wynik)


def test_anonimowe_zrodlo_nie_wymaga_logowania() -> None:
    """`ANONYMOUS` znaczy „ten serwis czyta się bez konta" (RECON.md §4.1,
    §4.2), a nie „jeszcze się nie zalogowaliśmy"."""
    anonim = StanLogowania(stan=AuthState.ANONYMOUS)
    assert not anonim.wymaga_logowania
    assert wolno_probowac(anonim)
    assert StanLogowania(stan=AuthState.EXPIRED).wymaga_logowania
    assert not StanLogowania(stan=AuthState.OK).wymaga_logowania


def test_ujemny_licznik_jest_bledem() -> None:
    with pytest.raises(ValueError, match="ujemny"):
        StanLogowania(stan=AuthState.OK, nieudane_proby=-1)
