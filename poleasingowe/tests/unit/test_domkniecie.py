"""Faza domknięcia — łapanie ceny końcowej (SPEC.md §11.5).

Czysta logika, więc testy są tabelą przypadków. Liczby przesunięć pochodzą
z rekonesansu: okno autoprzetargu to 15-17 s, EFL oznacza koniec dopiero po
5-7 minutach (RECON.md §3.4, §4.1).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.domain import domkniecie
from app.domain.entities import Auction, Source
from app.domain.enums import AuctionStatus, AuthState, Currency, FinalPriceState
from app.domain.value_objects import Money

KONIEC = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.UTC)


def zrodlo(**nadpisz: object) -> Source:
    dane: dict[str, object] = {
        "key": "autoprzetarg",
        "name": "autoprzetarg",
        "enabled": True,
        "sweep_interval_seconds": 21600,
        "rate_limit_per_minute": 30,
        "floor_seconds": 60,
        "auth_state": AuthState.ANONYMOUS,
        "consecutive_auth_failures": 0,
        "overtime_window_seconds": 120,
        "overtime_extension_seconds": 120,
        "overtime_cap_seconds": None,
        "closing_ladder_seconds": (2, 5, 8, 11, 14),
        "id": 1,
    }
    dane.update(nadpisz)
    return Source(**dane)  # type: ignore[arg-type]


def aukcja(**nadpisz: object) -> Auction:
    dane: dict[str, object] = {
        "source_id": 1,
        "external_id": "abc",
        "url": "https://przyklad.test/abc",
        "status": AuctionStatus.ACTIVE,
        "first_seen_at": KONIEC - dt.timedelta(days=2),
        "last_seen_at": KONIEC - dt.timedelta(seconds=30),
        "ends_at": KONIEC,
        "price_current": Money(Decimal("35000"), Currency.PLN),
        "id": 7,
    }
    dane.update(nadpisz)
    return Auction(**dane)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "sekund_po_koncu,oczekiwane_przesuniecie",
    [
        # Przed terminem drabinka jeszcze nie działa — pierwszy szczebel.
        (-5, 2),
        (0, 2),
        (1, 2),
        # Szczebel minięty: bierzemy PIERWSZY PRZYSZŁY, nie następny po
        # ostatnim. Spóźniony obrót nie odtwarza przeszłych prób.
        (3, 5),
        (6, 8),
        (13, 14),
    ],
)
def test_drabinka_idzie_po_pierwszym_przyszlym_szczeblu(
    sekund_po_koncu: int, oczekiwane_przesuniecie: int
) -> None:
    teraz = KONIEC + dt.timedelta(seconds=sekund_po_koncu)
    nastepny = domkniecie.nastepny_krok_drabinki(aukcja(), zrodlo(), teraz)
    assert nastepny == KONIEC + dt.timedelta(seconds=oczekiwane_przesuniecie)


def test_wyczerpana_drabinka_nie_planuje_nic() -> None:
    """Po ostatnim szczeblu nie ma czego czekać — serwis już nie pokaże."""
    teraz = KONIEC + dt.timedelta(seconds=20)
    assert domkniecie.nastepny_krok_drabinki(aukcja(), zrodlo(), teraz) is None


def test_drabinka_nie_przekracza_sufitu_prob() -> None:
    """SPEC.md §11.5 — najwyżej sześć prób, nawet gdy siatka jest dłuższa."""
    dluga = zrodlo(closing_ladder_seconds=(1, 2, 3, 4, 5, 6, 7, 8, 9))
    teraz = KONIEC + dt.timedelta(seconds=6)
    assert domkniecie.nastepny_krok_drabinki(aukcja(), dluga, teraz) is None


def test_aukcja_bez_terminu_nie_ma_czego_domykac() -> None:
    """poleasingowe.pl nie podaje godziny końca na liście (RECON.md §4.2)."""
    bez_terminu = aukcja(ends_at=None)
    assert domkniecie.nastepny_krok_drabinki(bez_terminu, zrodlo(), KONIEC) is None
    assert domkniecie.w_domykaniu(bez_terminu, KONIEC) is False


def test_w_domykaniu_tylko_po_terminie_i_tylko_aktywne() -> None:
    assert domkniecie.w_domykaniu(aukcja(), KONIEC + dt.timedelta(seconds=1)) is True
    assert domkniecie.w_domykaniu(aukcja(), KONIEC - dt.timedelta(seconds=1)) is False
    zamknieta = aukcja(status=AuctionStatus.ENDED)
    assert domkniecie.w_domykaniu(zamknieta, KONIEC + dt.timedelta(days=1)) is False


def test_potwierdzenie_przez_serwis_daje_cene_koncowa() -> None:
    """`CONFIRMED` znaczy: odczytaliśmy stronę, która SAMA mówi „zakończona".

    To jedyna droga do tego stanu. §9 liczy z niego osobną medianę rynku,
    więc pomyłka tutaj fałszuje cały obraz cen.
    """
    teraz = KONIEC + dt.timedelta(seconds=2)
    wynik = domkniecie.po_odczycie_po_terminie(
        aukcja(), teraz, serwis_potwierdza_koniec=True
    )
    assert wynik.status is AuctionStatus.ENDED
    assert wynik.final_price_state is FinalPriceState.CONFIRMED
    # SPEC.md §8.2 — dla CONFIRMED wyprzedzenie jest z definicji zerowe,
    # więc kolumna zostaje pusta (pilnuje tego też CHECK w bazie).
    assert wynik.last_price_lead_seconds is None
    assert wynik.next_poll_at is None


def test_brak_potwierdzenia_nie_zamyka_aukcji() -> None:
    """Cisza serwisu to nie jest dowód zakończenia — drabinka kręci się dalej."""
    teraz = KONIEC + dt.timedelta(seconds=2)
    wynik = domkniecie.po_odczycie_po_terminie(
        aukcja(), teraz, serwis_potwierdza_koniec=False
    )
    assert wynik.status is AuctionStatus.ACTIVE
    assert wynik.final_price_state is FinalPriceState.UNKNOWN


def test_wyczerpana_drabinka_zapisuje_dolne_oszacowanie() -> None:
    """`LAST_SEEN` z miarą jakości: ile sekund przed końcem był ten odczyt.

    Bez `last_price_lead_seconds` nie da się odróżnić ceny sprzed dwóch
    sekund od ceny sprzed doby, a obie wyglądają jak „cena końcowa".
    """
    teraz = KONIEC + dt.timedelta(seconds=20)
    wynik = domkniecie.po_wyczerpaniu_drabinki(aukcja(), teraz)
    assert wynik.status is AuctionStatus.ENDED
    assert wynik.final_price_state is FinalPriceState.LAST_SEEN
    assert wynik.last_price_lead_seconds == 30
    assert wynik.next_poll_at is None


def test_wyprzedzenie_nie_bywa_ujemne() -> None:
    """Odczyt PO terminie ma wyprzedzenie zerowe, nie ujemne."""
    po_terminie = aukcja(last_seen_at=KONIEC + dt.timedelta(seconds=5))
    assert domkniecie.wyprzedzenie_sekundy(po_terminie) == 0
