"""Wybór ekspertyzy po treści i redakcja przed modelem AI (0.31.0).

Teksty odtwarzają układ warstwy tekstowej trzech zmierzonych raportów
(dawro/Stellantis, Leasygroup, mLeasing/DEKRA) — z wymyślonymi danymi.
"""

from __future__ import annotations

from app.infrastructure import ekspertyza

VIN = "VF3YBTMFB12A67998"

RAPORT = f"""Nr zlecenia: 74/24/09/2026 Dawro
Rzeczoznawca:
Domaniewska 44A
Jan Testowy/zatw. Pion Weryfikacyjny eRzeczoznawcy
Wycena wartości
DANE IDENTYFIKACYJNE POJAZDU
VIN:
 {" ".join(VIN)}
Nr rejestracyjny:
RST12345
Wskazanie drogomierza
292443 km
Data pierwszej rejestracji 21.06.2016
Stan techniczny: uszkodzenia zderzaka przedniego
Oględziny wykonał: Adam Przykładowy
/ SRTSiRD 11013 Jan Testowy System INFO-EKSPERT
PEUGEOT_RST12345
tel. +48 71 340 08 55, biuro@example.test
Wartość rynkowa brutto: 41 000 zł
"""

REGULAMIN = (
    "Regulamin aukcji. Uczestnik akceptuje stan pojazdu i warunki sprzedaży. "
    "Wzór umowy kupna-sprzedaży."
)


def test_ekspertyza_tego_auta_wygrywa_niezaleznie_od_nazwy_pliku() -> None:
    wybrane = ekspertyza.wybierz(
        [("RST12345.pdf", RAPORT), ("regulamin.pdf", REGULAMIN)], VIN
    )
    assert [d.nazwa for d in wybrane] == ["RST12345.pdf"]
    assert wybrane[0].vin_zgodny


def test_ekspertyza_innego_pojazdu_odpada() -> None:
    assert ekspertyza.wybierz([("Ekspertyza_1.pdf", RAPORT)], "WVWZZZ1KZAW000001") == ()


def test_bez_vin_aukcji_wystarcza_tresc_raportu() -> None:
    (d,) = ekspertyza.wybierz([("x.pdf", RAPORT)], None)
    assert not d.vin_zgodny


def test_regulamin_nie_jest_ekspertyza() -> None:
    assert ekspertyza.wybierz([("Ekspertyza.pdf", REGULAMIN)], VIN) == ()


def test_redakcja_zdejmuje_identyfikatory_i_osoby_zostawia_stan() -> None:
    tekst = ekspertyza.zredaguj(RAPORT, VIN)
    assert VIN not in tekst.replace(" ", "")
    assert "RST12345" not in tekst
    assert "Testowy" not in tekst and "Przykładowy" not in tekst
    assert "340 08 55" not in tekst and "biuro@" not in tekst
    assert "uszkodzenia zderzaka" in tekst
    assert "41 000 zł" in tekst
    assert "292443 km" in tekst


def test_uszkodzony_pdf_daje_pusty_tekst() -> None:
    assert ekspertyza.tekst_pdf(b"%PDF-1.7 to nie jest PDF") == ""
