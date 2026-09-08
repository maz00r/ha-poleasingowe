"""Galeria zdjęć — proxy z cache na dysku (SPEC.md §12, §1.1)."""

from __future__ import annotations

import pathlib

from app.infrastructure.zdjecia import GaleriaZdjec


class ZrodloZeZdjeciami:
    key = "atrapa"

    def __init__(self, adresy: list[str]) -> None:
        self._adresy = adresy
        self.wywolania = 0

    async def zdjecia(self, external_id: str) -> list[str]:
        self.wywolania += 1
        return self._adresy


class ZrodloBezZdjec:
    key = "bez"


def galeria(adapter: object, tmp_path: pathlib.Path, **kw: object) -> GaleriaZdjec:
    return GaleriaZdjec(
        {"atrapa": adapter},  # type: ignore[dict-item]
        katalog=tmp_path / "zdjecia",
        **kw,  # type: ignore[arg-type]
    )


async def test_adresy_sa_cache_owane_w_pamieci(tmp_path: pathlib.Path) -> None:
    """Każde odświeżenie karty nie ma być nowym żądaniem strony źródła."""
    zrodlo = ZrodloZeZdjeciami(["https://x.test/1.jpg"])
    g = galeria(zrodlo, tmp_path)

    assert await g.adresy("atrapa", "a1") == ("https://x.test/1.jpg",)
    assert await g.adresy("atrapa", "a1") == ("https://x.test/1.jpg",)
    assert zrodlo.wywolania == 1


async def test_zrodlo_bez_galerii_zwraca_pustke_zamiast_bledu(
    tmp_path: pathlib.Path,
) -> None:
    """`zdjecia` jest osobnym protokołem — adapter bez galerii jest w porządku."""
    g = GaleriaZdjec({"bez": ZrodloBezZdjec()}, katalog=tmp_path)  # type: ignore[dict-item]
    assert await g.adresy("bez", "a1") == ()
    assert await g.obraz("bez", "a1", 0) is None


async def test_awaria_zrodla_nie_psuje_karty_aukcji(tmp_path: pathlib.Path) -> None:
    """Reszta danych o aukcji jest nadal użyteczna, a serwis bywa niedostępny."""

    class Padniete:
        key = "atrapa"

        async def zdjecia(self, external_id: str) -> list[str]:
            raise RuntimeError("serwis nie odpowiada")

    assert await galeria(Padniete(), tmp_path).adresy("atrapa", "a1") == ()


async def test_indeks_poza_zakresem_to_brak_zdjecia(tmp_path: pathlib.Path) -> None:
    """Trasa przyjmuje indeks, nie adres — to jest ta bariera.

    Gdyby przyjmowała adres, add-on byłby otwartym proxy: każdy, kto
    dosięgnie panelu, mógłby przez niego odpytywać sieć lokalną.
    """
    g = galeria(ZrodloZeZdjeciami(["https://x.test/1.jpg"]), tmp_path)
    assert await g.obraz("atrapa", "a1", 5) is None
    assert await g.obraz("atrapa", "a1", -1) is None


async def test_liczba_zdjec_ma_sufit(tmp_path: pathlib.Path) -> None:
    """Dwadzieścia wystarczy do obejrzenia auta; reszta to koszt bez pożytku."""
    zrodlo = ZrodloZeZdjeciami([f"https://x.test/{i}.jpg" for i in range(60)])
    assert len(await galeria(zrodlo, tmp_path).adresy("atrapa", "a1")) == 20


def test_nazwa_pliku_w_cache_nie_pochodzi_od_adresu(tmp_path: pathlib.Path) -> None:
    """Adres jest długi, ma znaki spoza dozwolonych i nie wolno mu sterować
    ścieżką — stąd hash zamiast przepisania adresu."""
    g = galeria(ZrodloZeZdjeciami([]), tmp_path)
    sciezka = g._sciezka_cache("https://x.test/../../etc/passwd?a=1.jpg")

    assert sciezka.parent == tmp_path / "zdjecia"
    assert ".." not in sciezka.name


def test_rotacja_trzyma_katalog_w_limicie(tmp_path: pathlib.Path) -> None:
    """SPEC.md §1.1 — `/data` ma budżet, więc cache ma sufit."""
    katalog = tmp_path / "zdjecia"
    g = GaleriaZdjec({}, katalog=katalog, limit_katalogu=3_000)
    for numer in range(6):
        g._zapisz(katalog / f"{numer}.jpg", b"x" * 1_000)

    laczny = sum(p.stat().st_size for p in katalog.iterdir())
    assert laczny <= 3_000
    assert (katalog / "5.jpg").exists(), "najnowszy plik ma przetrwać rotację"


def test_typ_mime_bierze_sie_z_rozszerzenia(tmp_path: pathlib.Path) -> None:
    g = galeria(ZrodloZeZdjeciami([]), tmp_path)
    assert g._sciezka_cache("https://x.test/a.png").suffix == ".png"
    assert g._sciezka_cache("https://x.test/a.webp").suffix == ".webp"
    assert g._sciezka_cache("https://x.test/a").suffix == ".jpg"
