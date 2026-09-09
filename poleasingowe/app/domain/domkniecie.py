"""Domknięcie aukcji — łapanie ceny końcowej (SPEC.md §11.5).

**Czysta funkcja**, jak `harmonogram.py`: bez sieci, bez bazy, bez
`datetime.now()` w środku. Zegar wchodzi argumentem.

DLACZEGO TO JEST TRUDNE. Cena końcowa nie jest widoczna „po zakończeniu"
przez dowolnie długi czas — liczy się **trafienie w okno**, nie długość
czujki. Zmierzone w rekonesansie:

| Serwis | Co się dzieje po `ends_at` |
|---|---|
| autoprzetarg.pl | strona **znika** 15-17 s po terminie (RECON.md §3.4) |
| poleasingowe.pl | `auction_pending:false` natychmiast, cena zostaje |
| aukcje.efl.com.pl | `Zakończona` dopiero **5-7 minut** po terminie |

Stąd dwie fazy:

1. **Czujka dogrywki.** Serwis z dogrywką przesuwa `ends_at`, gdy oferta
   padnie tuż przed końcem. Pierwsza próba tuż po terminie rozstrzyga, czy
   aukcja faktycznie się skończyła, czy dostała dodatkowe minuty.
2. **Drabinka.** Kolejne próby w rosnących odstępach OD TERMINU, nie od
   siebie nawzajem: `ends_at + 2 s`, `+5 s`, `+10 s`… Siatka jest **per
   źródło** (`closing_ladder_seconds`), bo 15-sekundowe okno autoprzetargu
   i siedmiominutowe opóźnienie EFL to dwa różne problemy.

KROK DRABINKI LICZYMY Z ZEGARA, nie z licznika prób w bazie. Licznik
rozjeżdżałby się przy restarcie add-onu i przy każdym obrocie, który
spóźnił się o sekundę; czas do terminu jest zawsze prawdziwy.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from app.domain.entities import Auction, Source
from app.domain.enums import AuctionStatus, FinalPriceState, PollTier

MAKS_PROB_DOMKNIECIA = 6
"""SPEC.md §11.5 — sufit prób fazy 2. Dłuższa czujka nie pomaga: jeśli po
sześciu podejściach serwis nie pokazał ceny końcowej, to jej tam nie ma."""


def punkt_zerowy(auction: Auction) -> dt.datetime | None:
    """Od czego odmierzamy drabinkę. `None`, gdy aukcja nie ma terminu."""
    return auction.ends_at


def nastepny_krok_drabinki(
    auction: Auction, source: Source, teraz: dt.datetime
) -> dt.datetime | None:
    """Kiedy następna próba domknięcia. `None`, gdy drabinka się skończyła.

    Bierzemy **pierwszy przyszły** szczebel, a nie „kolejny po ostatnim":
    dzięki temu spóźniony obrót dispatchera nie odtwarza przeszłych prób
    ani nie zapętla się na szczeblu, który już minął.
    """
    koniec = punkt_zerowy(auction)
    if koniec is None:
        return None
    for przesuniecie in source.closing_ladder_seconds[:MAKS_PROB_DOMKNIECIA]:
        proba = koniec + dt.timedelta(seconds=przesuniecie)
        if proba > teraz:
            return proba
    return None


def w_domykaniu(auction: Auction, teraz: dt.datetime) -> bool:
    """Czy aukcja jest po terminie i wciąż nierozstrzygnięta."""
    return (
        auction.status is AuctionStatus.ACTIVE
        and auction.ends_at is not None
        and auction.ends_at <= teraz
    )


def wyprzedzenie_sekundy(auction: Auction) -> int | None:
    """O ile sekund ostatni odczyt ceny wyprzedził koniec aukcji (§8.2).

    Miara jakości pomiaru: im większa, tym mniej warta jest „cena końcowa".
    `None`, gdy nie ma czego mierzyć.
    """
    if auction.ends_at is None:
        return None
    return max(0, int((auction.ends_at - auction.last_seen_at).total_seconds()))


def po_odczycie_po_terminie(
    auction: Auction, teraz: dt.datetime, *, serwis_potwierdza_koniec: bool
) -> Auction:
    """Stan aukcji po próbie domknięcia zakończonej odczytem strony.

    `serwis_potwierdza_koniec` znaczy: strona **sama** mówi, że aukcja się
    skończyła (`auction_pending:false`, nagłówek `Zakończona`). Tylko wtedy
    cena zasługuje na `CONFIRMED` — to jest cała różnica między pomiarem
    a domysłem, i §9 liczy z niej osobne mediany.
    """
    if not serwis_potwierdza_koniec:
        return auction
    return _z_zamknieta_cena(
        auction, teraz, stan=FinalPriceState.CONFIRMED, wyprzedzenie=None
    )


def po_wyczerpaniu_drabinki(auction: Auction, teraz: dt.datetime) -> Auction:
    """Drabinka się skończyła, a serwis nigdy nie potwierdził zakończenia.

    Zostaje ostatnia widziana cena — **dolne oszacowanie**, nie cena
    końcowa. Razem z nią zapisujemy, jak bardzo odczyt wyprzedził termin;
    bez tej liczby `LAST_SEEN` nie da się odróżnić od pomiaru sprzed doby.
    """
    return _z_zamknieta_cena(
        auction,
        teraz,
        stan=FinalPriceState.LAST_SEEN,
        wyprzedzenie=wyprzedzenie_sekundy(auction),
    )


def _z_zamknieta_cena(
    auction: Auction,
    teraz: dt.datetime,
    *,
    stan: FinalPriceState,
    wyprzedzenie: int | None,
) -> Auction:
    return replace(
        auction,
        status=AuctionStatus.ENDED,
        final_price_state=stan,
        last_price_lead_seconds=wyprzedzenie,
        last_seen_at=teraz,
        next_poll_at=None,
        poll_tier=PollTier.IDLE,
    )
