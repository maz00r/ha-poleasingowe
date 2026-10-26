"""Dokumenty aukcji — ekspertyzy PDF przez proxy add-onu (0.31.0).

Zasady jak przy zdjęciach (`zdjecia.py`):

- **Nie hotlinkujemy.** Przeglądarka pobiera plik z add-onu, a add-on — ze
  źródła, przez tę samą bramkę (blokada + kubełek tokenów) co dispatcher.
- **Adres nigdy nie pochodzi od przeglądarki.** Trasa przyjmuje numer aukcji
  i indeks na liście, którą add-on sam odczytał ze strony źródła; adapter
  dodatkowo ogranicza adres do hostów serwisu (`sources/pliki.py`).
- **Archiwum na żądanie.** Dokument zapisuje się na dysk dopiero po kliknięciu
  „Zapisz w archiwum" — ekspertyzy ważą po kilka MB, a strona aukcji bywa
  usuwana kilka godzin po końcu (dawro: `302` na `/`). Zarchiwizowany plik
  zostaje dostępny, gdy źródło go już nie ma.

Archiwum to katalog `/data/archiwum-dokumentow/<auction_id>/`: plik
`<id>.pdf` i obok `<id>.json` z nazwą i adresem źródłowym. Baza nic by tu nie
wniosła — dokumenty listujemy wyłącznie w obrębie jednej aukcji.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from app.application.ports import Dokument, ZrodloZDokumentami

log = logging.getLogger(__name__)

KATALOG_ARCHIWUM = pathlib.Path("/data/archiwum-dokumentow")
WAZNOSC_LISTY_S = 900.0
"""Lista plików aukcji nie zmienia się co chwilę; bez pamięci każde
odświeżenie karty byłoby nowym żądaniem strony źródła."""
MIN_WOLNE_BAJTY = 1024 * 1024 * 1024
"""Ta sama rezerwa co w archiwum zdjęć — `/data` dzielą oba."""

_ID = re.compile(r"^[0-9a-f]{16}$")

BramkaSieci = Callable[[str], AbstractAsyncContextManager[None]]


class BrakMiejscaArchiwum(OSError):
    """Rezerwa dysku jest naruszona; niczego nie usuwamy, tylko nie piszemy."""


@dataclass(slots=True, frozen=True)
class DokumentArchiwalny:
    id: str
    nazwa: str
    opis_zrodla: str
    source_url: str
    rozmiar: int
    zapisano: dt.datetime


@dataclass(slots=True, frozen=True)
class PlikDokumentu:
    nazwa: str
    dane: bytes


def nazwa_pliku(nazwa: str) -> str:
    """Bezpieczna nazwa do `Content-Disposition` — bez ścieżek i cudzysłowów."""
    czysta = re.sub(r"[^\w.\- ]+", "_", nazwa, flags=re.UNICODE).strip(" ._")
    czysta = czysta[:120] or "dokument"
    return czysta if czysta.lower().endswith(".pdf") else f"{czysta}.pdf"


class Dokumenty:
    """Lista, proxy i archiwum dokumentów aukcji."""

    def __init__(
        self,
        adaptery: Mapping[str, object],
        *,
        katalog: pathlib.Path = KATALOG_ARCHIWUM,
        min_wolne_bajty: int = MIN_WOLNE_BAJTY,
        bramka: BramkaSieci | None = None,
    ) -> None:
        self._adaptery = adaptery
        self._katalog = katalog
        self._min_wolne = min_wolne_bajty
        self._bramka = bramka
        self._listy: dict[tuple[str, str], tuple[float, tuple[Dokument, ...]]] = {}

    def ustaw_bramke(self, bramka: BramkaSieci) -> None:
        self._bramka = bramka

    @contextlib.asynccontextmanager
    async def _wejdz_do_sieci(self, source_key: str) -> AsyncIterator[None]:
        if self._bramka is None:
            yield
            return
        async with self._bramka(source_key):
            yield

    def _adapter(self, source_key: str) -> ZrodloZDokumentami | None:
        adapter = self._adaptery.get(source_key)
        return adapter if isinstance(adapter, ZrodloZDokumentami) else None

    async def lista(
        self, source_key: str, external_id: str, url: str | None = None
    ) -> tuple[Dokument, ...]:
        """Pliki aukcji wg źródła. Pusta krotka — brak plików albo błąd.

        Błąd źródła nie może zepsuć karty; zostaje w logu jako WARNING.
        """
        klucz = (source_key, external_id)
        wpis = self._listy.get(klucz)
        if wpis is not None and time.monotonic() - wpis[0] < WAZNOSC_LISTY_S:
            return wpis[1]
        adapter = self._adapter(source_key)
        if adapter is None:
            return ()
        try:
            async with self._wejdz_do_sieci(source_key):
                lista = tuple(await adapter.dokumenty(external_id, url))
        except Exception as exc:
            log.warning(
                "nie udało się pobrać listy dokumentów %s/%s: %s",
                source_key,
                external_id,
                exc,
            )
            return ()
        self._listy[klucz] = (time.monotonic(), lista)
        return lista

    async def pobierz(
        self, source_key: str, external_id: str, url: str | None, indeks: int
    ) -> tuple[Dokument, bytes] | None:
        """Bajty dokumentu o danym indeksie z listy add-onu, albo `None`."""
        lista = await self.lista(source_key, external_id, url)
        adapter = self._adapter(source_key)
        if adapter is None or not 0 <= indeks < len(lista):
            return None
        dokument = lista[indeks]
        try:
            async with self._wejdz_do_sieci(source_key):
                dane = await adapter.pobierz_dokument(dokument)
        except Exception as exc:
            log.warning("nie udało się pobrać dokumentu %s: %s", dokument.url, exc)
            return None
        return dokument, dane

    # ------------------------------------------------------------------
    # Archiwum
    # ------------------------------------------------------------------

    def _katalog_aukcji(self, auction_id: int) -> pathlib.Path:
        if auction_id < 0:
            raise ValueError("identyfikator aukcji musi być nieujemny")
        return self._katalog / str(auction_id)

    async def archiwizuj(
        self,
        auction_id: int,
        source_key: str,
        external_id: str,
        url: str | None,
        indeks: int,
        *,
        teraz: dt.datetime,
    ) -> DokumentArchiwalny | None:
        """Pobiera dokument i zapisuje go trwale. Idempotentne po adresie."""
        lista = await self.lista(source_key, external_id, url)
        if not 0 <= indeks < len(lista):
            return None
        for zapisany in self.archiwalne(auction_id):
            if zapisany.source_url == lista[indeks].url:
                return zapisany
        wynik = await self.pobierz(source_key, external_id, url, indeks)
        if wynik is None:
            return None
        dokument, dane = wynik
        identyfikator = hashlib.sha256(dokument.url.encode("utf-8")).hexdigest()[:16]
        opis = DokumentArchiwalny(
            id=identyfikator,
            nazwa=dokument.nazwa,
            opis_zrodla=dokument.opis_zrodla,
            source_url=dokument.url,
            rozmiar=len(dane),
            zapisano=teraz,
        )
        katalog = self._katalog_aukcji(auction_id)
        self._zapisz(katalog / f"{identyfikator}.pdf", dane)
        self._zapisz(
            katalog / f"{identyfikator}.json",
            json.dumps(
                {
                    "nazwa": opis.nazwa,
                    "opis_zrodla": opis.opis_zrodla,
                    "source_url": opis.source_url,
                    "rozmiar": opis.rozmiar,
                    "zapisano": opis.zapisano.isoformat(),
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        return opis

    def archiwalne(self, auction_id: int) -> tuple[DokumentArchiwalny, ...]:
        katalog = self._katalog_aukcji(auction_id)
        if not katalog.is_dir():
            return ()
        wynik: list[DokumentArchiwalny] = []
        for opis in sorted(katalog.glob("*.json")):
            identyfikator = opis.stem
            if (
                not _ID.match(identyfikator)
                or not (katalog / f"{identyfikator}.pdf").is_file()
            ):
                continue
            try:
                dane = json.loads(opis.read_text(encoding="utf-8"))
                wynik.append(
                    DokumentArchiwalny(
                        id=identyfikator,
                        nazwa=str(dane["nazwa"]),
                        opis_zrodla=str(dane.get("opis_zrodla") or ""),
                        source_url=str(dane["source_url"]),
                        rozmiar=int(dane["rozmiar"]),
                        zapisano=dt.datetime.fromisoformat(dane["zapisano"]),
                    )
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("uszkodzony opis dokumentu %s: %s", opis, exc)
        return tuple(sorted(wynik, key=lambda d: d.zapisano))

    def plik_archiwalny(
        self, auction_id: int, identyfikator: str
    ) -> tuple[DokumentArchiwalny, bytes] | None:
        if not _ID.match(identyfikator):
            return None
        for opis in self.archiwalne(auction_id):
            if opis.id == identyfikator:
                sciezka = self._katalog_aukcji(auction_id) / f"{identyfikator}.pdf"
                try:
                    return opis, sciezka.read_bytes()
                except OSError:
                    return None
        return None

    async def pliki_do_analizy(
        self,
        auction_id: int,
        source_key: str,
        external_id: str,
        url: str | None,
        *,
        maks_plikow: int = 4,
        maks_bajtow: int = 25 * 1024 * 1024,
    ) -> tuple[PlikDokumentu, ...]:
        """Dokumenty do przeczytania przez wycenę AI.

        Najpierw archiwum (działa także po zniknięciu aukcji), potem pliki
        źródła, których w archiwum nie ma — w sumie do `maks_plikow`.
        """
        wynik: list[PlikDokumentu] = []
        razem = 0
        znane: set[str] = set()
        for opis in self.archiwalne(auction_id):
            plik = self.plik_archiwalny(auction_id, opis.id)
            if plik is None or razem + len(plik[1]) > maks_bajtow:
                continue
            znane.add(opis.source_url)
            wynik.append(PlikDokumentu(opis.nazwa, plik[1]))
            razem += len(plik[1])
            if len(wynik) >= maks_plikow:
                return tuple(wynik)
        lista = await self.lista(source_key, external_id, url)
        for indeks, dokument in enumerate(lista):
            if dokument.url in znane:
                continue
            pobrany = await self.pobierz(source_key, external_id, url, indeks)
            if pobrany is None or razem + len(pobrany[1]) > maks_bajtow:
                continue
            wynik.append(PlikDokumentu(dokument.nazwa, pobrany[1]))
            razem += len(pobrany[1])
            if len(wynik) >= maks_plikow:
                break
        return tuple(wynik)

    def _zapisz(self, sciezka: pathlib.Path, dane: bytes) -> None:
        rodzic = self._istniejacy_rodzic(self._katalog)
        if shutil.disk_usage(rodzic).free - len(dane) < self._min_wolne:
            raise BrakMiejscaArchiwum(
                "mniej niż 1 GiB wolnego miejsca; archiwum dokumentów wstrzymane"
            )
        sciezka.parent.mkdir(parents=True, exist_ok=True)
        uchwyt, tymczasowy = tempfile.mkstemp(dir=sciezka.parent, suffix=".tmp")
        try:
            with os.fdopen(uchwyt, "wb") as plik:
                plik.write(dane)
            pathlib.Path(tymczasowy).replace(sciezka)
        except BaseException:
            pathlib.Path(tymczasowy).unlink(missing_ok=True)
            raise

    @staticmethod
    def _istniejacy_rodzic(sciezka: pathlib.Path) -> pathlib.Path:
        biezaca = sciezka
        while not biezaca.exists() and biezaca != biezaca.parent:
            biezaca = biezaca.parent
        return biezaca
