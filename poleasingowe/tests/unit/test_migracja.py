"""Pakiet migracyjny jest kompletny, przenośny i nie zabiera sekretów."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from typing import cast

import pytest

from app.infrastructure.kopia import Kopia, KopiaZapasowa
from app.infrastructure.migracja import BladEksportuMigracji, EksporterMigracji
from app.wersja import WERSJA

TERAZ = dt.datetime(2026, 9, 19, 12, 0, tzinfo=dt.UTC)


class _Kopia:
    def __init__(self, katalog: pathlib.Path) -> None:
        self.katalog = katalog
        self.wywolan = 0

    def ostatnia(self) -> Kopia | None:
        return None

    async def wykonaj(self, teraz: dt.datetime) -> Kopia:
        self.wywolan += 1
        plik = self.katalog / "swiezy.dump"
        plik.write_bytes(b"PGDMP-test")
        return Kopia(plik, teraz, plik.stat().st_size)


def _eksporter(
    tmp_path: pathlib.Path, kopia: object | None = None
) -> tuple[EksporterMigracji, pathlib.Path, pathlib.Path]:
    archiwum = tmp_path / "data" / "archiwum-zdjec"
    katalog = tmp_path / "share" / "migracja"
    archiwum.mkdir(parents=True)
    instancja = kopia or _Kopia(tmp_path)
    return (
        EksporterMigracji(
            cast(KopiaZapasowa, instancja),
            katalog=katalog,
            archiwum=archiwum,
            rezerwa_bajtow=0,
        ),
        archiwum,
        katalog,
    )


async def test_pakiet_zawiera_dump_zdjecia_i_manifest(tmp_path: pathlib.Path) -> None:
    eksporter, archiwum, katalog = _eksporter(tmp_path)
    zdjecie = archiwum / "12" / "0.jpg"
    zdjecie.parent.mkdir()
    zdjecie.write_bytes(b"jpeg-test")
    # Te katalogi sąsiadują z archiwum w realnym `/data`, ale nie wolno ich
    # przenosić: zawierają sesje, dane diagnostyczne albo odtwarzalny cache.
    for nazwa in ("sessions", "debug", "cache"):
        sekret = archiwum.parent / nazwa / "nie-pakuj.txt"
        sekret.parent.mkdir()
        sekret.write_text("tajne", encoding="utf-8")

    pakiet = await eksporter.utworz(TERAZ)

    assert pakiet.sciezka == katalog / "poleasingowe-migracja-20260919-120000.tar"
    assert pakiet.sha256 == hashlib.sha256(pakiet.sciezka.read_bytes()).hexdigest()
    assert list(katalog.glob("*.czesciowy")) == []
    assert pakiet.sciezka.with_suffix(".tar.sha256").read_text().strip() == (
        pakiet.sha256
    )
    with tarfile.open(pakiet.sciezka) as tar:
        nazwy = tar.getnames()
        assert nazwy == [
            "manifest.json",
            "database.dump",
            "archiwum-zdjec/12/0.jpg",
        ]
        manifest_file = tar.extractfile("manifest.json")
        assert manifest_file is not None
        manifest = json.loads(manifest_file.read())
        assert manifest["format"] == 1
        assert manifest["app_version"] == WERSJA
        assert [wpis["path"] for wpis in manifest["files"]] == nazwy[1:]
        assert tar.extractfile("database.dump").read() == b"PGDMP-test"  # type: ignore[union-attr]
        assert tar.extractfile("archiwum-zdjec/12/0.jpg").read() == b"jpeg-test"  # type: ignore[union-attr]
        assert all("nie-pakuj" not in nazwa for nazwa in nazwy)

    rozpakowane = tmp_path / "rozpakowane"
    weryfikator = (
        pathlib.Path(__file__).resolve().parents[3]
        / "deploy/proxmox/scripts/verify_package.py"
    )
    wynik = subprocess.run(
        [sys.executable, str(weryfikator), str(pakiet.sciezka), str(rozpakowane)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert wynik.returncode == 0, wynik.stderr
    assert (rozpakowane / "database.dump").read_bytes() == b"PGDMP-test"
    assert (rozpakowane / "archiwum-zdjec/12/0.jpg").read_bytes() == b"jpeg-test"


class _WstrzymanaKopia(_Kopia):
    def __init__(self, katalog: pathlib.Path) -> None:
        super().__init__(katalog)
        self.rozpoczeto = asyncio.Event()
        self.kontynuuj = asyncio.Event()

    async def wykonaj(self, teraz: dt.datetime) -> Kopia:
        self.rozpoczeto.set()
        await self.kontynuuj.wait()
        return await super().wykonaj(teraz)


async def test_z_interfejsu_moze_trwac_tylko_jeden_eksport(
    tmp_path: pathlib.Path,
) -> None:
    kopia = _WstrzymanaKopia(tmp_path)
    eksporter, _, _ = _eksporter(tmp_path, kopia)

    assert eksporter.uruchom(TERAZ) is True
    await kopia.rozpoczeto.wait()
    assert eksporter.w_trakcie is True
    assert eksporter.uruchom(TERAZ) is False
    kopia.kontynuuj.set()
    await eksporter.zaczekaj()

    assert kopia.wywolan == 1
    assert eksporter.ostatni() is not None


async def test_brak_miejsca_zatrzymuje_eksport_przed_pg_dump(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kopia = _Kopia(tmp_path)
    eksporter, _, _ = _eksporter(tmp_path, kopia)
    monkeypatch.setattr(
        "app.infrastructure.migracja.shutil.disk_usage",
        lambda _: SimpleNamespace(free=0),
    )

    with pytest.raises(BladEksportuMigracji, match="Za mało miejsca"):
        await eksporter.utworz(TERAZ)

    assert kopia.wywolan == 0
