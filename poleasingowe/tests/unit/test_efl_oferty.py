"""Lista ofert EFL: parser → mapper → encje (SPEC.md §11.8).

Dlaczego to jest osobny plik, a nie dopisek do `test_efl_parser.py`: tam
sprawdzamy, czy umiemy odczytać stronę, tu — czy rozumiemy, co odczytaliśmy.
To druga rzecz i psuje się z innych powodów.

Wszystko na plikach z `fixtures/efl/`, bez sieci (SPEC.md §13).
"""

from __future__ import annotations

import datetime as dt
import pathlib

from app.application.ports import SurowaOferta, SurowaOfertaUczestnika
from app.infrastructure.sources.efl import mapper, parser

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "efl"
TERAZ = dt.datetime(2026, 9, 7, 12, 0, tzinfo=dt.UTC)


def wczytaj(nazwa: str) -> str:
    return (FIXTURES / nazwa).read_text(encoding="utf-8", errors="replace")


def surowa_z_fixture(nazwa: str, external_id: str = "435508") -> SurowaOferta:
    """Buduje `SurowaOferta` tak, jak robi to adapter — z tego samego HTML-a."""
    html = wczytaj(nazwa)
    return SurowaOferta(
        external_id=external_id,
        url=f"https://aukcje.efl.com.pl/Auction/x-id{external_id}",
        pola={},
        oferty=tuple(
            SurowaOfertaUczestnika(kod=w["kod"], kwota=w["kwota"], zlozona=w["data"])
            for w in parser.sparsuj_oferty(html)
        ),
    )


def test_oferty_z_zakonczonej_aukcji_maja_kwoty_i_czasy() -> None:
    """Trzy oferty z `szczegoly-zakonczona-435508.html`, przeliczone na UTC.

    Serwis podaje czas lokalny bez strefy; 10:42:05 czasu polskiego we wrześniu
    to 08:42:05 UTC. Pomyłka o te dwie godziny nie wywraca niczego widocznie —
    po prostu przesuwa cały przebieg licytacji.
    """
    oferty = mapper.na_oferty(
        surowa_z_fixture("szczegoly-zakonczona-435508.html"), auction_id=7, teraz=TERAZ
    )

    assert len(oferty) == 3
    assert [str(o.amount.amount) for o in oferty] == [
        "51600.00",
        "51400.00",
        "49000.00",
    ]
    assert oferty[0].placed_at == dt.datetime(
        2026, 9, 7, 8, 42, 5, 880000, tzinfo=dt.UTC
    )
    assert all(o.auction_id == 7 for o in oferty)
    assert all(o.first_seen_at == TERAZ for o in oferty)


def test_ten_sam_licytant_ma_ten_sam_pseudonim_w_aukcji() -> None:
    """Bez tego nie dałoby się powiedzieć, kto kogo przebijał."""
    assert mapper.pseudonim("435508", "106125") == mapper.pseudonim("435508", "106125")


def test_ten_sam_licytant_ma_inny_pseudonim_w_innej_aukcji() -> None:
    """Sedno pseudonimizacji z `012_oferty.sql`.

    Gdyby pseudonim zależał wyłącznie od kodu, baza pozwalałaby zestawić ze
    sobą wszystkie aukcje, w których ktoś licytował. Takiego zbioru nie
    budujemy — do niczego w tej aplikacji nie jest potrzebny.
    """
    assert mapper.pseudonim("435508", "106125") != mapper.pseudonim("435587", "106125")


def test_kod_serwisu_nie_przezywa_mapowania() -> None:
    """Surowy kod ma zostać w warstwie antykorupcyjnej i nigdzie dalej."""
    oferty = mapper.na_oferty(
        surowa_z_fixture("szczegoly-zakonczona-435508.html"), auction_id=7, teraz=TERAZ
    )
    pseudonimy = {o.uczestnik for o in oferty}
    assert not pseudonimy & {"106125", "106147", "106148"}


def test_aukcja_bez_ofert_daje_pusta_krotke() -> None:
    """`szczegoly-435587.html` ma zakładkę, ale nie ma w niej wierszy."""
    assert (
        mapper.na_oferty(
            surowa_z_fixture("szczegoly-435587.html", "435587"),
            auction_id=8,
            teraz=TERAZ,
        )
        == ()
    )


def test_wiersz_nie_do_odczytania_jest_pomijany_a_nie_wywraca_odpytu() -> None:
    """Oferty są dodatkiem do ceny i terminu, nie warunkiem ich zapisania.

    Utrata całego szczegółu aukcji przez jedną dziwną datę byłaby złą zamianą:
    cena i `ends_at` sterują harmonogramem (§11.2), lista ofert nie.
    """
    surowa = SurowaOferta(
        external_id="435508",
        url="https://x",
        pola={},
        oferty=(
            SurowaOfertaUczestnika("1", "51 600,00 zł", "2026.09.07 10:42:05.8800"),
            SurowaOfertaUczestnika("2", "51 400,00 zł", "wczoraj po południu"),
            SurowaOfertaUczestnika("3", "nie kwota", "2026.09.07 10:46:58.5933"),
        ),
    )
    oferty = mapper.na_oferty(surowa, auction_id=7, teraz=TERAZ)
    assert len(oferty) == 1, "zostaje ta jedna, którą dało się odczytać"


def test_czas_oferty_ma_inny_format_niz_termin_zakonczenia() -> None:
    """Kropki i ułamek sekundy — patrz `_CZAS_OFERTY` w mapperze.

    Ten sam wzorzec dla obu formatów rozluźniłby walidację terminu końcowego,
    a ten steruje całym harmonogramem (§11.2).
    """
    surowa = SurowaOferta(
        external_id="1",
        url="https://x",
        pola={},
        # Format terminu zakonczenia aukcji: `07.09.2026 10:42:05`.
        oferty=(SurowaOfertaUczestnika("1", "100,00 zł", "07.09.2026 10:42:05"),),
    )
    assert mapper.na_oferty(surowa, auction_id=1, teraz=TERAZ) == ()
