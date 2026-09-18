"""Zdjęcia pojazdów — proxy z cache na dysku (SPEC.md §12).

„Bez miniatur zdjęć na start. Jeśli kiedyś — **proxy z cache na dysku
i twardym limitem, nie hotlink**." To jest to „kiedyś".

Zdjęcia mają dwie warstwy: rotowany cache oryginałów dla aktywnych aukcji
oraz trwałe, pomniejszone archiwum. Bajty nigdy nie trafiają do bazy;
PostgreSQL przechowuje wyłącznie małe metadane i stan kolejki.

- **Adresy z odczytu szczegółów wykorzystujemy ponownie.** Dzięki temu
  archiwizacja nie pobiera drugi raz strony aukcji. Kolejka przechowuje je
  do czasu zapisania plików, bo strona może zniknąć tuż po zakończeniu.
- **Nie hotlinkujemy.** Przeglądarka pobiera obraz z add-onu, nie z serwisu
  aukcyjnego. Inaczej każde otwarcie panelu byłoby ruchem widocznym dla
  serwisu, a Ingress i tak nie wypuściłby żądań na zewnątrz.
- **Twardy limit cache'u, rezerwa archiwum.** Cache kasuje najstarsze pliki.
  Archiwum niczego samoczynnie nie usuwa, ale przestaje pisać przed
  naruszeniem rezerwy 1 GiB wolnego miejsca.

Adres obrazu **nigdy nie pochodzi od przeglądarki** — trasa przyjmuje numer
aukcji i indeks, a URL wybiera add-on z listy, którą sam wyczytał ze strony
źródła. Gdyby przyjmowała adres, byłaby otwartym proxy: każdy, kto dosięgnie
panelu, mógłby przez add-on odpytywać dowolny adres w sieci lokalnej.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import io
import logging
import os
import pathlib
import shutil
import ssl
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.application.ports import AuctionSource, FabrykaKontekstu
from app.domain.entities import ArchivedPhoto, PhotoArchiveJob
from app.domain.enums import PhotoArchiveTarget

log = logging.getLogger(__name__)

KATALOG_CACHE = pathlib.Path("/data/cache/zdjecia")
KATALOG_ARCHIWUM = pathlib.Path("/data/archiwum-zdjec")
LIMIT_KATALOGU_BAJTY = 100 * 1024 * 1024
"""SPEC.md §1.1 — `/data` ma budżet, więc cache ma sufit."""
LIMIT_PLIKU_BAJTY = 4 * 1024 * 1024
WAZNOSC_LISTY_S = 900.0
"""Jak długo pamiętamy adresy galerii. Zdjęcia nie zmieniają się w trakcie
trwania aukcji, a bez tego każde odświeżenie karty to nowe żądanie strony."""
ROZMIAR_MINIATURY = (240, 160)
JAKOSC_MINIATURY = 78
ROZMIAR_OKLADKI = (800, 600)
ROZMIAR_GALERII = (1280, 1280)
JAKOSC_ARCHIWUM = 78
MIN_WOLNE_BAJTY = 1024 * 1024 * 1024

TYPY = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

BramkaSieci = Callable[[str], AbstractAsyncContextManager[None]]


class BrakMiejscaArchiwum(OSError):
    """Rezerwa dysku jest naruszona; istniejących zdjęć nie usuwamy."""


@dataclass(slots=True, frozen=True)
class StatystykiArchiwumZdjec:
    bajty: int
    wolne_bajty: int
    oczekujace: int
    niedostepne: int
    pelne_galerie: int
    zapis_wstrzymany: bool


class GaleriaZdjec:
    """Pobiera i cache'uje zdjęcia aukcji."""

    def __init__(
        self,
        adaptery: Mapping[str, AuctionSource],
        *,
        katalog: pathlib.Path = KATALOG_CACHE,
        katalog_archiwum: pathlib.Path = KATALOG_ARCHIWUM,
        limit_katalogu: int = LIMIT_KATALOGU_BAJTY,
        limit_pliku: int = LIMIT_PLIKU_BAJTY,
        min_wolne_bajty: int = MIN_WOLNE_BAJTY,
        timeout: float = 20.0,
        bramka: BramkaSieci | None = None,
        fabryka: FabrykaKontekstu | None = None,
    ) -> None:
        self._adaptery = adaptery
        self._katalog = katalog
        self._katalog_archiwum = katalog_archiwum
        self._limit_katalogu = limit_katalogu
        self._limit_pliku = limit_pliku
        self._min_wolne_bajty = min_wolne_bajty
        self._timeout = timeout
        self._bramka = bramka
        self._fabryka = fabryka
        self._listy: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
        self._klient: httpx.AsyncClient | None = None
        self._budzik_archiwum = asyncio.Event()
        self._ostatnia_synchronizacja: float | None = None
        self._wymaga_synchronizacji = True

    def ustaw_bramke(self, bramka: BramkaSieci) -> None:
        """Podpina wspólny limit dispatchera po jego utworzeniu."""
        self._bramka = bramka

    def zapamietaj_adresy(
        self, source_key: str, external_id: str, adresy: Sequence[str]
    ) -> None:
        """Przejmuje adresy wyciągnięte z odczytu szczegółów bez nowego HTTP."""
        self._listy[(source_key, external_id)] = (
            time.monotonic(),
            tuple(adresy),
        )
        self._wymaga_synchronizacji = True
        self._budzik_archiwum.set()

    def obudz_archiwum(self) -> None:
        """Budzi pracownika po dodaniu aukcji do watchlisty."""
        self._budzik_archiwum.set()

    async def wymus_pelne(self, auction_id: int) -> None:
        """Podnosi cel do pełnej galerii; odgwiazdkowanie go nie obniża."""
        if self._fabryka is None:
            return
        teraz = dt.datetime.now(dt.UTC)
        async with self._fabryka() as kontekst, kontekst.uow as uow:
            await uow.photo_archive.wymus_pelne(auction_id, teraz)
        self._budzik_archiwum.set()

    async def uruchom_archiwum(self) -> None:
        """Pojedynczy, niskopriorytetowy pracownik zapisujący po jednym zdjęciu."""
        if self._fabryka is None:
            return
        log.info("pracownik archiwum zdjęć wystartował")
        try:
            while True:
                zrobiono = False
                try:
                    zrobiono = await self._jeden_krok_archiwum()
                except Exception:
                    # Awaria archiwum nie może zatrzymać dispatchera ani UI.
                    log.exception("krok archiwum zdjęć nie powiódł się")
                if zrobiono:
                    # Oddaj event loop po każdym obrazie. Dispatcher czekający
                    # na wspólną bramkę źródła dostanie szansę przed kolejnym.
                    await asyncio.sleep(1.0)
                    continue
                self._budzik_archiwum.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._budzik_archiwum.wait(), 60.0)
        except asyncio.CancelledError:
            log.info("pracownik archiwum zdjęć zatrzymany")
            raise

    async def _jeden_krok_archiwum(self) -> bool:
        assert self._fabryka is not None
        async with self._fabryka() as kontekst:
            teraz = await kontekst.zapytania.czas_serwera()
            async with kontekst.uow as uow:
                monotoniczny = time.monotonic()
                if (
                    self._wymaga_synchronizacji
                    or self._ostatnia_synchronizacja is None
                    or monotoniczny - self._ostatnia_synchronizacja >= 60.0
                ):
                    self._wymaga_synchronizacji = False
                    try:
                        await uow.photo_archive.synchronizuj(teraz)
                    except Exception:
                        self._wymaga_synchronizacji = True
                        raise
                    self._ostatnia_synchronizacja = monotoniczny
                zadanie = await uow.photo_archive.nastepne(teraz)
        if zadanie is None:
            return False

        try:
            await self._archiwizuj_jeden(zadanie, teraz)
        except BrakMiejscaArchiwum as exc:
            # To nie jest błąd konkretnej aukcji. Nie zużywamy jej pięciu
            # prób i nie oznaczamy UNAVAILABLE — po zwolnieniu miejsca ma
            # ruszyć sama, a istniejących zdjęć nie wolno nam usuwać.
            log.warning("archiwum zdjęć wstrzymane: %s", exc)
            return False
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            log.warning(
                "nie udało się zarchiwizować zdjęcia aukcji %s: %s",
                zadanie.auction_id,
                exc,
            )
            async with self._fabryka() as kontekst, kontekst.uow as uow:
                await uow.photo_archive.odnotuj_blad(
                    zadanie.auction_id, teraz, f"{type(exc).__name__}: {exc}"
                )
        return True

    async def _archiwizuj_jeden(
        self, zadanie: PhotoArchiveJob, teraz: dt.datetime
    ) -> None:
        assert self._fabryka is not None
        adresy = zadanie.source_urls
        if not adresy:
            adresy = await self.adresy(
                zadanie.source_key, zadanie.external_id, zadanie.url
            )
            if not adresy:
                raise OSError("źródło nie udostępnia już galerii")
            async with self._fabryka() as kontekst, kontekst.uow as uow:
                await uow.photo_archive.ustaw_adresy(zadanie.auction_id, adresy, teraz)

        if zadanie.target is PhotoArchiveTarget.COVER:
            brakujace = () if 0 in zadanie.archived_positions else (0,)
            granica = ROZMIAR_OKLADKI
        else:
            zapisane = set(zadanie.archived_full_positions)
            brakujace = tuple(i for i in range(len(adresy)) if i not in zapisane)
            granica = ROZMIAR_GALERII

        if not brakujace:
            async with self._fabryka() as kontekst, kontekst.uow as uow:
                await uow.photo_archive.zakoncz_krok(
                    zadanie.auction_id, teraz, len(adresy)
                )
            return

        indeks = brakujace[0]
        oryginal = await self._z_cache_lub_sieci(zadanie.source_key, adresy[indeks])
        if oryginal is None:
            raise OSError(f"nie udało się pobrać zdjęcia {indeks}")
        if len(oryginal[0]) > self._limit_pliku:
            raise OSError(f"zdjęcie {indeks} ma {len(oryginal[0])} B — ponad limit")
        dane, szerokosc, wysokosc = await asyncio.to_thread(
            self._pomniejsz_archiwalny, oryginal[0], granica
        )
        await asyncio.to_thread(
            self._zapisz_trwale,
            self._sciezka_archiwum(zadanie.auction_id, indeks),
            dane,
        )
        zdjecie = ArchivedPhoto(
            auction_id=zadanie.auction_id,
            position=indeks,
            target=zadanie.target,
            width=szerokosc,
            height=wysokosc,
            byte_size=len(dane),
            sha256=hashlib.sha256(dane).hexdigest(),
            archived_at=teraz,
        )
        async with self._fabryka() as kontekst, kontekst.uow as uow:
            await uow.photo_archive.zapisz_zdjecie(zdjecie)
            await uow.photo_archive.zakoncz_krok(zadanie.auction_id, teraz, len(adresy))

    @contextlib.asynccontextmanager
    async def _wejdz_do_sieci(self, source_key: str) -> AsyncIterator[None]:
        if self._bramka is None:
            yield
            return
        async with self._bramka(source_key):
            yield

    async def _klient_http(self) -> httpx.AsyncClient:
        if self._klient is None:
            self._klient = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                # Leasygroup nie wysyła pełnego łańcucha certyfikatów. Obraz
                # dodatku instaluje jego publiczny certyfikat pośredni do
                # systemowego magazynu CA; domyślny klient httpx używa zaś
                # własnego certifi i przez to galeria nie mogła pobrać zdjęć.
                # Weryfikacja TLS pozostaje włączona.
                verify=ssl.create_default_context(),
            )
        return self._klient

    async def zamknij(self) -> None:
        if self._klient is not None:
            await self._klient.aclose()
            self._klient = None

    async def adresy(
        self, source_key: str, external_id: str, url: str | None = None
    ) -> tuple[str, ...]:
        """Adresy zdjęć aukcji. Pusta krotka, gdy źródło ich nie udostępnia.

        `url` to adres aukcji z bazy — adapter woli go od adresu składanego
        z identyfikatora (`sources/adresy.py`).
        """
        klucz = (source_key, external_id)
        wpis = self._listy.get(klucz)
        if wpis is not None and time.monotonic() - wpis[0] < WAZNOSC_LISTY_S:
            return wpis[1]

        adapter = self._adaptery.get(source_key)
        pobierz = getattr(adapter, "zdjecia", None)
        if adapter is None or pobierz is None:
            return ()

        try:
            # Pełna dokumentacja fotograficzna pojazdu. Limit przestrzeni
            # dotyczy cache'u bajtów, nie liczby adresów w galerii.
            async with self._wejdz_do_sieci(source_key):
                adresy = tuple(await pobierz(external_id, url))
        except Exception as exc:
            # Brak zdjęć nie ma prawa zepsuć karty aukcji — reszta danych
            # jest nadal użyteczna, a serwis bywa chwilowo niedostępny.
            #
            # WARNING, nie INFO: przy domyślnym poziomie logów INFO było
            # niewidoczne, więc źródło, które NIGDY nie oddaje zdjęć,
            # wyglądało tak samo jak źródło bez galerii — puste miejsce
            # na karcie i cisza w logach.
            log.warning(
                "nie udało się pobrać galerii %s/%s: %s", source_key, external_id, exc
            )
            return ()

        self._listy[klucz] = (time.monotonic(), adresy)
        return adresy

    async def obraz(
        self,
        source_key: str,
        external_id: str,
        indeks: int,
        *,
        miniatura: bool = False,
        url: str | None = None,
        auction_id: int | None = None,
    ) -> tuple[bytes, str] | None:
        """Bajty zdjęcia i jego typ MIME. `None`, gdy takiego zdjęcia nie ma.

        Indeks, nie adres — patrz uwaga o otwartym proxy w opisie modułu.
        """
        wynik = await self.obraz_http(
            source_key,
            external_id,
            indeks,
            miniatura=miniatura,
            url=url,
            auction_id=auction_id,
        )
        return None if wynik is None else (wynik[0], wynik[1])

    async def obraz_http(
        self,
        source_key: str,
        external_id: str,
        indeks: int,
        *,
        miniatura: bool = False,
        url: str | None = None,
        auction_id: int | None = None,
    ) -> tuple[bytes, str, str | None] | None:
        """Jak `obraz`, ale z hashem ETag dla trwałego pliku."""
        if auction_id is not None:
            archiwalne = await self.archiwalne(auction_id)
            metadane = next((z for z in archiwalne if z.position == indeks), None)
            if metadane is not None:
                try:
                    dane = self._sciezka_archiwum(auction_id, indeks).read_bytes()
                except OSError:
                    log.warning(
                        "metadane zdjęcia %s/%s istnieją bez pliku",
                        auction_id,
                        indeks,
                    )
                else:
                    if miniatura:
                        dane = await self._miniatura_z_archiwum(metadane, dane)
                    return dane, "image/jpeg", metadane.sha256

        adresy = await self.adresy(source_key, external_id, url)
        if not 0 <= indeks < len(adresy):
            return None
        adres_zdjecia = adresy[indeks]
        if miniatura:
            wynik = await self._miniatura(source_key, adres_zdjecia)
        else:
            wynik = await self._z_cache_lub_sieci(source_key, adres_zdjecia)
        return None if wynik is None else (wynik[0], wynik[1], None)

    async def archiwalne(self, auction_id: int) -> tuple[ArchivedPhoto, ...]:
        if self._fabryka is None:
            return ()
        async with self._fabryka() as kontekst, kontekst.uow as uow:
            return tuple(await uow.photo_archive.dla_aukcji(auction_id))

    async def _miniatura_z_archiwum(
        self, metadane: ArchivedPhoto, dane: bytes
    ) -> bytes:
        klucz = f"archive:{metadane.auction_id}:{metadane.sha256}"
        sciezka = self._sciezka_miniatury(klucz)
        try:
            return sciezka.read_bytes()
        except OSError:
            miniatura = await asyncio.to_thread(self._pomniejsz, dane)
            await asyncio.to_thread(self._zapisz, sciezka, miniatura)
            return miniatura

    async def diagnostyka(self) -> StatystykiArchiwumZdjec:
        oczekujace = niedostepne = pelne = 0
        if self._fabryka is not None:
            async with self._fabryka() as kontekst, kontekst.uow as uow:
                oczekujace, niedostepne, pelne = await uow.photo_archive.statystyki()
        bajty = 0
        if self._katalog_archiwum.is_dir():
            for plik in self._katalog_archiwum.rglob("*.jpg"):
                try:
                    bajty += plik.stat().st_size
                except OSError:
                    continue
        wolne = shutil.disk_usage(self._istniejacy_rodzic(self._katalog_archiwum)).free
        return StatystykiArchiwumZdjec(
            bajty=bajty,
            wolne_bajty=wolne,
            oczekujace=oczekujace,
            niedostepne=niedostepne,
            pelne_galerie=pelne,
            zapis_wstrzymany=wolne < self._min_wolne_bajty,
        )

    async def _miniatura(self, source_key: str, url: str) -> tuple[bytes, str] | None:
        """Mały JPEG 3:2 do listy; nigdy oryginał tylko pomniejszony CSS-em."""
        sciezka = self._sciezka_miniatury(url)
        try:
            return sciezka.read_bytes(), "image/jpeg"
        except OSError:
            pass

        oryginal = await self._z_cache_lub_sieci(source_key, url)
        if oryginal is None:
            return None
        try:
            dane = await asyncio.to_thread(self._pomniejsz, oryginal[0])
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            log.info("nie udało się utworzyć miniatury %s: %s", url, exc)
            return None
        await asyncio.to_thread(self._zapisz, sciezka, dane)
        return dane, "image/jpeg"

    @staticmethod
    def _pomniejsz(dane: bytes) -> bytes:
        with Image.open(io.BytesIO(dane)) as oryginal:
            poprawiony = ImageOps.exif_transpose(oryginal)
            miniatura = ImageOps.fit(
                poprawiony.convert("RGB"),
                ROZMIAR_MINIATURY,
                method=Image.Resampling.LANCZOS,
            )
            wynik = io.BytesIO()
            miniatura.save(
                wynik, format="JPEG", quality=JAKOSC_MINIATURY, optimize=True
            )
            return wynik.getvalue()

    @staticmethod
    def _pomniejsz_archiwalny(
        dane: bytes, granica: tuple[int, int]
    ) -> tuple[bytes, int, int]:
        """JPEG bez EXIF, mieszczący się w granicy i nigdy niepowiększany."""
        with Image.open(io.BytesIO(dane)) as oryginal:
            poprawiony = ImageOps.exif_transpose(oryginal)
            poprawiony.thumbnail(granica, Image.Resampling.LANCZOS)
            if poprawiony.mode in ("RGBA", "LA"):
                rgb = Image.new("RGB", poprawiony.size, "white")
                alfa = poprawiony.getchannel("A")
                rgb.paste(poprawiony.convert("RGB"), mask=alfa)
            else:
                rgb = poprawiony.convert("RGB")
            wynik = io.BytesIO()
            rgb.save(wynik, format="JPEG", quality=JAKOSC_ARCHIWUM, optimize=True)
            return wynik.getvalue(), rgb.width, rgb.height

    async def _z_cache_lub_sieci(
        self, source_key: str, url: str
    ) -> tuple[bytes, str] | None:
        sciezka = self._sciezka_cache(url)
        typ = TYPY.get(sciezka.suffix, "application/octet-stream")
        try:
            return sciezka.read_bytes(), typ
        except OSError:
            pass

        try:
            async with self._wejdz_do_sieci(source_key):
                klient = await self._klient_http()
                odpowiedz = await klient.get(url, headers=self._naglowki(url))
                odpowiedz.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("nie udało się pobrać zdjęcia %s: %s", url, exc)
            return None

        dane = odpowiedz.content
        if len(dane) > self._limit_pliku:
            log.info("zdjęcie %s ma %s B — ponad limit, nie cache'uję", url, len(dane))
            return dane, typ

        await asyncio.to_thread(self._zapisz, sciezka, dane)
        return dane, typ

    @staticmethod
    def _naglowki(url: str) -> dict[str, str]:
        """`Referer` z własnego serwisu — inaczej część serwerów oddaje 403.

        Obrazy stoją zwykle za tą samą ochroną co strona: żądanie bez
        `Referer` wygląda jak hotlink z obcej witryny. Nie udajemy tu
        przeglądarki dla samego udawania — wskazujemy stronę, z której
        zdjęcie faktycznie pochodzi.
        """
        rozbity = urlsplit(url)
        return {"Referer": f"{rozbity.scheme}://{rozbity.netloc}/"}

    def _sciezka_cache(self, url: str) -> pathlib.Path:
        # Nazwa z hasha adresu: adresy bywają długie, mają znaki spoza
        # dozwolonych w nazwie pliku i nie wolno im sterować ścieżką.
        odcisk = hashlib.sha256(url.encode("utf-8")).hexdigest()
        rozszerzenie = next(
            (r for r in TYPY if url.lower().split("?")[0].endswith(r)), ".jpg"
        )
        return self._katalog / f"{odcisk}{rozszerzenie}"

    def _sciezka_miniatury(self, url: str) -> pathlib.Path:
        odcisk = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._katalog / f"{odcisk}.miniatura.jpg"

    def _sciezka_archiwum(self, auction_id: int, indeks: int) -> pathlib.Path:
        if auction_id < 0 or indeks < 0:
            raise ValueError("identyfikator aukcji i indeks muszą być nieujemne")
        return self._katalog_archiwum / str(auction_id) / f"{indeks}.jpg"

    def _zapisz_trwale(self, sciezka: pathlib.Path, dane: bytes) -> None:
        """Atomowo zastępuje plik, np. przy ulepszeniu okładki do 1280 px."""
        rodzic = self._istniejacy_rodzic(self._katalog_archiwum)
        if shutil.disk_usage(rodzic).free - len(dane) < self._min_wolne_bajty:
            raise BrakMiejscaArchiwum(
                "mniej niż 1 GiB wolnego miejsca; zapis archiwum wstrzymany"
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

    def _zapisz(self, sciezka: pathlib.Path, dane: bytes) -> None:
        # Ta sama karta (miniatura na liście) i galeria szczegółów proszą
        # o indeks 0 tego samego zdjęcia niemal równocześnie — dwa wątki
        # `asyncio.to_thread` mogą trafić tu w tej samej milisekundzie.
        # Jeśli ktoś już zdążył zapisać ten plik, nie ma czego robić
        # drugi raz: krótsza droga niż łapanie wyścigu w środku zapisu.
        if sciezka.exists():
            return
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
