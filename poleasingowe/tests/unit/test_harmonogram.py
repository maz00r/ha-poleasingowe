"""Tabela progów harmonogramu (SPEC.md §11.2, §13).

`PollingPolicy` jest czystą funkcją właśnie po to, żeby dało się ją sprawdzić
tabelą, a nie obserwacją działającego add-onu. Pełna tabela progów plus
przypadki brzegowe z §13: dogrywka, `ends_at` w przeszłości, brak `ends_at`.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain.entities import Auction, Source
from app.domain.enums import AuctionStatus, AuthState, PollTier
from app.domain.harmonogram import (
    INTERWAL_DALEKI_S,
    MINIMALNY_FLOOR_S,
    floor_zrodla,
    interwal,
    nastepny_odpyt,
    tier,
)

TERAZ = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.UTC)

MINUTA = 60
GODZINA = 3600
DZIEN = 86_400


def zrodlo(**nadpisz: object) -> Source:
    dane: dict[str, object] = {
        "key": "test",
        "name": "Test",
        "enabled": True,
        "sweep_interval_seconds": 6 * GODZINA,
        "rate_limit_per_minute": 30,
        "floor_seconds": MINIMALNY_FLOOR_S,
        "auth_state": AuthState.ANONYMOUS,
        "consecutive_auth_failures": 0,
        "overtime_window_seconds": 0,
        "overtime_extension_seconds": 0,
        "overtime_cap_seconds": None,
    }
    dane.update(nadpisz)
    return Source(**dane)  # type: ignore[arg-type]


def aukcja(do_konca_s: float | None, **nadpisz: object) -> Auction:
    dane: dict[str, object] = {
        "source_id": 1,
        "external_id": "x1",
        "url": "https://przyklad.test/x1",
        "status": AuctionStatus.ACTIVE,
        "first_seen_at": TERAZ - dt.timedelta(days=1),
        "last_seen_at": TERAZ,
        "ends_at": None
        if do_konca_s is None
        else TERAZ + dt.timedelta(seconds=do_konca_s),
    }
    dane.update(nadpisz)
    return Auction(**dane)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Pełna tabela progów z SPEC.md §11.2
# --------------------------------------------------------------------------

POLEASINGOWE = zrodlo(
    key="poleasingowe", overtime_window_seconds=30, overtime_extension_seconds=30
)


@pytest.mark.parametrize(
    "do_konca_s,oczekiwany_interwal_s,opis",
    [
        (8 * DZIEN, 24 * GODZINA, "> 7 dni"),
        (7 * DZIEN + 1, 24 * GODZINA, "tuż nad progiem 7 dni"),
        (7 * DZIEN, 6 * GODZINA, "dokładnie 7 dni — próg należy do niższego"),
        (3 * DZIEN, 6 * GODZINA, "1-7 dni"),
        (24 * GODZINA + 1, 6 * GODZINA, "tuż nad progiem doby"),
        (24 * GODZINA, GODZINA, "dokładnie doba"),
        (12 * GODZINA, GODZINA, "6-24 h"),
        (6 * GODZINA + 1, GODZINA, "tuż nad progiem 6 h"),
        (6 * GODZINA, 15 * MINUTA, "dokładnie 6 h"),
        (2 * GODZINA, 15 * MINUTA, "1-6 h"),
        (60 * MINUTA + 1, 15 * MINUTA, "tuż nad godziną"),
        (60 * MINUTA, 3 * MINUTA, "dokładnie godzina"),
        (30 * MINUTA, 3 * MINUTA, "15-60 min"),
        (15 * MINUTA + 1, 3 * MINUTA, "tuż nad kwadransem"),
        (15 * MINUTA, 15, "dokładnie kwadrans — już floor"),
        (60, 15, "minuta do końca — floor"),
        (1, 15, "sekunda do końca — floor"),
    ],
)
def test_tabela_progow(do_konca_s: int, oczekiwany_interwal_s: int, opis: str) -> None:
    """SPEC.md §11.2, wiersz po wierszu, razem z granicami.

    Granice testujemy z obu stron, bo „1-7 dni" i „> 7 dni" różnią się
    o jedną sekundę, a pomyłka o jeden przesuwa cały odpyt o 18 godzin.
    """
    wynik = interwal(aukcja(do_konca_s), POLEASINGOWE, TERAZ)
    assert wynik == oczekiwany_interwal_s, opis


@pytest.mark.parametrize(
    "do_konca_s,oczekiwany",
    [
        (10 * DZIEN, PollTier.FAR),
        (12 * GODZINA, PollTier.FAR),
        (6 * GODZINA, PollTier.NEAR),
        (20 * MINUTA, PollTier.NEAR),
        (15 * MINUTA, PollTier.ENDGAME),
        (30, PollTier.ENDGAME),
        (0, PollTier.CLOSING),
        (-60, PollTier.CLOSING),
    ],
)
def test_kubelki(do_konca_s: int, oczekiwany: PollTier) -> None:
    assert tier(aukcja(do_konca_s), TERAZ) is oczekiwany


# --------------------------------------------------------------------------
# Floor z reguły, nie ze stałej (SPEC.md §11.2)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "serwis,okno_s,oczekiwany_floor_s",
    [
        ("poleasingowe.pl", 30, 15),
        ("aukcje.leasygroup.pl", 120, 60),
        ("autoprzetarg.pl", 120, 60),
    ],
)
def test_floor_to_polowa_okna_dogrywki(
    serwis: str, okno_s: int, oczekiwany_floor_s: int
) -> None:
    """Wartości wprost z tabeli w §11.2, wyliczone z okien z RECON.md §3.2.

    Reguła: dwie próbki mają się zmieścić w oknie dogrywki. Jedna nie
    gwarantuje wykrycia przedłużenia, zanim aukcja się domknie.
    """
    zr = zrodlo(
        key=serwis, overtime_window_seconds=okno_s, overtime_extension_seconds=okno_s
    )
    assert floor_zrodla(zr) == oczekiwany_floor_s, serwis


def test_floor_nigdy_ponizej_dziesieciu_sekund() -> None:
    """SPEC.md §11.2 — twarda granica, niezależna od reguły.

    Serwis z ośmiosekundowym oknem dałby z reguły 4 s, a tak nisko nie
    schodzimy nawet przy udowodnionym limicie tempa.
    """
    ciasne = zrodlo(overtime_window_seconds=8, overtime_extension_seconds=8)
    assert floor_zrodla(ciasne) == MINIMALNY_FLOOR_S


def test_floor_wolno_podniesc_per_zrodlo_ale_nie_obnizyc() -> None:
    """RECON.md §4.3 — dla serwisu za zaporą aplikacyjną podnosimy floor.

    Konfiguracja może być tylko **ostrożniejsza** od reguły: obniżenie
    poniżej niej wymagałoby dowodu z nagłówków limitu tempa, więc reguła
    jest tu podłogą.
    """
    ostrozne = zrodlo(
        overtime_window_seconds=120, overtime_extension_seconds=120, floor_seconds=300
    )
    assert floor_zrodla(ostrozne) == 300

    zbyt_smiale = zrodlo(
        overtime_window_seconds=120, overtime_extension_seconds=120, floor_seconds=20
    )
    assert floor_zrodla(zbyt_smiale) == 60, "reguła wygrywa z niższą konfiguracją"


def test_zrodlo_bez_dogrywki_nie_dostaje_floora_z_reguly() -> None:
    """EFL nie ma dogrywki (RECON.md §3.2), więc `ends_at` jest twardy.

    Nie ma czego łapać gęstszym odpytem — zostaje wartość z konfiguracji.
    """
    efl = zrodlo(key="efl", floor_seconds=60)
    assert not efl.ma_dogrywke
    assert floor_zrodla(efl) == 60


# --------------------------------------------------------------------------
# Przypadki brzegowe wymagane przez SPEC.md §13
# --------------------------------------------------------------------------


def test_brak_ends_at_nie_wywala_polityki() -> None:
    """poleasingowe.pl nie podaje daty końca na liście w żadnej postaci
    (RECON.md §4.2), więc to nie jest przypadek teoretyczny."""
    bez_terminu = aukcja(None)
    assert tier(bez_terminu, TERAZ) is PollTier.FAR
    assert interwal(bez_terminu, POLEASINGOWE, TERAZ) == INTERWAL_DALEKI_S


def test_ends_at_w_przeszlosci_daje_floor_a_nie_ujemny_odstep() -> None:
    """Po terminie decyduje domknięcie z §11.5, nie tabela progów.

    Naiwne odjęcie dałoby tu wartość ujemną i odpyt „w przeszłości", czyli
    pętlę bez przerwy.
    """
    po_terminie = aukcja(-3600)
    assert interwal(po_terminie, POLEASINGOWE, TERAZ) == floor_zrodla(POLEASINGOWE)
    assert nastepny_odpyt(po_terminie, POLEASINGOWE, TERAZ) > TERAZ


def test_dogrywka_przesuwa_odpyt_bo_ends_at_odczytujemy_na_nowo() -> None:
    """SPEC.md §11.2 — w endgame `ends_at` z bazy jest wskazówką, nie prawdą.

    Aukcja `9mjrl4k9` przeszła z 12:00 na 12:18 (RECON.md §3.7). Polityka ma
    liczyć od nowego terminu, a nie trzymać się starego.
    """
    from dataclasses import replace

    przed = aukcja(30)
    assert interwal(przed, POLEASINGOWE, TERAZ) == 15

    po_przedluzeniu = replace(przed, ends_at=TERAZ + dt.timedelta(minutes=18))
    assert interwal(po_przedluzeniu, POLEASINGOWE, TERAZ) == 3 * MINUTA


@pytest.mark.parametrize(
    "status", [AuctionStatus.ENDED, AuctionStatus.DISAPPEARED, AuctionStatus.ENDING]
)
def test_nieaktywna_aukcja_nie_jest_odpytywana_pojedynczo(
    status: AuctionStatus,
) -> None:
    assert tier(aukcja(3600, status=status), TERAZ) is PollTier.IDLE


def test_nastepny_odpyt_jest_zawsze_w_przyszlosci() -> None:
    """Odpyt wyznaczony w przeszłości to pętla bez przerwy — a to jedyna
    rzecz, która na współdzielonym Postgresie boli natychmiast (§2)."""
    for do_konca in (-86_400, -1, 0, 1, 60, 3600, 10 * DZIEN, None):
        wynik = nastepny_odpyt(aukcja(do_konca), POLEASINGOWE, TERAZ)
        assert wynik > TERAZ, f"do_konca={do_konca}"


def test_polityka_nie_siega_po_zegar_systemowy() -> None:
    """SPEC.md §11.2 — czysta funkcja, zegar wchodzi argumentem.

    Bez tego harmonogram byłby nietestowalny, a `PollingPolicy` przestałaby
    być czystą funkcją.
    """
    import ast
    import inspect

    from app.domain import harmonogram

    # Po AST, nie po tekście: docstring tego modułu sam cytuje regułę
    # „bez `datetime.now()` w środku", więc wyszukiwanie w źródle łapałoby
    # własny komentarz zamiast wywołania.
    drzewo = ast.parse(inspect.getsource(harmonogram))
    wywolania = {
        ast.unparse(w.func) for w in ast.walk(drzewo) if isinstance(w, ast.Call)
    }
    zakazane = {w for w in wywolania if w.endswith((".now", ".today", ".time"))}
    assert not zakazane, f"polityka sięga po zegar systemowy: {sorted(zakazane)}"
