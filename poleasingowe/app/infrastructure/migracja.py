"""Przenośny pakiet danych między add-onem HA i kontenerem standalone.

Pakiet zawiera wyłącznie logiczny zrzut własnej bazy i trwałe archiwum
zdjęć. Nie trafiają do niego opcje, sekrety, sesje, debug dumpy ani cache.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import io
import json
import logging
import pathlib
import shutil
import tarfile
from dataclasses import dataclass

from app.infrastructure.kopia import KopiaZapasowa
from app.wersja import WERSJA

log = logging.getLogger(__name__)

KATALOG_PAKIETOW = pathlib.Path("/share/poleasingowe/migracja")
KATALOG_ARCHIWUM = pathlib.Path("/data/archiwum-zdjec")
REZERWA_BAJTOW = 64 * 1024 * 1024
WZORZEC = "poleasingowe-migracja-*.tar"


class BladEksportuMigracji(RuntimeError):
    """Pakiet nie mógł powstać bezpiecznie i w całości."""


@dataclass(slots=True, frozen=True)
class PakietMigracyjny:
    sciezka: pathlib.Path
    utworzono: dt.datetime
    bajtow: int
    sha256: str


class EksporterMigracji:
    """Tworzy najwyżej jeden pakiet naraz i udostępnia jego stan dla UI."""

    def __init__(
        self,
        kopia: KopiaZapasowa,
        *,
        katalog: pathlib.Path = KATALOG_PAKIETOW,
        archiwum: pathlib.Path = KATALOG_ARCHIWUM,
        rezerwa_bajtow: int = REZERWA_BAJTOW,
    ) -> None:
        self._kopia = kopia
        self._katalog = katalog
        self._archiwum = archiwum
        self._rezerwa = rezerwa_bajtow
        self._zadanie: asyncio.Task[None] | None = None
        self._ostatni_blad: str | None = None

    @property
    def w_trakcie(self) -> bool:
        return self._zadanie is not None and not self._zadanie.done()

    @property
    def ostatni_blad(self) -> str | None:
        return self._ostatni_blad

    def ostatni(self) -> PakietMigracyjny | None:
        try:
            pliki = sorted(
                self._katalog.glob(WZORZEC),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        if not pliki:
            return None
        plik = pliki[0]
        try:
            plik_skrotu = plik.with_suffix(".tar.sha256")
            try:
                sha256 = plik_skrotu.read_text(encoding="ascii").strip()
            except OSError:
                sha256 = self._sha256(plik)
            if len(sha256) != 64 or any(
                znak not in "0123456789abcdef" for znak in sha256
            ):
                sha256 = self._sha256(plik)
            return PakietMigracyjny(
                sciezka=plik,
                utworzono=dt.datetime.fromtimestamp(plik.stat().st_mtime, dt.UTC),
                bajtow=plik.stat().st_size,
                sha256=sha256,
            )
        except OSError:
            return None

    def uruchom(self, teraz: dt.datetime | None = None) -> bool:
        """Uruchamia eksport w tle; `False` oznacza, że jeden już trwa."""
        if self.w_trakcie:
            return False
        self._ostatni_blad = None
        chwila = teraz or dt.datetime.now(dt.UTC)
        self._zadanie = asyncio.create_task(
            self._uruchomione(chwila), name="poleasingowe-eksport-migracji"
        )
        return True

    async def _uruchomione(self, teraz: dt.datetime) -> None:
        try:
            pakiet = await self.utworz(teraz)
        except Exception as exc:
            self._ostatni_blad = str(exc)
            log.exception("eksport migracyjny nie powiódł się")
        else:
            log.info(
                "pakiet migracyjny: %s (%s B, sha256 %s)",
                pakiet.sciezka.name,
                pakiet.bajtow,
                pakiet.sha256,
            )

    async def zaczekaj(self) -> None:
        """Czeka na zadanie uruchomione z UI; używane też w testach."""
        if self._zadanie is not None:
            await self._zadanie

    async def zamknij(self) -> None:
        """Nie zostawia częściowego tar-a podczas łagodnego SIGTERM."""
        await self.zaczekaj()

    async def utworz(self, teraz: dt.datetime) -> PakietMigracyjny:
        """Robi świeży dump i pakuje go z archiwum zdjęć."""
        await asyncio.to_thread(self._sprawdz_miejsce_przed_startem)
        kopia = await self._kopia.wykonaj(teraz)
        return await asyncio.to_thread(self._spakuj, kopia.sciezka, teraz)

    def _sprawdz_miejsce_przed_startem(self) -> None:
        """Chroni `/share` zanim pg_dump zacznie tworzyć kolejny plik."""
        self._katalog.mkdir(parents=True, exist_ok=True)
        zdjecia_bajty = sum(plik.stat().st_size for _, plik in self._zdjecia())
        ostatnia = self._kopia.ostatnia()
        # Przy pierwszym eksporcie nie znamy jeszcze rozmiaru dumpa. 16 MiB
        # jest większe od zmierzonej pustej bazy; dokładny test powtarzamy po
        # pg_dump, zanim zacznie powstawać tar.
        dump_bajty = 16 * 1024 * 1024 if ostatnia is None else ostatnia.bajtow
        potrzeba = zdjecia_bajty + 2 * dump_bajty + self._rezerwa
        wolne = shutil.disk_usage(self._katalog).free
        if wolne < potrzeba:
            raise BladEksportuMigracji(
                f"Za mało miejsca na eksport: wolne {wolne} B, potrzeba "
                f"co najmniej {potrzeba} B"
            )

    def _spakuj(self, dump: pathlib.Path, teraz: dt.datetime) -> PakietMigracyjny:
        self._katalog.mkdir(parents=True, exist_ok=True)
        cel = self._katalog / f"poleasingowe-migracja-{teraz:%Y%m%d-%H%M%S}.tar"
        tymczasowy = cel.with_suffix(".tar.czesciowy")
        zdjecia = self._zdjecia()
        pliki = [("database.dump", dump), *zdjecia]
        rozmiar = sum(p.stat().st_size for _, p in pliki)
        narzut_tar = (len(pliki) + 4) * 1024
        wolne = shutil.disk_usage(self._katalog).free
        potrzeba = rozmiar + narzut_tar + self._rezerwa
        if wolne < potrzeba:
            raise BladEksportuMigracji(
                f"Za mało miejsca na pakiet: wolne {wolne} B, potrzeba "
                f"co najmniej {potrzeba} B"
            )

        wpisy = [
            {
                "path": nazwa,
                "size": plik.stat().st_size,
                "sha256": self._sha256(plik),
            }
            for nazwa, plik in pliki
        ]
        manifest = json.dumps(
            {
                "format": 1,
                "app_version": WERSJA,
                "created_at": teraz.isoformat(),
                "files": wpisy,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")

        try:
            with tarfile.open(tymczasowy, "w") as archiwum:
                info = tarfile.TarInfo("manifest.json")
                info.size = len(manifest)
                info.mtime = int(teraz.timestamp())
                info.mode = 0o600
                archiwum.addfile(info, io.BytesIO(manifest))
                for nazwa, plik in pliki:
                    archiwum.add(
                        plik,
                        arcname=nazwa,
                        recursive=False,
                        filter=self._normalizuj_metadane,
                    )
            tymczasowy.replace(cel)
        except Exception:
            tymczasowy.unlink(missing_ok=True)
            raise

        sha256 = self._sha256(cel)
        plik_skrotu = cel.with_suffix(".tar.sha256")
        tymczasowy_skrot = plik_skrotu.with_suffix(".sha256.czesciowy")
        tymczasowy_skrot.write_text(f"{sha256}\n", encoding="ascii")
        tymczasowy_skrot.replace(plik_skrotu)
        return PakietMigracyjny(
            sciezka=cel,
            utworzono=teraz,
            bajtow=cel.stat().st_size,
            sha256=sha256,
        )

    def _zdjecia(self) -> list[tuple[str, pathlib.Path]]:
        if not self._archiwum.is_dir():
            return []
        wynik = []
        for plik in sorted(self._archiwum.rglob("*.jpg")):
            if plik.is_file() and not plik.is_symlink():
                wzgledna = plik.relative_to(self._archiwum).as_posix()
                wynik.append((f"archiwum-zdjec/{wzgledna}", plik))
        return wynik

    @staticmethod
    def _normalizuj_metadane(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mode = 0o600
        return info

    @staticmethod
    def _sha256(plik: pathlib.Path) -> str:
        skrot = hashlib.sha256()
        with plik.open("rb") as uchwyt:
            for fragment in iter(lambda: uchwyt.read(1024 * 1024), b""):
                skrot.update(fragment)
        return skrot.hexdigest()
