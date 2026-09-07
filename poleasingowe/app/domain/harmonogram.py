"""Kiedy odpytać aukcję (SPEC.md §11.2).

**Czysta funkcja**: bez sieci, bez bazy, bez `datetime.now()` w środku.
Zegar wchodzi argumentem, więc cała logika progów daje się sprawdzić tabelą
przypadków — i to jedyne miejsce, w którym wolno ją zmieniać.

Floor endgame'u **nie jest stałą liczbą, tylko regułą** wyprowadzoną
z dogrywki serwisu:

> W endgame odpytuj tak, żeby w okno dogrywki serwisu mieściły się dwie
> próbki. Floor = połowa okna dogrywki, nie mniej niż 10 s.

Jedna próbka na okno nie gwarantuje wykrycia przedłużenia, zanim aukcja się
domknie; dwie gwarantują. Stała liczba tego nie zapewnia, bo okna dogrywki
różnią się między serwisami o rząd wielkości — 30 s w poleasingowe.pl wobec
2 min w autoprzetarg.pl (RECON.md §3.2).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.domain.entities import Auction, Source
from app.domain.enums import AuctionStatus, PollTier

MINIMALNY_FLOOR_S = 10
"""SPEC.md §11.2 — poniżej tego nie schodzimy nigdy, niezależnie od reguły."""


@dataclass(slots=True, frozen=True)
class Prog:
    """Jeden wiersz tabeli progów: „do tylu czasu do końca — taki interwał"."""

    do_konca_s: int
    interwal_s: int


# SPEC.md §11.2. Kolejność od najbliższego końca — pierwszy pasujący wygrywa.
# `do_konca_s` czytamy jako „gdy zostało nie więcej niż tyle".
PROGI: tuple[Prog, ...] = (
    Prog(do_konca_s=15 * 60, interwal_s=0),  # 0 = floor źródła
    Prog(do_konca_s=60 * 60, interwal_s=3 * 60),
    Prog(do_konca_s=6 * 3600, interwal_s=15 * 60),
    Prog(do_konca_s=24 * 3600, interwal_s=3600),
    Prog(do_konca_s=7 * 86400, interwal_s=6 * 3600),
)
INTERWAL_DALEKI_S = 24 * 3600
"""Powyżej siedmiu dni. Osobno, bo to nie jest próg, tylko domyślna reszta."""


def floor_zrodla(source: Source) -> int:
    """Dolna granica interwału w endgame, wyliczona z reguły (SPEC.md §11.2).

    `source.floor_seconds` wolno **podnieść** ponad wartość z reguły — tak
    robimy dla serwisu za zaporą aplikacyjną (RECON.md §4.3). Zejście poniżej
    reguły wymaga dowodu z nagłówków limitu tempa, więc reguła jest tu
    podłogą, a konfiguracja może być tylko ostrożniejsza.
    """
    if not source.ma_dogrywke:
        # Serwis bez dogrywki: `ends_at` jest twardy, nie ma czego łapać
        # gęstszym odpytem niż to, co ustawiono w konfiguracji.
        return max(source.floor_seconds, MINIMALNY_FLOOR_S)
    z_reguly = max(source.overtime_window_seconds // 2, MINIMALNY_FLOOR_S)
    return max(source.floor_seconds, z_reguly)


def tier(auction: Auction, teraz: dt.datetime) -> PollTier:
    """Kubełek harmonogramu dla aukcji (SPEC.md §11.2).

    `IDLE` znaczy „nie odpytujemy pojedynczo w ogóle" — aukcjom
    nieobserwowanym wystarcza zbiorczy przemiat listy. To główna oszczędność
    całego systemu: koszt rośnie z liczbą obserwowanych, nie z liczbą ofert
    w serwisie.
    """
    if auction.status is not AuctionStatus.ACTIVE:
        return PollTier.IDLE
    if auction.ends_at is None:
        # Bez terminu nie ma czego liczyć. Taka aukcja żyje z przemiatu
        # listy — poleasingowe.pl nie podaje daty końca na liście w żadnej
        # postaci (RECON.md §4.2), więc to nie jest przypadek teoretyczny.
        return PollTier.FAR

    zostalo = (auction.ends_at - teraz).total_seconds()
    if zostalo <= 0:
        return PollTier.CLOSING
    if zostalo <= 15 * 60:
        return PollTier.ENDGAME
    if zostalo <= 6 * 3600:
        return PollTier.NEAR
    return PollTier.FAR


def interwal(auction: Auction, source: Source, teraz: dt.datetime) -> int:
    """Odstęp do następnego odpytu w sekundach."""
    if auction.ends_at is None:
        return INTERWAL_DALEKI_S

    zostalo = (auction.ends_at - teraz).total_seconds()
    if zostalo <= 0:
        # Po terminie decyduje domknięcie z §11.5, nie tabela progów.
        # Zwracamy floor, żeby czujka dogrywki miała czym się kręcić.
        return floor_zrodla(source)

    for prog in PROGI:
        if zostalo <= prog.do_konca_s:
            return prog.interwal_s or floor_zrodla(source)
    return INTERWAL_DALEKI_S


def nastepny_odpyt(auction: Auction, source: Source, teraz: dt.datetime) -> dt.datetime:
    """Kiedy odpytać tę aukcję następnym razem.

    Sygnatura z §11.2 — czysta funkcja `(aukcja, teraz) -> czas`, rozszerzona
    o źródło, bo floor jest per źródło i nie ma prawa być stałą w kodzie.
    """
    return teraz + dt.timedelta(seconds=interwal(auction, source, teraz))
