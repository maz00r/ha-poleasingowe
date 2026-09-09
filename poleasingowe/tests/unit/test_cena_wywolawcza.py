"""Cena wywoławcza wnioskowana z braku ofert (SPEC.md §8.2).

Żaden z czterech serwisów nie podaje ceny wywoławczej wprost. Da się ją
jednak wywnioskować **pewnie**: aukcja bez ani jednej oferty stoi na cenie
wywoławczej, bo licytować można wyłącznie w górę.

Te testy pilnują przede wszystkim granic tego wnioskowania — bo to one
decydują, czy w bazie stoi fakt, czy zmyślona liczba.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.domain.entities import Auction, z_cena_wywolawcza
from app.domain.enums import AuctionStatus, Currency
from app.domain.value_objects import Money

TERAZ = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.UTC)


def aukcja(**pola: object) -> Auction:
    dane: dict[str, object] = {
        "source_id": 1,
        "external_id": "x",
        "url": "https://przyklad.test/x",
        "status": AuctionStatus.ACTIVE,
        "first_seen_at": TERAZ,
        "last_seen_at": TERAZ,
    }
    dane.update(pola)
    return Auction(**dane)  # type: ignore[arg-type]


def pln(kwota: str) -> Money:
    return Money(Decimal(kwota), Currency.PLN)


def test_zero_ofert_znaczy_ze_cena_biezaca_jest_wywolawcza() -> None:
    wynik = z_cena_wywolawcza(aukcja(price_current=pln("94950"), bid_count=0))
    assert wynik.price_start == pln("94950")


def test_jedna_oferta_juz_wystarczy_zeby_nie_dalo_sie_wywnioskowac() -> None:
    """Po pierwszym postąpieniu cena bieżąca jest wyższa o nieznaną wartość."""
    wynik = z_cena_wywolawcza(aukcja(price_current=pln("94950"), bid_count=1))
    assert wynik.price_start is None


def test_brak_licznika_ofert_to_nie_zero() -> None:
    """autoprzetarg bez logowania nie podaje `bid_count` (RECON.md §4.4).

    „Nie wiem, ile było ofert" nie znaczy „nie było żadnej" — a różnica
    decyduje o tym, czy wpiszemy do bazy fakt, czy zgadywankę.
    """
    wynik = z_cena_wywolawcza(aukcja(price_current=pln("94950"), bid_count=None))
    assert wynik.price_start is None


def test_znanej_ceny_wywolawczej_nie_nadpisujemy() -> None:
    """Pierwsza obserwacja bywa jedyną, w której aukcja nie miała ofert."""
    wynik = z_cena_wywolawcza(
        aukcja(price_start=pln("80000"), price_current=pln("94950"), bid_count=0)
    )
    assert wynik.price_start == pln("80000")


def test_bez_ceny_biezacej_nie_ma_z_czego_wnioskowac() -> None:
    assert z_cena_wywolawcza(aukcja(bid_count=0)).price_start is None
