"""Odczyt formularza logowania z realnych stron (SPEC.md §10.2, §4).

Selektory i nazwy pól pochodzą **wyłącznie z fixtures** — te testy są
dowodem, że reguła „nic zaszytego na sztywno" nie jest tu ozdobnikiem,
tylko koniecznością.
"""

from __future__ import annotations

import pathlib

import pytest

from app.domain.errors import ParseFailed
from app.infrastructure.auth.formularz import wczytaj_formularz

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures"


def html(serwis: str) -> str:
    return (FIXTURES / serwis / "logowanie.html").read_text(
        encoding="utf-8", errors="replace"
    )


def test_efl_formularz_z_tokenem_i_para_rememberme() -> None:
    """EFL: `/Account/LogOn`, pola `Login`, `Password`, token CSRF."""
    formularz = wczytaj_formularz(
        html("efl"), "https://aukcje.efl.com.pl/Account/LogOn"
    )

    assert formularz.akcja == "https://aukcje.efl.com.pl/Account/LogOn"
    assert formularz.metoda == "post"
    assert formularz.pole_loginu == "Login"
    assert formularz.pole_hasla == "Password"
    assert formularz.pola["__RequestVerificationToken"], "token ma być przepisany"

    # `RememberMe` występuje dwa razy: checkbox `true` i ukryte `false`.
    # Checkbox jest niezaznaczony, więc przeglądarka wysłałaby `false` —
    # i my też. Przepisanie checkboxa dałoby „zapamiętaj mnie" bez pytania.
    assert formularz.pola["RememberMe"] == "false"


def test_autoprzetarg_bierze_token_z_formularza_logowania_a_nie_z_wyszukiwarki() -> (
    None
):
    """Na stronie są DWA `__RequestVerificationToken`, o różnych wartościach.

    Pierwszy należy do formularza szybkiego wyszukiwania. Wzięcie „pierwszego
    tokenu na stronie" daje token od wyszukiwarki i odrzucone logowanie —
    błąd, którego nie widać inaczej niż przez porównanie z HTML-em.
    """
    surowy = html("autoprzetarg")
    formularz = wczytaj_formularz(
        surowy, "https://autoprzetarg.pl/uzytkownik/logowanie"
    )

    assert formularz.akcja == "https://autoprzetarg.pl/uzytkownik/logowanie"
    assert formularz.pole_loginu == "Login"
    assert formularz.pole_hasla == "Password"

    tokeny = [
        wiersz.split('value="')[1].split('"')[0]
        for wiersz in surowy.splitlines()
        if "__RequestVerificationToken" in wiersz and 'value="' in wiersz
    ]
    assert (
        len(tokeny) >= 2
    ), "fixture ma zawierać oba tokeny — inaczej test nic nie waży"
    assert formularz.pola["__RequestVerificationToken"] == tokeny[-1]
    assert formularz.pola["__RequestVerificationToken"] != tokeny[0]


def test_pole_hasla_rozpoznajemy_niezaleznie_od_wielkosci_liter() -> None:
    """autoprzetarg ma `type="Password"` z wielkiej litery.

    Porównanie wprost z `"password"` gubi ten formularz i kończy się
    komunikatem „strona wymaga JavaScriptu", który jest nieprawdą.
    """
    assert 'type="Password"' in html("autoprzetarg")
    assert wczytaj_formularz(
        html("autoprzetarg"), "https://autoprzetarg.pl/"
    ).pole_hasla


def test_wszystkie_ukryte_pola_sa_przepisywane() -> None:
    """Bez tego logowanie odpada na brakującym polu, o którym nikt nie wiedział."""
    formularz = wczytaj_formularz(
        html("efl"), "https://aukcje.efl.com.pl/Account/LogOn"
    )
    assert "ReturnUrl" in formularz.pola


def test_leasygroup_nie_ma_formularza_logowania_i_mowimy_to_wprost() -> None:
    """Zebrany zrzut to formularz przypomnienia hasła, nie logowania.

    Ma jedno pole `mail`, checkbox regulaminu i ukryte pole o **losowej
    nazwie**. Parser ma odmówić z czytelnym powodem, a nie zgadywać, że
    `mail` to login — zgadywanie skończyłoby się wysłaniem hasła w pole,
    które hasła nie oczekuje.
    """
    with pytest.raises(ParseFailed, match="polem hasła"):
        wczytaj_formularz(
            html("leasygroup"), "https://aukcje.leasygroup.pl/zaloguj-sie/"
        )


def test_wzgledna_akcja_jest_rozwijana_do_adresu_bezwzglednego() -> None:
    strona = """
      <form action="../konto/login" method="POST">
        <input type="hidden" name="csrf" value="abc">
        <input type="text" name="u"><input type="password" name="p">
      </form>
    """
    formularz = wczytaj_formularz(strona, "https://serwis.test/panel/logowanie")
    assert formularz.akcja == "https://serwis.test/konto/login"
    assert formularz.metoda == "post", "metodę normalizujemy do małych liter"


def test_pusta_akcja_znaczy_ten_sam_adres() -> None:
    """Zgodnie z HTML-em, i tak ma to leasygroup w swoim `<form method=post>`."""
    strona = '<form method="post"><input type="password" name="p"></form>'
    assert (
        wczytaj_formularz(strona, "https://serwis.test/logowanie").akcja
        == "https://serwis.test/logowanie"
    )


def test_haslo_i_login_trafiaja_do_wyslanych_pol() -> None:
    formularz = wczytaj_formularz(
        html("efl"), "https://aukcje.efl.com.pl/Account/LogOn"
    )
    dane = formularz.z_poswiadczeniami("jan", "bardzo-tajne")
    assert dane["Login"] == "jan"
    assert dane["Password"] == "bardzo-tajne"
    assert (
        dane["__RequestVerificationToken"]
        == formularz.pola["__RequestVerificationToken"]
    )


def test_repr_formularza_nie_pokazuje_wartosci_ukrytych_pol() -> None:
    """SPEC.md §10.2 — token sesji nie ma po co trafiać do logu."""
    formularz = wczytaj_formularz(
        html("efl"), "https://aukcje.efl.com.pl/Account/LogOn"
    )
    token = formularz.pola["__RequestVerificationToken"]
    assert token not in repr(formularz)
    assert token not in str(formularz)


def test_przyciski_nie_sa_przepisywane() -> None:
    """Przycisk wysyła wartość tylko wtedy, gdy się go kliknie."""
    strona = """
      <form method="post">
        <input type="text" name="u"><input type="password" name="p">
        <input type="submit" name="zaloguj" value="Zaloguj">
      </form>
    """
    assert "zaloguj" not in wczytaj_formularz(strona, "https://serwis.test/").pola


def test_zaznaczony_checkbox_jest_przepisywany() -> None:
    strona = """
      <form method="post">
        <input type="text" name="u"><input type="password" name="p">
        <input type="checkbox" name="zgoda" value="1" checked>
      </form>
    """
    assert wczytaj_formularz(strona, "https://serwis.test/").pola["zgoda"] == "1"
