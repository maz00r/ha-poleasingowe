"""Formatowanie w warstwie widoku (SPEC.md §8.2, §12)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.application.read_models import PozycjaListy
from app.domain.enums import AuctionStatus, Currency
from app.domain.value_objects import Money
from app.interfaces.web.filtry_szablonu import (
    NBSP,
    NIEZNANE,
    bajty,
    czas_lokalny,
    do_konca,
    kwota,
    kwota_w_polu,
    liczba,
)

TERAZ = dt.datetime(2026, 9, 7, 10, 0, tzinfo=dt.UTC)


def test_czas_pokazuje_sie_w_strefie_lokalnej() -> None:
    """SPEC.md §8.2 — konwersja do strefy lokalnej należy tylko do widoku.

    7 września to czas letni, więc CEST = UTC+2.
    """
    assert czas_lokalny(TERAZ) == "2026-09-07 12:00"
    assert czas_lokalny(TERAZ, z_sekundami=True) == "2026-09-07 12:00:00"


@pytest.mark.parametrize(
    "formater,pusta",
    [(czas_lokalny, None), (kwota, None), (liczba, None), (bajty, None)],
)
def test_brak_danych_pokazuje_sie_jako_brak(formater: object, pusta: None) -> None:
    """Nie „0" i nie pusta komórka — te dwie rzeczy kłamią inaczej."""
    assert formater(pusta) == NIEZNANE  # type: ignore[operator]


def test_kwota_ma_polski_zapis_i_nielamiace_spacje() -> None:
    sformatowana = kwota(Money(Decimal("48600"), Currency.PLN))
    assert sformatowana == f"48{NBSP}600,00{NBSP}PLN"
    assert " " not in sformatowana, "kwota nie ma prawa łamać się w pół wiersza"


def test_bajty_skaluja_sie_do_czytelnej_jednostki() -> None:
    assert bajty(142_000_000).endswith("MB")
    assert bajty(512) == f"512,0{NBSP}B"


def test_do_konca_liczy_od_podanej_chwili_nie_od_zegara_systemu() -> None:
    """Zegar jako argument, nie `now()` w środku — inaczej test byłby losowy."""
    assert do_konca(TERAZ + dt.timedelta(minutes=45), TERAZ) == f"45{NBSP}min"
    assert do_konca(TERAZ + dt.timedelta(hours=3, minutes=5), TERAZ) == (
        f"3{NBSP}h 5{NBSP}min"
    )
    assert (
        do_konca(TERAZ + dt.timedelta(days=2, hours=4), TERAZ) == f"2{NBSP}d 4{NBSP}h"
    )


def test_po_terminie_nie_pokazuje_ujemnych_liczb() -> None:
    assert do_konca(TERAZ - dt.timedelta(minutes=1), TERAZ) == "po terminie"


def pozycja(**nadpisz: object) -> PozycjaListy:
    dane: dict[str, object] = {
        "id": 1,
        "source_key": "efl",
        "external_id": "435508",
        "url": "https://przyklad.test/435508",
        "status": AuctionStatus.ACTIVE,
    }
    dane.update(nadpisz)
    return PozycjaListy(**dane)  # type: ignore[arg-type]


def test_nazwa_sklada_sie_z_marki_modelu_i_wersji() -> None:
    assert pozycja(make="Audi", model="A6", variant="Avant").nazwa == "Audi A6 Avant"


def test_bez_marki_i_modelu_zostaje_identyfikator() -> None:
    """Wiersz bez nazwy musi dać się kliknąć — pusty link to ślepy wiersz."""
    assert pozycja().nazwa == "435508"


def test_wyroznienie_progu_wymaga_i_ceny_i_progu() -> None:
    """SPEC.md §12 — brak którejkolwiek liczby znaczy „nie wiadomo", nie „tak"."""
    cena = Money(Decimal("40000"), Currency.PLN)
    prog = Money(Decimal("45000"), Currency.PLN)
    assert pozycja(price_current=cena, cena_docelowa=prog).ponizej_progu
    assert not pozycja(price_current=cena).ponizej_progu
    assert not pozycja(cena_docelowa=prog).ponizej_progu


def test_prog_zadziala_takze_przy_rownosci() -> None:
    cena = Money(Decimal("45000"), Currency.PLN)
    assert pozycja(price_current=cena, cena_docelowa=cena).ponizej_progu


def test_progu_nie_porownujemy_miedzy_walutami() -> None:
    """45 000 EUR to nie 45 000 PLN — porównanie byłoby po prostu błędem."""
    assert not pozycja(
        price_current=Money(Decimal("40000"), Currency.PLN),
        cena_docelowa=Money(Decimal("45000"), Currency.EUR),
    ).ponizej_progu


def test_kwota_w_polu_nie_dokleja_zer_ani_waluty() -> None:
    """Pole formularza to nie to samo co kwota do czytania.

    `60000.00` w polu edycji wygląda jak literówka, a `Decimal.normalize()`
    samo z siebie daje `6E+4`. Grosze, jeśli są, muszą przeżyć.
    """
    assert kwota_w_polu(Money(Decimal("60000.00"), Currency.PLN)) == ("60000")
    assert kwota_w_polu(Money(Decimal("60000.50"), Currency.PLN)) == ("60000.5")
    assert kwota_w_polu(None) == ""
