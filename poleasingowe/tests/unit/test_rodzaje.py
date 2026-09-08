"""Rozpoznawanie rodzaju pojazdu (SPEC.md §6.2, §12).

Testy chodzą po **zmierzonym słownictwie** — wartościach, które faktycznie
stoją w `fixtures/`, a nie po wymyślonych. Stąd tak dużo dosłownych napisów:
to one są tu specyfikacją.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.domain.enums import RodzajPojazdu
from app.infrastructure.sources import rodzaje


@pytest.mark.parametrize(
    "kategoria,oczekiwany",
    [
        # autoprzetarg.pl — segment kategorii w adresie aukcji. Te pięć
        # wartości pokrywa całą listę w fixtures (2 strony, 24 pozycje).
        ("Samochody-osobowe", RodzajPojazdu.OSOBOWY),
        ("Samochody-dostawcze", RodzajPojazdu.DOSTAWCZY),
        ("Samochody-ciężarowe", RodzajPojazdu.CIEZAROWY),
        ("Naczepy-i-przyczepy", RodzajPojazdu.PRZYCZEPA),
        ("Motocykle", RodzajPojazdu.MOTOCYKL),
        # EFL pisze to samo na trzy sposoby w jednej liście.
        ("osobowy", RodzajPojazdu.OSOBOWY),
        ("Osobowy", RodzajPojazdu.OSOBOWY),
        ("Samochód osobowy", RodzajPojazdu.OSOBOWY),
    ],
)
def test_deklaracja_zrodla_wystarcza(kategoria: str, oczekiwany: RodzajPojazdu) -> None:
    assert rodzaje.z_kategorii(kategoria) is oczekiwany


def test_kategoria_vehicles_nie_przesadza_rodzaju() -> None:
    """Jedyna kategoria, która NIE jest deklaracją rodzaju.

    poleasingowe.pl trzyma w `vehicles` osobowe, dostawcze i ciągniki
    siodłowe razem — zmierzone na `lista-vehicles-01.html`. Uznanie jej za
    „osobowe" wpuściłoby ciągnik siodłowy do domyślnego widoku listy.
    """
    assert rodzaje.z_kategorii("vehicles") is RodzajPojazdu.NIEZNANY
    assert (
        rodzaje.rozpoznaj(kategoria="vehicles", nazwa="MAN TGX CIĄGNIK SIODŁOWY")
        is RodzajPojazdu.CIEZAROWY
    )


@pytest.mark.parametrize(
    "nazwa,oczekiwany",
    [
        # Wszystkie dziesięć nazw z `fixtures/poleasingowe/lista-vehicles-01`.
        ("MAN TGX CIĄGNIK SIODŁOWY", RodzajPojazdu.CIEZAROWY),
        ("SKODA SUPERB KOMBI", RodzajPojazdu.OSOBOWY),
        ("VOLKSWAGEN GOLF KOMBI", RodzajPojazdu.OSOBOWY),
        ("FORD MONDEO KOMBI", RodzajPojazdu.OSOBOWY),
        ("FIAT DUCATO PLANDEKA", RodzajPojazdu.DOSTAWCZY),
        ("BMW X6 M60I XDRIVE SUV", RodzajPojazdu.OSOBOWY),
        ("AUDI RS6 KOMBI", RodzajPojazdu.OSOBOWY),
        ("KIA PROCEED KOMBI", RodzajPojazdu.OSOBOWY),
        ("FIAT DUCATO FURGON BLASZAK", RodzajPojazdu.DOSTAWCZY),
        ("PEUGEOT BOXER 335 2.0 FURGON BLASZAK", RodzajPojazdu.DOSTAWCZY),
    ],
)
def test_nazwa_wystarcza_gdy_zrodlo_milczy(
    nazwa: str, oczekiwany: RodzajPojazdu
) -> None:
    assert rodzaje.z_nazwy(nazwa) is oczekiwany


def test_dluzsze_wyrazenie_wygrywa_z_krotszym() -> None:
    """„CIĄGNIK SIODŁOWY" to ciężarówka, „CIĄGNIK ROLNICZY" już nie.

    Kolejność w `NADWOZIA` jest tu jedynym mechanizmem — test pilnuje, żeby
    dopisanie czegoś na początku listy nie przestawiło rozstrzygnięcia.
    """
    assert rodzaje.z_nazwy("MAN TGX CIĄGNIK SIODŁOWY") is RodzajPojazdu.CIEZAROWY
    assert rodzaje.z_nazwy("URSUS CIĄGNIK ROLNICZY") is RodzajPojazdu.INNY


def test_dopasowanie_tylko_na_cale_slowa() -> None:
    """Bez granic słowa „SUV" trafiłby w środek modelu, a to zmienia wynik."""
    assert rodzaje.z_nazwy("NISSAN QASHQAI") is RodzajPojazdu.NIEZNANY
    assert rodzaje.z_nazwy("NISSAN QASHQAI SUV") is RodzajPojazdu.OSOBOWY


def test_nierozpoznane_zostaje_nieznane() -> None:
    """`NIEZNANY`, nie `OSOBOWY`.

    Zgadnięcie „to pewnie auto" byłoby wygodne i czasem fałszywe, a fałsz
    trafiłby prosto do domyślnego widoku listy.
    """
    assert rodzaje.z_nazwy("TESLA MODEL Y") is RodzajPojazdu.NIEZNANY
    assert rodzaje.z_nazwy("") is RodzajPojazdu.NIEZNANY
    assert rodzaje.z_nazwy(None) is RodzajPojazdu.NIEZNANY
    assert rodzaje.z_kategorii(None) is RodzajPojazdu.NIEZNANY


def test_ogonki_i_wielkosc_liter_nie_maja_znaczenia() -> None:
    """Serwisy piszą kategorie raz z ogonkami, raz bez — jak przy paliwach."""
    assert rodzaje.z_kategorii("Samochody-ciężarowe") is RodzajPojazdu.CIEZAROWY
    assert rodzaje.z_kategorii("SAMOCHODY CIEZAROWE") is RodzajPojazdu.CIEZAROWY
    assert rodzaje.z_nazwy("man tgx ciagnik siodlowy") is RodzajPojazdu.CIEZAROWY


def test_deklaracja_ma_pierwszenstwo_przed_nazwa() -> None:
    """Nazwa bywa myląca; kategoria pochodzi od serwisu.

    Naczepa chłodnia ma w nazwie „IZOTERMA", co samo w sobie wskazywałoby na
    dostawczy — ale serwis mówi wprost, że to naczepa.
    """
    assert (
        rodzaje.rozpoznaj(kategoria="Naczepy-i-przyczepy", nazwa="SCHMITZ IZOTERMA")
        is RodzajPojazdu.PRZYCZEPA
    )


def test_migracja_i_kod_maja_ten_sam_slownik() -> None:
    """Migracja `009_rodzaje.sql` POWTARZA reguły za kodem Pythona.

    Powtórzenie jest konieczne — SQL nie zawoła `rodzaje.py` — ale dwie listy
    rozjeżdżają się przy pierwszym dopisanym nadwoziu i nikt tego nie
    zauważy, bo obie strony nadal działają. Ten test jest jedynym miejscem,
    w którym rozjazd boli od razu.
    """
    sql = (
        pathlib.Path(__file__).resolve().parents[2] / "app/migrations/009_rodzaje.sql"
    ).read_text(encoding="utf-8")

    blok = sql.split("slownik(wzorzec, rodzaj, pierwszenstwo)")[1]
    z_sql = [
        (m.group(1), m.group(2))
        for m in re.finditer(r"\('([^']+)',\s*'([A-Z]+)',\s*\d+\)", blok)
    ]
    z_kodu = [(wzorzec, rodzaj.value) for wzorzec, rodzaj in rodzaje.NADWOZIA]
    assert z_sql == z_kodu, "słownik nadwozi rozjechał się z migracją"

    # Wartości dozwolone w CHECK muszą pokrywać cały enum — inaczej pierwszy
    # zapis nowego rodzaju wywali się na ograniczeniu bazy.
    dozwolone = set(re.findall(r"'([A-Z]+)'", sql.split("CHECK (")[1].split(")")[0]))
    assert {r.value for r in RodzajPojazdu} <= dozwolone
