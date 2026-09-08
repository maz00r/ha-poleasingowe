"""Zdjęcia pojazdów — proxy z cache na dysku (SPEC.md §12).

„Bez miniatur zdjęć na start. Jeśli kiedyś — **proxy z cache na dysku
i twardym limitem, nie hotlink**." To jest to „kiedyś".

Trzy decyzje wynikają wprost z tego zdania i z prośby, żeby nie trzymać
zdjęć w bazie:

- **Adresy nie idą do bazy.** Wyciągamy je ze strony aukcji dopiero wtedy,
  gdy ktoś otworzy jej kartę. Kosztuje to jedno żądanie na obejrzaną aukcję,
  a nie jedno na każdą zebraną.
- **Nie hotlinkujemy.** Przeglądarka pobiera obraz z add-onu, nie z serwisu
  aukcyjnego. Inaczej każde otwarcie panelu byłoby ruchem widocznym dla
  serwisu, a Ingress i tak nie wypuściłby żądań na zewnątrz.
- **Twardy limit katalogu.** Bajty na dysku to §1.1, więc cache ma sufit
  i kasuje najstarsze pliki, zamiast rosnąć bez końca.

Adres obrazu **nigdy nie pochodzi od przeglądarki** — trasa przyjmuje numer
aukcji i indeks, a URL wybiera add-on z listy, którą sam wyczytał ze strony
źródła. Gdyby przyjmowała adres, byłaby otwartym proxy: każdy, kto dosięgnie
panelu, mógłby przez add-on odpytywać dowolny adres w sieci lokalnej.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import pathlib
import tempfile
import time
from collections.abc import Mapping, Sequence

import httpx

from app.application.ports import AuctionSource

log = logging.getLogger(__name__)

KATALOG_CACHE = pathlib.Path("/data/cache/zdjecia")
LIMIT_KATALOGU_BAJTY = 100 * 1024 * 1024
"""SPEC.md §1.1 — `/data` ma budżet, więc cache ma sufit."""
LIMIT_PLIKU_BAJTY = 4 * 1024 * 1024
MAKS_ZDJEC = 20
"""Tyle wystarczy do obejrzenia auta; reszta galerii to koszt bez pożytku."""
WAZNOSC_LISTY_S = 900.0
"""Jak długo pamiętamy adresy galerii. Zdjęcia nie zmieniają się w trakcie
trwania aukcji, a bez tego każde odświeżenie karty to nowe żądanie strony."""

TYPY = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class GaleriaZdjec:
    """Pobiera i cache'uje zdjęcia aukcji."""

    def __init__(
        self,
        adaptery: Mapping[str, AuctionSource],
        *,
        katalog: pathlib.Path = KATALOG_CACHE,
        limit_katalogu: int = LIMIT_KATALOGU_BAJTY,
        limit_pliku: int = LIMIT_PLIKU_BAJTY,
        timeout: float = 20.0,
    ) -> None:
        self._adaptery = adaptery
        self._katalog = katalog
        self._limit_katalogu = limit_katalogu
        self._limit_pliku = limit_pliku
        self._timeout = timeout
        self._listy: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
        self._klient: httpx.AsyncClient | None = None

    async def _klient_http(self) -> httpx.AsyncClient:
        if self._klient is None:
            self._klient = httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            )
        return self._klient

    async def zamknij(self) -> None:
        if self._klient is not None:
            await self._klient.aclose()
            self._klient = None

    async def adresy(self, source_key: str, external_id: str) -> tuple[str, ...]:
        """Adresy zdjęć aukcji. Pusta krotka, gdy źródło ich nie udostępnia."""
        klucz = (source_key, external_id)
        wpis = self._listy.get(klucz)
        if wpis is not None and time.monotonic() - wpis[0] < WAZNOSC_LISTY_S:
            return wpis[1]

        adapter = self._adaptery.get(source_key)
        pobierz = getattr(adapter, "zdjecia", None)
        if adapter is None or pobierz is None:
            return ()

        try:
            adresy = tuple((await pobierz(external_id))[:MAKS_ZDJEC])
        except Exception as exc:
            # Brak zdjęć nie ma prawa zepsuć karty aukcji — reszta danych
            # jest nadal użyteczna, a serwis bywa chwilowo niedostępny.
            log.info(
                "nie udało się pobrać galerii %s/%s: %s", source_key, external_id, exc
            )
            return ()

        self._listy[klucz] = (time.monotonic(), adresy)
        return adresy

    async def obraz(
        self, source_key: str, external_id: str, indeks: int
    ) -> tuple[bytes, str] | None:
        """Bajty zdjęcia i jego typ MIME. `None`, gdy takiego zdjęcia nie ma.

        Indeks, nie adres — patrz uwaga o otwartym proxy w opisie modułu.
        """
        adresy = await self.adresy(source_key, external_id)
        if not 0 <= indeks < len(adresy):
            return None
        return await self._z_cache_lub_sieci(adresy[indeks])

    async def _z_cache_lub_sieci(self, url: str) -> tuple[bytes, str] | None:
        sciezka = self._sciezka_cache(url)
        typ = TYPY.get(sciezka.suffix, "application/octet-stream")
        try:
            return sciezka.read_bytes(), typ
        except OSError:
            pass

        try:
            klient = await self._klient_http()
            odpowiedz = await klient.get(url)
            odpowiedz.raise_for_status()
        except httpx.HTTPError as exc:
            log.info("nie udało się pobrać zdjęcia %s: %s", url, exc)
            return None

        dane = odpowiedz.content
        if len(dane) > self._limit_pliku:
            log.info("zdjęcie %s ma %s B — ponad limit, nie cache'uję", url, len(dane))
            return dane, typ

        await asyncio.to_thread(self._zapisz, sciezka, dane)
        return dane, typ

    def _sciezka_cache(self, url: str) -> pathlib.Path:
        # Nazwa z hasha adresu: adresy bywają długie, mają znaki spoza
        # dozwolonych w nazwie pliku i nie wolno im sterować ścieżką.
        odcisk = hashlib.sha256(url.encode("utf-8")).hexdigest()
        rozszerzenie = next(
            (r for r in TYPY if url.lower().split("?")[0].endswith(r)), ".jpg"
        )
        return self._katalog / f"{odcisk}{rozszerzenie}"

    def _zapisz(self, sciezka: pathlib.Path, dane: bytes) -> None:
        try:
            self._katalog.mkdir(parents=True, exist_ok=True)
            uchwyt, tymczasowy = tempfile.mkstemp(dir=self._katalog, suffix=".tmp")
            try:
                with os.fdopen(uchwyt, "wb") as plik:
                    plik.write(dane)
                pathlib.Path(tymczasowy).replace(sciezka)
            except BaseException:
                pathlib.Path(tymczasowy).unlink(missing_ok=True)
                raise
            self._obetnij()
        except OSError as exc:
            # Pełny dysk ma skończyć się brakiem cache'u, nie brakiem zdjęcia.
            log.warning("nie udało się zapisać zdjęcia do cache'u: %s", exc)

    def _obetnij(self) -> None:
        """Kasuje najstarsze pliki, aż katalog zmieści się w limicie (§1.1)."""
        pliki = sorted(
            (p for p in self._katalog.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        laczny = sum(p.stat().st_size for p in pliki)
        for plik in pliki:
            if laczny <= self._limit_katalogu:
                return
            rozmiar = plik.stat().st_size
            plik.unlink(missing_ok=True)
            laczny -= rozmiar


def zdjecia_wspierane(adaptery: Mapping[str, AuctionSource]) -> Sequence[str]:
    """Klucze źródeł, które w ogóle udostępniają galerię."""
    return [k for k, a in adaptery.items() if hasattr(a, "zdjecia")]
