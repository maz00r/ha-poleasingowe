"""Wybór ekspertyzy i raportów historii po treści (0.31.0, 0.35.0, 0.35.2).

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


def test_uszkodzony_pdf_daje_pusty_tekst() -> None:
    assert ekspertyza.tekst_pdf(b"%PDF-1.7 to nie jest PDF") == ""


def _raport_autodna(vin: str = VIN) -> str:
    """Układ warstwy tekstowej prawdziwego raportu, z wymyślonymi danymi."""
    return f"""Raport Historii Pojazdu autoDNA {vin}
{vin}
5f28b815-aef49328 2026-05-11 08:29:19 UTC +0200 Strona 1
Raport Historii Pojazdu
autoDNA
Data pierwszej rejestracji 2017-03-03
1 3 9 1 1 6 km
Uwaga: Nie jest to aktualny przebieg sprawdzanego pojazdu.
HISTORIA POJAZDU
11.02.2025
Wykonano okresowe badanie techniczne Stan drogomierza108 791 km
"""


def test_raport_autodna_jest_osobno_i_nie_wypiera_ekspertyzy() -> None:
    dluga_ekspertyza = RAPORT + "wyposażenie " * 2000
    dlugi_raport = _raport_autodna() + "wpis historii " * 1000

    wybrane = ekspertyza.wybierz(
        [
            ("Pobierz DARMOWY raport — raport-autodna-x.pdf", dlugi_raport),
            ("Opinia.pdf", dluga_ekspertyza),
        ],
        VIN,
    )

    assert [d.raport_historii for d in wybrane] == [False, True]
    assert wybrane[0].nazwa == "Opinia.pdf"
    assert len(wybrane[0].tekst) == ekspertyza.MAKS_ZNAKOW
    assert wybrane[1].tekst == dlugi_raport


def test_raport_rozpoznany_po_tytule_mimo_innej_nazwy_pliku() -> None:
    (d,) = ekspertyza.wybierz([("zalacznik-67298.pdf", _raport_autodna())], VIN)
    assert d.raport_historii and d.vin_zgodny


def test_sama_wzmianka_o_autodna_nie_robi_z_ekspertyzy_raportu() -> None:
    (d,) = ekspertyza.wybierz(
        [("Opinia.pdf", RAPORT + "Sprawdzono też serwis autoDNA.")], VIN
    )
    assert not d.raport_historii


def test_raport_autodna_cudzego_auta_odpada() -> None:
    wybrane = ekspertyza.wybierz(
        [("raport-autodna.pdf", _raport_autodna("WVWZZZ1KZAW000001"))], VIN
    )
    assert wybrane == ()


def test_raport_bez_vin_aukcji_i_tak_trafia_do_modelu() -> None:
    (d,) = ekspertyza.wybierz([("raport-autodna.pdf", _raport_autodna())], None)
    assert d.raport_historii and not d.vin_zgodny


def _raport_rzadowy(vin: str = VIN) -> str:
    """Układ warstwy tekstowej raportu z gov.pl, z wymyślonymi danymi."""
    return f"""Raport z usługi Historia pojazdu
Status rejestracji: zarejestrowany
Numer VIN, nadwozia, podwozia lub ramy {vin}
Ostatni stan licznika 142 525 km
Oś czasu
07.12.2022 Dodatkowe badanie techniczne
Wynik badania: pozytywny
Rodzaj badania: taksówka
Odczytany stan licznika: 53 305 km
14.02.2025 Zgłoszenie kradzieży pojazdu!
AutoDNA pozyskuje dane m.in. z Niemiec, Francji, Belgii.
"""


def test_raport_rzadowy_jest_osobno_i_nie_wypiera_ekspertyzy() -> None:
    karta = RAPORT + "wyposażenie " * 2000

    wybrane = ekspertyza.wybierz(
        [
            ("KARTA.INFORMACYJNA.pdf", karta),
            ("HISTORIA.POJAZDU.pdf", _raport_rzadowy()),
        ],
        VIN,
    )

    assert [d.nazwa for d in wybrane] == [
        "KARTA.INFORMACYJNA.pdf",
        "HISTORIA.POJAZDU.pdf",
    ]
    assert [d.raport_historii for d in wybrane] == [False, True]
    assert wybrane[1].tekst == _raport_rzadowy()


def test_oba_raporty_historii_ida_razem() -> None:
    wybrane = ekspertyza.wybierz(
        [("h.pdf", _raport_rzadowy()), ("raport-autodna.pdf", _raport_autodna())], VIN
    )
    assert {d.nazwa for d in wybrane} == {"h.pdf", "raport-autodna.pdf"}
    assert all(d.raport_historii for d in wybrane)


def test_raporty_historii_dziela_jeden_limit() -> None:
    wybrane = ekspertyza.wybierz(
        [
            ("h.pdf", _raport_rzadowy() + "x" * 40_000),
            ("raport-autodna.pdf", _raport_autodna() + "x" * 40_000),
        ],
        VIN,
    )
    assert sum(len(d.tekst) for d in wybrane) == ekspertyza.MAKS_ZNAKOW_HISTORII


def test_raport_rzadowy_cudzego_auta_odpada() -> None:
    wybrane = ekspertyza.wybierz(
        [("HISTORIA.POJAZDU.pdf", _raport_rzadowy("WVWZZZ1KZAW000001"))], VIN
    )
    assert wybrane == ()


def test_wzmianka_o_usludze_nie_robi_z_ekspertyzy_raportu() -> None:
    (d,) = ekspertyza.wybierz(
        [("Opinia.pdf", RAPORT + "Sprawdzono usługę Historia pojazdu.")], VIN
    )
    assert not d.raport_historii


def test_raport_jest_przycinany_do_wlasnego_limitu() -> None:
    (d,) = ekspertyza.wybierz(
        [("raport-autodna.pdf", _raport_autodna() + "x" * 40_000)], VIN
    )
    assert len(d.tekst) == ekspertyza.MAKS_ZNAKOW_HISTORII
