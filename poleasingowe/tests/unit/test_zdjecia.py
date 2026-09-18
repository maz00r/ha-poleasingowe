"""Galeria zdjęć — proxy z cache na dysku (SPEC.md §12, §1.1)."""

from __future__ import annotations

import io
import pathlib
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from PIL import Image

from app.application.ports import FabrykaKontekstu
from app.domain.entities import ArchivedPhoto
from app.domain.enums import PhotoArchiveTarget
from app.infrastructure.zdjecia import BrakMiejscaArchiwum, GaleriaZdjec


class ZrodloZeZdjeciami:
    key = "atrapa"

    def __init__(self, adresy: list[str]) -> None:
        self._adresy = adresy
        self.wywolania = 0

    async def zdjecia(self, external_id: str, url: str | None = None) -> list[str]:
        self.wywolania += 1
        self.ostatni_url = url
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


async def test_klient_galerii_uzywa_systemowego_magazynu_ca(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Galeria musi zauważyć certyfikat pośredni Leasygroup z obrazu add-onu."""
    kontekst = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    wywolania: list[object] = []

    class Klient:
        def __init__(self, **kw: object) -> None:
            wywolania.append(kw["verify"])

    # Patchujemy `ssl`/`httpx` pod ich WŁASNYM importem w tym pliku, nie pod
    # `zdjecia.ssl`/`zdjecia.httpx` — moduł jest tym samym obiektem w
    # sys.modules, więc podmiana działa identycznie, a mypy --strict nie
    # uznaje `import ssl` w zdjecia.py za świadomy re-eksport (przez co
    # `zdjecia.ssl` jako atrybut jest błędem pod --no-implicit-reexport).
    monkeypatch.setattr(ssl, "create_default_context", lambda: kontekst)
    monkeypatch.setattr(httpx, "AsyncClient", Klient)

    await galeria(ZrodloZeZdjeciami([]), tmp_path)._klient_http()
    assert wywolania == [kontekst]


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

        async def zdjecia(self, external_id: str, url: str | None = None) -> list[str]:
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


async def test_galeria_zwraca_wszystkie_zdjecia(tmp_path: pathlib.Path) -> None:
    """Karta szczegółów ma pokazać całą dokumentację fotograficzną."""
    zrodlo = ZrodloZeZdjeciami([f"https://x.test/{i}.jpg" for i in range(60)])
    assert len(await galeria(zrodlo, tmp_path).adresy("atrapa", "a1")) == 60


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


def test_zapisz_pomija_plik_ktory_juz_istnieje(tmp_path: pathlib.Path) -> None:
    """Miniatura na liście i galeria szczegółów proszą o to samo zdjęcie
    (indeks 0) niemal równocześnie — dwa wątki `_zapisz` na ten sam plik.

    Kto pierwszy, ten lepszy: drugi zapis ma być cichym no-opem, nie
    nadpisywać efektu pierwszego ani zgłaszać błędu.
    """
    katalog = tmp_path / "zdjecia"
    g = GaleriaZdjec({}, katalog=katalog)
    cel = katalog / "0.jpg"

    g._zapisz(cel, b"pierwszy zapis")
    g._zapisz(cel, b"drugi, spozniony zapis")

    assert cel.read_bytes() == b"pierwszy zapis"


def test_typ_mime_bierze_sie_z_rozszerzenia(tmp_path: pathlib.Path) -> None:
    g = galeria(ZrodloZeZdjeciami([]), tmp_path)
    assert g._sciezka_cache("https://x.test/a.png").suffix == ".png"
    assert g._sciezka_cache("https://x.test/a.webp").suffix == ".webp"
    assert g._sciezka_cache("https://x.test/a").suffix == ".jpg"


def test_miniatura_jest_faktycznie_malym_obrazem_jpeg(
    tmp_path: pathlib.Path,
) -> None:
    wejscie = io.BytesIO()
    Image.new("RGB", (2400, 1600), "#56789a").save(wejscie, format="PNG")

    wynik = GaleriaZdjec._pomniejsz(wejscie.getvalue())

    with Image.open(io.BytesIO(wynik)) as obraz:
        assert obraz.size == (240, 160)
        assert obraz.format == "JPEG"
    assert len(wynik) < len(wejscie.getvalue())


def test_archiwum_zachowuje_proporcje_i_nie_powieksza() -> None:
    duzy = io.BytesIO()
    Image.new("RGB", (1600, 1200), "#56789a").save(duzy, format="PNG")
    dane, szerokosc, wysokosc = GaleriaZdjec._pomniejsz_archiwalny(
        duzy.getvalue(), (800, 600)
    )
    assert (szerokosc, wysokosc) == (800, 600)

    maly = io.BytesIO()
    Image.new("RGB", (320, 200), "#345678").save(maly, format="PNG")
    wynik, szerokosc, wysokosc = GaleriaZdjec._pomniejsz_archiwalny(
        maly.getvalue(), (1280, 1280)
    )
    assert (szerokosc, wysokosc) == (320, 200)
    with Image.open(io.BytesIO(wynik)) as obraz:
        assert obraz.format == "JPEG"
        assert not obraz.getexif(), "archiwum nie może zachowywać EXIF"
    assert dane


def test_archiwum_stosuje_orientacje_exif() -> None:
    wejscie = io.BytesIO()
    obraz = Image.new("RGB", (1200, 800), "#123456")
    exif = Image.Exif()
    exif[274] = 6  # obrót o 90 stopni w prawo
    obraz.save(wejscie, format="JPEG", exif=exif)

    wynik, szerokosc, wysokosc = GaleriaZdjec._pomniejsz_archiwalny(
        wejscie.getvalue(), (800, 600)
    )

    assert (szerokosc, wysokosc) == (400, 600)
    with Image.open(io.BytesIO(wynik)) as zapisany:
        assert not zapisany.getexif()


def test_rezerwa_dysku_nie_nadpisuje_istniejacego_archiwum(
    tmp_path: pathlib.Path,
) -> None:
    g = GaleriaZdjec(
        {},
        katalog=tmp_path / "cache",
        katalog_archiwum=tmp_path / "archiwum",
        min_wolne_bajty=10**30,
    )
    cel = g._sciezka_archiwum(7, 0)
    cel.parent.mkdir(parents=True)
    cel.write_bytes(b"poprzednie")

    with pytest.raises(BrakMiejscaArchiwum):
        g._zapisz_trwale(cel, b"nowe")

    assert cel.read_bytes() == b"poprzednie"


async def test_archiwalne_zdjecie_dziala_bez_adaptera_zrodla(
    tmp_path: pathlib.Path,
) -> None:
    wejscie = io.BytesIO()
    Image.new("RGB", (800, 600), "#56789a").save(wejscie, format="JPEG")
    dane = wejscie.getvalue()
    metadane = ArchivedPhoto(
        auction_id=7,
        position=0,
        target=PhotoArchiveTarget.COVER,
        width=800,
        height=600,
        byte_size=len(dane),
        sha256="a" * 64,
        archived_at=datetime(2026, 9, 18, tzinfo=UTC),
    )

    class Repo:
        async def dla_aukcji(self, auction_id: int) -> tuple[ArchivedPhoto, ...]:
            assert auction_id == 7
            return (metadane,)

    class Uow:
        photo_archive = Repo()

        async def __aenter__(self) -> Uow:
            return self

        async def __aexit__(self, *wyjatek: object) -> None:
            return None

    class Fabryka:
        @asynccontextmanager
        async def __call__(self) -> AsyncIterator[Any]:
            yield SimpleNamespace(uow=Uow())

    g = GaleriaZdjec(
        {},
        katalog=tmp_path / "cache",
        katalog_archiwum=tmp_path / "archiwum",
        fabryka=cast(FabrykaKontekstu, Fabryka()),
        min_wolne_bajty=0,
    )
    cel = g._sciezka_archiwum(7, 0)
    cel.parent.mkdir(parents=True)
    cel.write_bytes(dane)

    wynik = await g.obraz_http("nieistniejace", "x", 0, auction_id=7)
    assert wynik == (dane, "image/jpeg", "a" * 64)

    miniatura = await g.obraz_http(
        "nieistniejace", "x", 0, auction_id=7, miniatura=True
    )
    assert miniatura is not None
    with Image.open(io.BytesIO(miniatura[0])) as obraz_miniatury:
        assert obraz_miniatury.size == (240, 160)


async def test_adres_aukcji_z_bazy_trafia_do_adaptera(
    tmp_path: pathlib.Path,
) -> None:
    """Adapter ma dostać adres, który serwis podał nam sam.

    To jest naprawa braku zdjęć z EFL: adapter składał `/Auction/x-id<id>`
    z założenia o routingu, którego rekonesans nigdy nie potwierdził, a błąd
    pobrania galerii jest połykany — więc jedynym objawem było puste miejsce
    na zdjęcia (`sources/adresy.py`).
    """
    zrodlo = ZrodloZeZdjeciami(["https://x.test/1.jpg"])
    await galeria(zrodlo, tmp_path).adresy(
        "atrapa", "a1", "https://aukcje.efl.com.pl/Auction/Audi-A4-...-id435663"
    )
    assert (
        zrodlo.ostatni_url == "https://aukcje.efl.com.pl/Auction/Audi-A4-...-id435663"
    )
