"""Reguły uwierzytelniania w serwisach (SPEC.md §10.2).

Czysta logika przejść między stanami — bez sieci, bez bazy, bez zegara.
Dzięki temu najostrzejsza reguła całego projektu, **twardy limit trzech
nieudanych logowań**, da się sprawdzić tabelarycznie, a nie przez próbę
zalogowania się trzy razy złym hasłem na prawdziwym koncie.

Dlaczego ten limit jest twardy: ruch jest imienny, powiązany z moim kontem.
Pętla ponowień, która w anonimowym scrapingu kończy się odrzuconym żądaniem,
tutaj kończy się **zablokowanym kontem w serwisie**.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import AuthState

MAKS_NIEUDANYCH_LOGOWAN = 3
"""SPEC.md §10.2. Po przekroczeniu źródło wymaga ręcznego resetu z UI."""


class ZrodloZablokowane(Exception):
    """Próba żądania do źródła w stanie `LOCKED`.

    Nie `DomainError`: to nie jest błąd wykonania, tylko odmowa wykonania.
    Łapiący `DomainError` dispatcher ma to zobaczyć osobno i **nie** liczyć
    jako awarii źródła.
    """


@dataclass(slots=True, frozen=True)
class StanLogowania:
    """Trwały stan uwierzytelnienia źródła — odzwierciedla kolumny `source`.

    Licznik jest w bazie, nie w pamięci procesu: inaczej pętla restartów
    kontenera zerowałaby go co restart i obeszła limit z §10.2.
    """

    stan: AuthState
    nieudane_proby: int = 0

    def __post_init__(self) -> None:
        if self.nieudane_proby < 0:
            raise ValueError(
                f"licznik nieudanych logowań ujemny: {self.nieudane_proby}"
            )

    @property
    def zablokowane(self) -> bool:
        return self.stan is AuthState.LOCKED

    @property
    def wymaga_logowania(self) -> bool:
        """Czy przed żądaniem trzeba się zalogować.

        `ANONYMOUS` znaczy „to źródło nie wymaga logowania do odczytu"
        (RECON.md §4.1, §4.2) — nie „jeszcze się nie zalogowaliśmy".
        """
        return self.stan is AuthState.EXPIRED


def wolno_probowac(stan: StanLogowania) -> bool:
    """SPEC.md §10.2 — `LOCKED` wstrzymuje **jakiekolwiek** żądania."""
    return not stan.zablokowane


def po_udanym_logowaniu(stan: StanLogowania) -> StanLogowania:
    """Sukces zeruje licznik. Dwie porażki i sukces to nie „dwie porażki"."""
    if stan.zablokowane:
        raise ZrodloZablokowane(
            "źródło jest zablokowane — logowanie wymaga wcześniejszego resetu"
        )
    return StanLogowania(stan=AuthState.OK, nieudane_proby=0)


def po_nieudanym_logowaniu(stan: StanLogowania) -> StanLogowania:
    """Odrzucone poświadczenia. Trzecia z rzędu porażka blokuje źródło.

    Blokada zapada **przy trzeciej próbie**, nie po czwartej: limit z §10.2
    mówi „3 nieudane logowania", a nie „3 dozwolone i dopiero czwarta boli".
    """
    if stan.zablokowane:
        raise ZrodloZablokowane("źródło jest już zablokowane")
    licznik = stan.nieudane_proby + 1
    nowy = AuthState.LOCKED if licznik >= MAKS_NIEUDANYCH_LOGOWAN else AuthState.EXPIRED
    return StanLogowania(stan=nowy, nieudane_proby=licznik)


def po_wygasnieciu_sesji(stan: StanLogowania) -> StanLogowania:
    """Sesja padła, ale poświadczenia nie zostały odrzucone.

    **Licznika nie ruszamy.** Wygaśnięcie to normalny bieg rzeczy — serwis
    ma prawo zamknąć sesję po swoim czasie. Doliczanie tego do limitu
    z §10.2 blokowałoby źródło po trzech spokojnych dniach pracy.
    """
    if stan.zablokowane:
        return stan
    return StanLogowania(stan=AuthState.EXPIRED, nieudane_proby=stan.nieudane_proby)


def po_recznym_odblokowaniu(stan: StanLogowania) -> StanLogowania:
    """Reset z panelu diagnostycznego (SPEC.md §12).

    Wynikiem jest `EXPIRED`, nie `OK`: reset kasuje blokadę, ale sesji nadal
    nie ma. `OK` znaczyłoby „jesteśmy zalogowani", a to byłaby nieprawda.
    """
    return StanLogowania(stan=AuthState.EXPIRED, nieudane_proby=0)
