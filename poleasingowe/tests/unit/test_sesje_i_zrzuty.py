"""Magazyn sesji, redakcja i zrzuty diagnostyczne (SPEC.md §10.2, §1.1)."""

from __future__ import annotations

import json
import pathlib
import stat

import pytest

from app.application.ports import Ciastko
from app.infrastructure.auth.sesje import (
    UPRAWNIENIA_KATALOGU,
    UPRAWNIENIA_PLIKU,
    PlikowyMagazynSesji,
    do_httpx,
    z_httpx,
)
from app.infrastructure.redakcja import MASKA, Redakcja
from app.infrastructure.zrzuty import ZrzutyDebug

CIASTKA = (
    Ciastko(nazwa="sesja", wartosc="abc123", domena="serwis.test", sciezka="/"),
    Ciastko(nazwa="XSRF-TOKEN", wartosc="xyz789", domena="serwis.test"),
)


def test_sesja_przezywa_zapis_i_odczyt(tmp_path: pathlib.Path) -> None:
    magazyn = PlikowyMagazynSesji(tmp_path)
    magazyn.zapisz("efl", CIASTKA)
    assert magazyn.wczytaj("efl") == CIASTKA


def test_plik_sesji_ma_uprawnienia_0600(tmp_path: pathlib.Path) -> None:
    """SPEC.md §10.2 — plik z ciasteczkami JEST poświadczeniem.

    Kto go ma, ten jest zalogowany na moje konto.
    """
    katalog = tmp_path / "sessions"
    magazyn = PlikowyMagazynSesji(katalog)
    magazyn.zapisz("efl", CIASTKA)

    plik = katalog / "efl.json"
    assert stat.S_IMODE(plik.stat().st_mode) == UPRAWNIENIA_PLIKU
    assert stat.S_IMODE(katalog.stat().st_mode) == UPRAWNIENIA_KATALOGU


def test_brak_pliku_to_brak_sesji_a_nie_wyjatek(tmp_path: pathlib.Path) -> None:
    assert PlikowyMagazynSesji(tmp_path).wczytaj("nigdy-nie-zapisane") == ()


def test_uszkodzony_plik_konczy_sie_ponownym_logowaniem(
    tmp_path: pathlib.Path,
) -> None:
    """Zepsuty JSON to jedno logowanie, nie pętla restartów (SPEC.md §2 pkt 8)."""
    (tmp_path / "efl.json").write_text("{ to nie jest json", encoding="utf-8")
    assert PlikowyMagazynSesji(tmp_path).wczytaj("efl") == ()


def test_zapis_jest_atomowy_i_nie_zostawia_smieci(tmp_path: pathlib.Path) -> None:
    """Obcięty JSON wygląda jak sesja, a nią nie jest."""
    magazyn = PlikowyMagazynSesji(tmp_path)
    magazyn.zapisz("efl", CIASTKA)
    magazyn.zapisz("efl", CIASTKA[:1])

    assert list(tmp_path.glob("*.tmp")) == []
    assert len(magazyn.wczytaj("efl")) == 1


def test_usuniecie_sesji_jest_idempotentne(tmp_path: pathlib.Path) -> None:
    magazyn = PlikowyMagazynSesji(tmp_path)
    magazyn.zapisz("efl", CIASTKA)
    magazyn.usun("efl")
    magazyn.usun("efl")
    assert magazyn.wczytaj("efl") == ()


def test_klucz_zrodla_nie_wyprowadza_poza_katalog(tmp_path: pathlib.Path) -> None:
    """Klucze pochodzą z opcji add-onu, czyli spoza kodu."""
    magazyn = PlikowyMagazynSesji(tmp_path / "sessions")
    magazyn.zapisz("../../etc/passwd", CIASTKA)

    assert not (tmp_path / "etc").exists()
    assert [p.name for p in (tmp_path / "sessions").glob("*.json")] == [
        "etcpasswd.json"
    ]


def test_pusty_klucz_jest_bledem(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="nazwę pliku"):
        PlikowyMagazynSesji(tmp_path).wczytaj("///")


def test_ciastko_nie_pokazuje_wartosci_w_repr() -> None:
    """SPEC.md §10.2 — wartość ciasteczka sesji to poświadczenie."""
    ciastko = Ciastko(nazwa="sesja", wartosc="bardzo-tajna-wartosc")
    assert "bardzo-tajna-wartosc" not in repr(ciastko)
    assert "bardzo-tajna-wartosc" not in str(ciastko)
    assert "sesja" in repr(ciastko), "nazwa ma zostać — po logu ma być co poznać"


def test_ciastka_przezywaja_obieg_przez_httpx() -> None:
    """Magazyn mówi `Ciastko`, klient HTTP mówi `httpx.Cookies`."""
    odtworzone = z_httpx(do_httpx(CIASTKA))
    assert {(c.nazwa, c.wartosc) for c in odtworzone} == {
        (c.nazwa, c.wartosc) for c in CIASTKA
    }


def test_plik_sesji_zawiera_to_co_zapisano(tmp_path: pathlib.Path) -> None:
    """Format ma być czytelny — plik sesji bywa oglądany przy diagnozie."""
    PlikowyMagazynSesji(tmp_path).zapisz("efl", CIASTKA[:1])
    dane = json.loads((tmp_path / "efl.json").read_text(encoding="utf-8"))
    assert dane == [
        {
            "nazwa": "sesja",
            "wartosc": "abc123",
            "domena": "serwis.test",
            "sciezka": "/",
            "wygasa": None,
        }
    ]


# --------------------------------------------------------------------------
# Zrzuty diagnostyczne
# --------------------------------------------------------------------------

STRONA = (
    "<html><body>Zalogowany: jan.kowalski"
    '<input name="__RequestVerificationToken" value="TAJNY-TOKEN">'
    "<script>var haslo = 'moje-haslo';</script></body></html>"
)


def test_zrzuty_sa_domyslnie_wylaczone(tmp_path: pathlib.Path) -> None:
    """SPEC.md §10.2 — strona po zalogowaniu zawiera moje dane osobowe."""
    zrzuty = ZrzutyDebug(Redakcja(), katalog=tmp_path)
    assert zrzuty.wlaczone is False
    assert zrzuty.zapisz("efl", "435508", STRONA, "błąd parsowania") is None
    assert list(tmp_path.glob("*")) == []


def test_zrzut_przechodzi_przez_ten_sam_filtr_co_logi(tmp_path: pathlib.Path) -> None:
    """SPEC.md §10.2 mówi wprost: przez ten sam filtr."""
    zrzuty = ZrzutyDebug(Redakcja(["moje-haslo"]), wlaczone=True, katalog=tmp_path)
    sciezka = zrzuty.zapisz("efl", "435508", STRONA, "błąd parsowania")

    assert sciezka is not None
    tresc = sciezka.read_text(encoding="utf-8")
    assert "TAJNY-TOKEN" not in tresc
    assert "moje-haslo" not in tresc
    assert MASKA in tresc
    assert "błąd parsowania" in tresc, "powód ma zostać — po to jest zrzut"


def test_zrzut_ma_limit_rozmiaru_pliku(tmp_path: pathlib.Path) -> None:
    zrzuty = ZrzutyDebug(Redakcja(), wlaczone=True, katalog=tmp_path, limit_pliku=1024)
    sciezka = zrzuty.zapisz("efl", "1", "x" * 100_000, "za duże")
    assert sciezka is not None
    assert sciezka.stat().st_size <= 1024


def test_rotacja_kasuje_najstarsze_zrzuty(tmp_path: pathlib.Path) -> None:
    """SPEC.md §1.1 — katalog debug w `/data` ma się mieścić w limicie.

    Kasujemy od najstarszych: diagnozuje się to, co zepsuło się teraz.
    """
    zrzuty = ZrzutyDebug(
        Redakcja(), wlaczone=True, katalog=tmp_path, limit_katalogu=3_000
    )
    for numer in range(6):
        zrzuty.zapisz("efl", f"aukcja{numer}", "y" * 1_000, "błąd")

    pozostale = sorted(p.name for p in tmp_path.glob("*.html"))
    laczny = sum(p.stat().st_size for p in tmp_path.glob("*.html"))
    assert laczny <= 3_000
    assert "aukcja5" in pozostale[-1], "najnowszy zrzut ma przetrwać rotację"
    assert not any("aukcja0" in n for n in pozostale)


def test_blad_zapisu_nie_przerywa_odpytu(tmp_path: pathlib.Path) -> None:
    """Zrzut to pomoc w diagnozie, nie zadanie add-onu."""
    plik_zamiast_katalogu = tmp_path / "debug"
    plik_zamiast_katalogu.write_text("nie jestem katalogiem", encoding="utf-8")

    zrzuty = ZrzutyDebug(Redakcja(), wlaczone=True, katalog=plik_zamiast_katalogu)
    assert zrzuty.zapisz("efl", "1", STRONA, "błąd") is None


def test_identyfikator_nie_wyprowadza_poza_katalog(tmp_path: pathlib.Path) -> None:
    """`external_id` pochodzi z serwisu, więc jest danymi, nie nazwą pliku."""
    zrzuty = ZrzutyDebug(Redakcja(), wlaczone=True, katalog=tmp_path / "debug")
    sciezka = zrzuty.zapisz("efl", "../../../etc/passwd", STRONA, "błąd")

    assert sciezka is not None
    assert sciezka.parent == tmp_path / "debug"
    assert not (tmp_path / "etc").exists()
