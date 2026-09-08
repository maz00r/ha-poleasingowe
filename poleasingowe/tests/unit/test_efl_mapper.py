"""Warstwa antykorupcyjna EFL — tłumaczenie na model domenowy (SPEC.md §6.2)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.application.ports import SurowaOferta
from app.domain.enums import Currency
from app.domain.value_objects import Mileage, Money
from app.infrastructure.sources.efl import mapper, parser
from tests.unit.test_efl_parser import wczytaj

TERAZ = dt.datetime(2026, 9, 6, 18, 0, tzinfo=dt.UTC)


def test_szczegoly_mapuja_sie_na_komplet_pol() -> None:
    surowa = parser.sparsuj_szczegoly(
        wczytaj("szczegoly-435508.html"), "435508", "https://x"
    )
    a = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)

    assert (a.make, a.model, a.variant) == ("Škoda", "Superb", "Style DSG")
    assert a.year == 2023
    assert a.mileage == Mileage(208475)
    assert a.fuel == "Diesel"
    assert a.gearbox == "Automatyczna"
    assert a.body == "Liftback"
    assert a.color == "czarny"
    assert a.vin is not None and a.vin.value == "TMBC0XXT1S7Y8X23F"
    assert a.price_current == Money(Decimal("48600.00"), Currency.PLN)
    assert a.bid_count == 1


def test_moc_i_pojemnosc_z_jednego_pola_w_szczegolach() -> None:
    """Szczegóły podają „Silnik: 1968ccm (Moc 150KM/110KW)" — jedno pole na dwa."""
    surowa = parser.sparsuj_szczegoly(
        wczytaj("szczegoly-435508.html"), "435508", "https://x"
    )
    a = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)
    assert (a.engine_ccm, a.engine_hp) == (1968, 150)


def test_moc_i_pojemnosc_z_dwoch_pol_na_liscie() -> None:
    """Lista rozbija to na „Pojemność silnika" i „Moc silnika" — inny kształt."""
    pozycja = parser.sparsuj_liste(wczytaj("lista-01.html"))[0]
    a = mapper.na_aukcje(pozycja, source_id=1, teraz=TERAZ)
    assert (a.engine_ccm, a.engine_hp) == (1968, 163)


def test_czas_zakonczenia_idzie_do_bazy_w_utc() -> None:
    """SPEC.md §8.2 — wszystko zapisywane w UTC.

    Serwis podaje czas bez strefy; zakładamy Europe/Warsaw, bo taki pokazuje
    użytkownikowi (RECON.md §4.1). 10:47 czasu polskiego to 08:47 UTC.
    """
    surowa = parser.sparsuj_szczegoly(
        wczytaj("szczegoly-435508.html"), "435508", "https://x"
    )
    a = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)
    assert a.ends_at == dt.datetime(2026, 9, 7, 8, 47, tzinfo=dt.UTC)


def test_niechlujny_vin_jest_odrzucany_a_nie_zapisywany() -> None:
    """SPEC.md §8.4 — VIN jest podstawą deduplikacji,
    więc śmieć jest gorszy niż jego brak.
    """
    surowa = SurowaOferta(
        external_id="1", url="u", pola={"VIN": "NIE-JEST-VIN-EM", "tytul": "Audi A4"}
    )
    a = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)
    assert a.vin is None


def test_brak_ceny_nie_wywraca_mapowania() -> None:
    """Lista bez ceny zdarza się przy pozycjach nietypowych — ma przejść."""
    surowa = SurowaOferta(external_id="1", url="u", pola={"tytul": "Audi A4"})
    a = mapper.na_aukcje(surowa, source_id=1, teraz=TERAZ)
    assert a.price_current is None
    assert a.ends_at is None


def test_cala_lista_mapuje_sie_bez_wyjatku() -> None:
    """Jedna dziwna pozycja nie ma prawa przerwać przemiatu (SPEC.md §13)."""
    pozycje = parser.sparsuj_liste(wczytaj("lista-01.html"))
    aukcje = [mapper.na_aukcje(p, source_id=1, teraz=TERAZ) for p in pozycje]
    assert len(aukcje) == len(pozycje)
    assert all(a.make for a in aukcje)
    assert all(a.price_current is not None for a in aukcje)
