"""Parametry adresu → kryteria listy (SPEC.md §12).

Adres bywa sklejony ręcznie albo zapamiętany sprzed zmiany, więc te testy
pilnują jednej rzeczy: **żadne dane z adresu nie mają prawa wywalić strony**.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.application.read_models import Kursor, Sortowanie
from app.domain.enums import AuctionStatus
from app.interfaces.web.formularze import (
    kursor_z_parametrow,
    na_parametry,
    zbuduj_kryteria,
)

WIZYTA = dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.UTC)


def test_pusty_adres_daje_widok_domyslny() -> None:
    """Domyślnie: aktywne, najbliżej końca u góry — tak się tego używa."""
    kryteria = zbuduj_kryteria({})
    assert kryteria.status is AuctionStatus.ACTIVE
    assert kryteria.sortowanie is Sortowanie.KONIEC_ROSNACO
    assert kryteria.szukaj is None
    assert kryteria.tylko_obserwowane is False


def test_filtry_tekstowe_i_liczbowe() -> None:
    kryteria = zbuduj_kryteria(
        {
            "szukaj": " passat ",
            "marka": "Volkswagen",
            "cena_od": "20000",
            "rocznik_od": "2018",
            "przebieg_do": "150000",
            "do_konca_h": "24",
        }
    )
    assert kryteria.szukaj == "passat", "spacje z pola tekstowego mają odpaść"
    assert kryteria.marka == "Volkswagen"
    assert (kryteria.cena_od, kryteria.rocznik_od) == (20_000, 2018)
    assert (kryteria.przebieg_do, kryteria.konczy_sie_w_h) == (150_000, 24)


@pytest.mark.parametrize(
    "parametr,wartosc,pole",
    [
        ("cena_od", "dużo", "cena_od"),
        ("cena_od", "-5", "cena_od"),
        ("rocznik_od", "1200", "rocznik_od"),
        ("rocznik_do", "3000", "rocznik_do"),
        ("do_konca_h", "0", "konczy_sie_w_h"),
        ("do_konca_h", "999999", "konczy_sie_w_h"),
        ("przebieg_do", "1e9", "przebieg_do"),
    ],
)
def test_bezsensowna_liczba_jest_ignorowana_a_nie_wysadza_strony(
    parametr: str, wartosc: str, pole: str
) -> None:
    """Zły parametr ma zniknąć, nie zwrócić 500."""
    kryteria = zbuduj_kryteria({parametr: wartosc})
    assert getattr(kryteria, pole) is None


def test_nieznane_sortowanie_wraca_do_domyslnego() -> None:
    assert (
        zbuduj_kryteria({"sort": "po-kolorze"}).sortowanie is Sortowanie.KONIEC_ROSNACO
    )


def test_nieznany_status_traktujemy_jak_aktywne() -> None:
    """Cichy powrót do domyślnego, bo lista musi coś pokazać."""
    assert zbuduj_kryteria({"status": "cokolwiek"}).status is AuctionStatus.ACTIVE


def test_wszystkie_znaczy_brak_filtra_statusu() -> None:
    assert zbuduj_kryteria({"status": "wszystkie"}).status is None


def test_archiwum_to_status_zakonczone() -> None:
    """SPEC.md §12 — archiwum zakończonych z ceną końcową."""
    assert zbuduj_kryteria({"status": "zakonczone"}).status is AuctionStatus.ENDED


@pytest.mark.parametrize("wartosc", ["1", "true", "tak", "on", "ON"])
def test_flaga_rozumie_typowe_zapisy(wartosc: str) -> None:
    assert zbuduj_kryteria({"obserwowane": wartosc}).tylko_obserwowane is True


def test_samo_ciasteczko_niczego_nie_filtruje() -> None:
    """„Nowe od ostatniej wizyty" włącza użytkownik, nie obecność ciasteczka."""
    assert zbuduj_kryteria({}, ostatnia_wizyta=WIZYTA).nowe_od is None
    assert zbuduj_kryteria({"nowe": "1"}, ostatnia_wizyta=WIZYTA).nowe_od == WIZYTA


def test_bez_ciasteczka_widok_nowych_nie_filtruje_nic() -> None:
    """Pierwsza wizyta: „nowe od ostatniej wizyty" nie ma punktu odniesienia."""
    assert zbuduj_kryteria({"nowe": "1"}, ostatnia_wizyta=None).nowe_od is None


def test_parametry_przezywaja_obieg_tam_i_z_powrotem() -> None:
    """Bez tego link „dalej" albo zmiana sortowania gubiłyby filtry."""
    zrodlowe = {
        "szukaj": "passat",
        "marka": "Volkswagen",
        "cena_do": "80000",
        "status": "wszystkie",
        "obserwowane": "1",
        "sort": Sortowanie.CENA_ROSNACO.value,
    }
    kryteria = zbuduj_kryteria(zrodlowe)
    odtworzone = zbuduj_kryteria(na_parametry(kryteria))
    assert odtworzone == kryteria


def test_kursor_przezywa_obieg() -> None:
    kursor = Kursor(wartosc="2026-09-07T12:00:00+00:00", id=42)
    assert Kursor.odkoduj(kursor.zakoduj()) == kursor


def test_kursor_ogona_z_pustymi_wartosciami() -> None:
    """`None` w kursorze znaczy „jestem już w ogonie z pustym kluczem"."""
    kursor = Kursor(wartosc=None, id=7)
    assert Kursor.odkoduj(kursor.zakoduj()) == kursor


@pytest.mark.parametrize("smiec", ["", "!!!", "YWJj", "eyJhIjogMX0="])
def test_uszkodzony_kursor_daje_pierwsza_strone_a_nie_blad(smiec: str) -> None:
    """Skopiowany w połowie adres ma pokazać listę, nie stronę błędu."""
    assert kursor_z_parametrow({"kursor": smiec}) is None
