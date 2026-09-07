"""Zasłanianie poświadczeń (SPEC.md §10.2).

Wspólne dla logów i zrzutów, bo spec wymaga **tego samego** filtra w obu
miejscach.
"""

from __future__ import annotations

import time

import pytest

from app.infrastructure.redakcja import MASKA, Redakcja


@pytest.mark.parametrize(
    "wejscie,zniknac_ma",
    [
        ("Set-Cookie: sessionid=abc123; HttpOnly", "abc123"),
        ("set-cookie: laravel_session=qqq", "qqq"),
        ("Cookie: XSRF-TOKEN=xyz789", "xyz789"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOiJIUzI1NiJ9"),
        ('{"csrf_token": "abc-123"}', "abc-123"),
        ('{"password": "p0ufne"}', "p0ufne"),
        ("?api_token=sekret123&x=1", "sekret123"),
        ('<input name="__RequestVerificationToken" value="MZMjRuM7">', "MZMjRuM7"),
    ],
)
def test_wzorce_zaslaniaja_wartosci(wejscie: str, zniknac_ma: str) -> None:
    wynik = Redakcja().zastosuj(wejscie)
    assert zniknac_ma not in wynik
    assert MASKA in wynik


def test_nazwa_naglowka_zostaje() -> None:
    """Po logu ma być widać, ŻE ciasteczko przyszło — bez pokazywania jakie."""
    wynik = Redakcja().zastosuj("Set-Cookie: sessionid=abc123")
    assert wynik.startswith("Set-Cookie:")


def test_sekrety_z_konfiguracji_znikaja_wszedzie() -> None:
    """Hasła nie da się rozpoznać po kształcie — może wyglądać jak słowo."""
    redakcja = Redakcja(["korepetycje"])
    assert redakcja.zastosuj("dawaj korepetycje z SQL-a") == f"dawaj {MASKA} z SQL-a"


def test_dsn_z_haslem_nie_przechodzi() -> None:
    redakcja = Redakcja(["bardzo-tajne"])
    wynik = redakcja.zastosuj("postgresql://rola:bardzo-tajne@host:5432/db")
    assert "bardzo-tajne" not in wynik
    assert "postgresql://rola:" in wynik, "reszta DSN-u jest przydatna w diagnozie"


def test_dluzsze_sekrety_ida_pierwsze() -> None:
    """Gdyby krótsze hasło było fragmentem dłuższego, zasłonięcie krótszego
    zostawiłoby ogon dłuższego — czyli część prawdziwego hasła."""
    redakcja = Redakcja(["tajne", "tajne-haslo-dluzsze"])
    assert redakcja.zastosuj("haslo=tajne-haslo-dluzsze") == f"haslo={MASKA}"


def test_puste_sekrety_sa_pomijane() -> None:
    """Pusty łańcuch pasuje wszędzie i zamieniłby tekst w same maski."""
    assert Redakcja(["", None]).zastosuj("zwykły tekst") == "zwykły tekst"  # type: ignore[list-item]


def test_tekst_bez_poswiadczen_zostaje_nietkniety() -> None:
    czysty = "zastosowano migracje: 001_init, 002_reporting, 003_domkniecie"
    assert Redakcja(["haslo"]).zastosuj(czysty) == czysty


def test_duzy_zrzut_redaguje_sie_szybko() -> None:
    """Wzorce muszą być odporne na nawroty.

    Zrzut HTML ma setki kilobajtów. Wzorzec z zagnieżdżonym kwantyfikatorem
    zamienia redakcję w zawieszenie procesu — tak wywaliło się kiedyś
    narzędzie do redakcji fixtures.
    """
    strona = ('<input name="csrf_token" value="' + "a" * 200 + '">') * 500
    start = time.monotonic()
    wynik = Redakcja(["nieistotne"]).zastosuj(strona)
    czas = time.monotonic() - start

    assert czas < 2.0, f"redakcja 300 kB trwała {czas:.1f} s — podejrzenie nawrotów"
    assert "a" * 200 not in wynik
