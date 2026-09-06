"""Parser EFL na fixtures — offline, bez sieci (SPEC.md §13).

Selektory i nazwy pól pochodzą wyłącznie z plików w `fixtures/efl/`
(SPEC.md §4). Jeśli któryś z tych testów padnie po zmianie szablonu serwisu,
to jest właśnie sygnał, po który tu są.
"""

from __future__ import annotations

import pathlib

import pytest

from app.domain.errors import ParseFailed
from app.infrastructure.sources.efl import parser

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "efl"


def wczytaj(nazwa: str) -> str:
    return (FIXTURES / nazwa).read_text(encoding="utf-8", errors="replace")


def test_lista_zwraca_wszystkie_pozycje_ze_strony() -> None:
    pozycje = parser.sparsuj_liste(wczytaj("lista-01.html"))
    assert len(pozycje) == 8
    assert all(p.external_id.isdigit() for p in pozycje)
    assert (
        len({p.external_id for p in pozycje}) == 8
    ), "identyfikatory mają być unikalne"


def test_external_id_pochodzi_z_numeru_a_nie_ze_slugu() -> None:
    """RECON.md §4.1 — slug bywa niespójnie zakodowany, numer nie."""
    pozycje = parser.sparsuj_liste(wczytaj("lista-01.html"))
    pierwsza = pozycje[0]
    assert pierwsza.external_id == "435663"
    assert pierwsza.url.endswith("-id435663")
    assert pierwsza.url.startswith("https://aukcje.efl.com.pl/Auction/")


def test_lista_niesie_cene_czas_i_dane_techniczne() -> None:
    pozycja = parser.sparsuj_liste(wczytaj("lista-01.html"))[0]
    assert pozycja.pola["cena"] == "49\xa0410,00 zł"
    assert pozycja.pola["do_konca"] == "4 dni"
    assert pozycja.pola["Rok produkcji"] == "2022"
    assert pozycja.pola["Przebieg odczytany"] == "180848km"
    assert pozycja.pola["Typ nadwozia"] == "Sedan"
    assert pozycja.pola["Moc silnika"] == "163KM"


def test_szczegoly_niosa_vin_cene_liczbe_ofert_i_absolutny_koniec() -> None:
    d = parser.sparsuj_szczegoly(
        wczytaj("szczegoly-435508.html"), "435508", "https://x"
    )
    # Fixtures sa zredagowane przed publikacja repo (tools/redakcja_fixtures.py),
    # wiec VIN jest syntetyczny — zachowuje ksztalt, nie identyfikuje pojazdu.
    assert d.pola["VIN"] == "TMBC0XXT1S7Y8X23F"
    assert d.pola["cena"] == "48\xa0600,00"
    assert d.pola["liczba_ofert"] == "1"
    # RECON.md §4.1: obok zgrubnego "22 godz." stoi absolutny znacznik.
    assert d.pola["koniec"] == "07.09.2026 10:47:00"


def test_flaga_ceny_minimalnej_zapisuje_fakt_a_nie_wniosek() -> None:
    """Brak komunikatu może znaczyć „osiągnięta" ALBO „nie ustalono jej wcale".

    Tych dwóch przypadków nie da się na tej stronie rozróżnić, więc zapisujemy
    wyłącznie obecność komunikatu.
    """
    z_komunikatem = parser.sparsuj_szczegoly(
        wczytaj("szczegoly-435587.html"), "435587", "https://x"
    )
    assert z_komunikatem.pola["cena_minimalna_nieosiagnieta"] == "True"

    bez_komunikatu = parser.sparsuj_szczegoly(
        wczytaj("szczegoly-435508.html"), "435508", "https://x"
    )
    assert "cena_minimalna_nieosiagnieta" not in bez_komunikatu.pola


def test_historia_ofert_jest_inline_i_bez_logowania() -> None:
    """RECON.md §4.1 — dla EFL §11.8 jest dane wprost, bez zgadywania."""
    oferty = parser.sparsuj_oferty(wczytaj("szczegoly-435508.html"))
    assert oferty == [
        {
            "kod": "106125",
            "kwota": "48\xa0600,00 zł",
            "data": "2026.09.04 13:00:52.2194",
        }
    ]


def test_pusta_historia_ofert_to_pusta_lista_a_nie_blad() -> None:
    assert parser.sparsuj_oferty(wczytaj("szczegoly-435587.html")) == []


def test_paginacja_jest_liczona_od_zera() -> None:
    """Separatory w HTML są zakodowane jako `&amp;`, co łatwo przeoczyć."""
    assert parser.numery_stron(wczytaj("lista-01.html")) == [0, 1, 2, 34]


def test_strona_ktora_nie_jest_aukcja_konczy_sie_jasnym_bledem() -> None:
    with pytest.raises(ParseFailed, match="div.product"):
        parser.sparsuj_szczegoly("<html><body>nic tu nie ma</body></html>", "1", "u")


def test_lista_ignoruje_smieci_zamiast_sie_wywalac() -> None:
    """Kafelek bez linku nie ma prawa przewrócić całego przemiatu."""
    assert parser.sparsuj_liste('<div class="OfferList"><p>bez linku</p></div>') == []
