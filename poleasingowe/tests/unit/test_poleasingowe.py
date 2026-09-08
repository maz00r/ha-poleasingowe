"""Parser i mapper poleasingowe.pl na fixtures (SPEC.md §4, §13).

Offline, bez sieci. Wszystkie oczekiwane wartości pochodzą z plików
w `fixtures/poleasingowe/` — jeśli test twierdzi, że cena to 32 100, to
dlatego, że tyle stoi w zapisanym HTML-u.
"""

from __future__ import annotations

import datetime as dt
import pathlib
from decimal import Decimal

import pytest

from app.domain.enums import AuctionStatus, Currency
from app.domain.errors import ParseFailed
from app.domain.value_objects import Money
from app.infrastructure.sources.poleasingowe import mapper, parser
from app.infrastructure.sources.poleasingowe.source import hash_tresci

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "poleasingowe"
TERAZ = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.UTC)


def html(nazwa: str) -> str:
    return (FIXTURES / nazwa).read_text(encoding="utf-8", errors="replace")


def szczegoly(nazwa: str, external_id: str):  # type: ignore[no-untyped-def]
    return parser.sparsuj_szczegoly(
        html(nazwa), external_id, f"https://poleasingowe.pl/x/{external_id}"
    )


# --------------------------------------------------------------------------
# Lista
# --------------------------------------------------------------------------


def test_lista_daje_pozycje_z_identyfikatorami() -> None:
    pozycje = parser.sparsuj_liste(html("lista-vehicles-01.html"))
    assert len(pozycje) == 10
    assert all(p.external_id for p in pozycje)
    assert len({p.external_id for p in pozycje}) == 10, "bez duplikatów"


def test_identyfikator_jest_alfanumeryczny_a_nie_liczba() -> None:
    """RECON.md §4.2 — `external_id` to 8 znaków, kolumna tekstowa."""
    pozycje = parser.sparsuj_liste(html("lista-vehicles-01.html"))
    assert pozycje[0].external_id == "1dl8a2ae"
    assert not pozycje[0].external_id.isdigit()


def test_kafelek_niesie_cene_liczbe_ofert_i_lokalizacje() -> None:
    pozycje = parser.sparsuj_liste(html("lista-vehicles-01.html"))
    pola = pozycje[0].pola
    assert pola["nazwa"] == "MAN TGX CIĄGNIK SIODŁOWY"
    assert pola["cena"] == "110 600"
    assert pola["Ilość ofert"] == "0"
    assert pola["Lokalizacja"] == "Miękinia, Aukcyjna 1"
    assert pola["przebieg"] == "669796 km"


def test_lista_nie_udaje_ze_zna_dokladny_koniec() -> None:
    """Lista podaje datę bez godziny — „19 godzin (2026-09-07)".

    Wpisanie z tego `ends_at` dawałoby termin dokładny co do doby, a na tym
    stoi cały harmonogram z §11.2. Wolimy `None` niż fałszywą precyzję.
    """
    pozycje = parser.sparsuj_liste(html("lista-vehicles-01.html"))
    assert "2026-09-07" in pozycje[0].pola["do_konca_tekst"]
    assert "end_date" not in pozycje[0].pola

    aukcja = mapper.na_aukcje(pozycje[0], source_id=1, teraz=TERAZ)
    assert aukcja.ends_at is None


def test_paginacja_liczona_od_jedynki() -> None:
    """RECON.md §4.2. `&amp;` w HTML sprawia, że przed `page=` bywa średnik."""
    assert parser.numery_stron(html("lista-vehicles-01.html"))[:3] == [2, 3, 4]


# --------------------------------------------------------------------------
# Szczegóły — blok Alpine
# --------------------------------------------------------------------------


def test_szczegoly_biora_dane_z_bloku_alpine() -> None:
    """Widoczny tekst podaje „16 godzin"; blok Alpine — absolutny termin."""
    pola = szczegoly("szczegoly-9ooxn4x9.html", "9ooxn4x9").pola
    assert pola["cena"] == "32 100"
    assert pola["offers_count"] == "2"
    assert pola["bidders_count"] == "2"
    assert pola["instep_price"] == "100.00"
    assert pola["auction_pending"] == "true"
    assert pola["end_date"] == "2026-09-07 12:00:00"


def test_end_date_nie_jest_mylony_z_end_date_timer() -> None:
    """W bloku `endDateTimer` stoi TUŻ PRZED `endDate`, z tą samą wartością.

    Gdyby wzorzec łapał pierwsze wystąpienie, przy rozjeżdżających się
    wartościach czytalibyśmy nie ten termin, co trzeba.
    """
    surowy = html("szczegoly-9ooxn4x9.html")
    assert surowy.index("endDateTimer:") < surowy.index("endDate: moment")
    assert szczegoly("szczegoly-9ooxn4x9.html", "9ooxn4x9").pola["end_date"]


def test_tabela_danych_podstawowych_trafia_do_pol() -> None:
    pola = szczegoly("szczegoly-9ooxn4x9.html", "9ooxn4x9").pola
    assert pola["VIN"] == "WVWL5PHZPK2SWBY5U"
    assert pola["Przebieg"] == "210434 km"
    assert pola["Skrzynia biegów"] == "Manualna"
    assert pola["Pojemność silnika"] == "1968 ccm"
    assert pola["Moc silnika"] == "115 KM"


def test_login_zwyciezcy_nie_wychodzi_z_parsera() -> None:
    """SPEC.md §10.2 — to dane osobowe OSOBY TRZECIEJ.

    Blok Alpine niesie pełny, niezamaskowany `winner` obok zamaskowanego
    `winner_formated`. Nie chroni go żadne hasło, więc nie ma prawa trafić
    do `raw_json` ani do zrzutów. Wycinamy u źródła, nie licząc na filtr
    redakcji dalej w łańcuchu.
    """
    surowy = html("szczegoly-9ooxn4x9.html")
    assert (
        "winner:" in surowy
    ), "fixture ma zawierać to pole — inaczej test nic nie waży"

    pola = szczegoly("szczegoly-9ooxn4x9.html", "9ooxn4x9").pola
    assert "winner" not in pola
    assert not any("winner" in k.lower() for k in pola)


def test_strona_bez_bloku_aukcji_jest_bledem_parsowania() -> None:
    """Cicha pustka byłaby gorsza: aukcja wyglądałaby na pozbawioną ceny."""
    with pytest.raises(ParseFailed, match="brak bloku danych"):
        parser.sparsuj_szczegoly("<html><body>nic tu nie ma</body></html>", "x", "u")


# --------------------------------------------------------------------------
# content_hash — SPEC.md §11.3 krok 2
# --------------------------------------------------------------------------


def test_hash_liczy_sie_z_bloku_aukcji_a_nie_z_calej_strony() -> None:
    """Zmierzone 2026-09-08: trzy żądania — trzy różne treści całej strony.

    Zmienia się token CSRF i karuzela poleceń. Hash całości byłby zawsze
    inny, więc §11.3 krok 2 nie oszczędzałby tu niczego.
    """
    surowy = html("szczegoly-9ooxn4x9.html")
    blok = parser.wytnij_blok_aukcji(surowy)
    assert blok is not None
    assert "current_price" in blok and "endDate" in blok
    assert "csrfToken" not in blok

    z_innym_tokenem = surowy.replace("csrfToken = '", "csrfToken = 'ZMIENIONY", 1)
    assert z_innym_tokenem != surowy
    assert hash_tresci(surowy.encode()) == hash_tresci(z_innym_tokenem.encode())


def test_zmiana_ceny_zmienia_hash() -> None:
    """Hash ma być czuły na to, co nas obchodzi — inaczej gubilibyśmy zmiany."""
    surowy = html("szczegoly-9ooxn4x9.html")
    droższy = surowy.replace("current_price:  '32 100'", "current_price:  '32 200'", 1)
    assert droższy != surowy
    assert hash_tresci(surowy.encode()) != hash_tresci(droższy.encode())


def test_strona_bez_bloku_wraca_do_hasha_calosci() -> None:
    """Taka strona i tak się nie sparsuje — niestabilny hash niczego nie psuje."""
    assert hash_tresci(b"<html>bez bloku</html>")


# --------------------------------------------------------------------------
# Mapper
# --------------------------------------------------------------------------


def test_mapowanie_szczegolow_na_komplet_pol() -> None:
    aukcja = mapper.na_aukcje(
        szczegoly("szczegoly-9ooxn4x9.html", "9ooxn4x9"), source_id=7, teraz=TERAZ
    )
    assert aukcja.source_id == 7
    assert aukcja.external_id == "9ooxn4x9"
    # Marka w postaci kanonicznej — serwis pisze WERSALIKAMI, my nie.
    assert (aukcja.make, aukcja.model) == ("Volkswagen", "GOLF")
    assert aukcja.year == 2022
    assert aukcja.mileage is not None and aukcja.mileage.km == 210_434
    assert aukcja.gearbox == "Manualna"
    assert aukcja.engine_ccm == 1968
    assert aukcja.engine_hp == 115
    assert aukcja.body == "KOMBI"
    assert aukcja.color == "Biały"
    assert aukcja.bid_count == 2
    assert aukcja.price_current == Money(Decimal("32100"), Currency.PLN)
    assert aukcja.bid_increment_raw == "100.00"


def test_rocznik_i_nadwozie_nie_wchodza_do_marki_i_modelu() -> None:
    """Tytuł to „VOLKSWAGEN GOLF 2022  KOMBI", a oba są też osobnymi polami.

    Bez usunięcia powtórzeń rocznik wylądowałby jako model, a nadwozie jako
    wersja wyposażenia.
    """
    aukcja = mapper.na_aukcje(
        szczegoly("szczegoly-9ooxn4x9.html", "9ooxn4x9"), source_id=1, teraz=TERAZ
    )
    assert aukcja.model == "GOLF"
    assert aukcja.variant is None


def test_czas_konca_idzie_do_bazy_w_utc() -> None:
    """SPEC.md §8.2 — w bazie UTC. Serwis podaje `Europe/Warsaw`.

    7 września to czas letni, więc CEST = UTC+2 i 12:00 lokalnie to 10:00 UTC.
    """
    aukcja = mapper.na_aukcje(
        szczegoly("szczegoly-9ooxn4x9.html", "9ooxn4x9"), source_id=1, teraz=TERAZ
    )
    assert aukcja.ends_at == dt.datetime(2026, 9, 7, 10, 0, tzinfo=dt.UTC)


def test_auction_pending_to_jedyny_marker_zakonczenia() -> None:
    """RECON.md §4.2 — serwis nie dodaje żadnej etykiety tekstowej."""
    zakonczona = szczegoly("szczegoly-zakonczona-9ooxn4x9.html", "9ooxn4x9")
    assert zakonczona.pola["auction_pending"] == "false"
    assert (
        mapper.na_aukcje(zakonczona, source_id=1, teraz=TERAZ).status
        is AuctionStatus.ENDED
    )

    aktywna = szczegoly("szczegoly-9ooxn4x9.html", "9ooxn4x9")
    assert (
        mapper.na_aukcje(aktywna, source_id=1, teraz=TERAZ).status
        is AuctionStatus.ACTIVE
    )


def test_pozycja_z_listy_nie_jest_uznawana_za_zakonczona() -> None:
    """Brak markera to „nie wiem", a nie „zakończona".

    Lista nie niesie `auction_pending` w ogóle — gdyby jego brak znaczył
    koniec, cały przemiat oznaczałby aukcje jako zamknięte.
    """
    pozycje = parser.sparsuj_liste(html("lista-vehicles-01.html"))
    assert "auction_pending" not in pozycje[0].pola
    assert (
        mapper.na_aukcje(pozycje[0], source_id=1, teraz=TERAZ).status
        is AuctionStatus.ACTIVE
    )


def test_cena_po_zakonczeniu_nadal_jest_odczytywana() -> None:
    """RECON.md §3.6 — cena trzyma się po końcu bezterminowo.

    To ta właściwość sprawia, że faza 2 z §11.5 nie ma tu wyścigu z zegarem.
    """
    aukcja = mapper.na_aukcje(
        szczegoly("szczegoly-zakonczona-9ooxn4x9.html", "9ooxn4x9"),
        source_id=1,
        teraz=TERAZ,
    )
    assert aukcja.price_current == Money(Decimal("32100"), Currency.PLN)


def test_cala_lista_mapuje_sie_bez_wyjatku() -> None:
    """Jeden nietypowy kafelek nie ma prawa wywrócić całego przemiatu."""
    pozycje = parser.sparsuj_liste(html("lista-vehicles-01.html"))
    aukcje = [mapper.na_aukcje(p, source_id=1, teraz=TERAZ) for p in pozycje]
    assert len(aukcje) == len(pozycje)


def test_dogrywka_widoczna_w_przesunietym_end_date() -> None:
    """RECON.md §3.7 — aukcja 9mjrl4k9 przeszła z 12:00 na 12:18.

    Fixture z trwającej dogrywki ma już przesunięty `endDate` w bloku Alpine,
    więc parser czyta faktyczny termin, a nie nominalny.
    """
    w_dogrywce = szczegoly("szczegoly-9mjrl4k9-dogrywka.html", "9mjrl4k9")
    koniec = mapper.na_aukcje(w_dogrywce, source_id=1, teraz=TERAZ).ends_at
    assert koniec is not None
    assert koniec > dt.datetime(
        2026, 9, 7, 10, 0, tzinfo=dt.UTC
    ), "termin ma być PÓŹNIEJSZY niż nominalne 12:00 lokalnego czasu"


def test_zdjecia_pojazdu_odsiane_od_logotypow() -> None:
    """SPEC.md §12 — galeria pobierana na żądanie, nie trzymana w bazie.

    Serwis ma na stronie dziesiątki obrazów; zdjęcia pojazdu odróżnia
    przedrostek `sgallery_` w nazwie pliku.
    """
    adresy = parser.zdjecia(html("szczegoly-9ooxn4x9.html"))
    assert adresy, "fixture ma galerię — inaczej test nic nie waży"
    assert all("sgallery_" in u for u in adresy)
    assert all(u.startswith("https://poleasingowe.pl/") for u in adresy)
    assert len(adresy) == len(set(adresy)), "bez duplikatów"
