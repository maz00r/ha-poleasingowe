#!/usr/bin/env python3
"""Sprawdza i bezpiecznie rozpakowuje pakiet migracyjny."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import tarfile
from typing import Any


def blad(komunikat: str) -> None:
    raise ValueError(komunikat)


def bezpieczna_nazwa(nazwa: str) -> bool:
    sciezka = pathlib.PurePosixPath(nazwa)
    return (
        not sciezka.is_absolute()
        and ".." not in sciezka.parts
        and nazwa != ""
        and (
            nazwa in {"manifest.json", "database.dump"}
            or nazwa.startswith("archiwum-zdjec/")
        )
    )


def sha256_z_tar(archiwum: tarfile.TarFile, element: tarfile.TarInfo) -> str:
    uchwyt = archiwum.extractfile(element)
    if uchwyt is None:
        blad(f"Nie można odczytać {element.name}")
    skrot = hashlib.sha256()
    for fragment in iter(lambda: uchwyt.read(1024 * 1024), b""):
        skrot.update(fragment)
    return skrot.hexdigest()


def sprawdz_manifest(dane: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(dane, dict) or dane.get("format") != 1:
        blad("Nieobsługiwany format manifestu")
    wpisy = dane.get("files")
    if not isinstance(wpisy, list):
        blad("Manifest nie zawiera listy plików")
    wynik: dict[str, dict[str, Any]] = {}
    for wpis in wpisy:
        if not isinstance(wpis, dict):
            blad("Niepoprawny wpis manifestu")
        nazwa = wpis.get("path")
        rozmiar = wpis.get("size")
        skrot = wpis.get("sha256")
        if (
            not isinstance(nazwa, str)
            or not bezpieczna_nazwa(nazwa)
            or nazwa == "manifest.json"
            or not isinstance(rozmiar, int)
            or rozmiar < 0
            or not isinstance(skrot, str)
            or len(skrot) != 64
        ):
            blad(f"Niepoprawny wpis manifestu: {nazwa!r}")
        if nazwa in wynik:
            blad(f"Powtórzony plik w manifeście: {nazwa}")
        wynik[nazwa] = wpis
    if "database.dump" not in wynik:
        blad("Pakiet nie zawiera database.dump")
    return wynik


def zweryfikuj_i_rozpakuj(pakiet: pathlib.Path, cel: pathlib.Path) -> None:
    cel.mkdir(parents=True, exist_ok=True)
    if any(cel.iterdir()):
        blad(f"Katalog docelowy nie jest pusty: {cel}")

    with tarfile.open(pakiet, "r:*") as archiwum:
        elementy = archiwum.getmembers()
        if any(not element.isfile() for element in elementy):
            blad("Pakiet może zawierać wyłącznie zwykłe pliki")
        if any(not bezpieczna_nazwa(element.name) for element in elementy):
            blad("Pakiet zawiera niedozwoloną ścieżkę")
        nazwy = [element.name for element in elementy]
        if len(nazwy) != len(set(nazwy)):
            blad("Pakiet zawiera powtórzone ścieżki")

        try:
            manifest_info = archiwum.getmember("manifest.json")
        except KeyError:
            blad("Pakiet nie zawiera manifest.json")
        manifest_file = archiwum.extractfile(manifest_info)
        if manifest_file is None:
            blad("Nie można odczytać manifest.json")
        manifest_surowy = json.load(manifest_file)
        manifest = sprawdz_manifest(manifest_surowy)

        elementy_danych = {
            element.name: element
            for element in elementy
            if element.name != "manifest.json"
        }
        if set(elementy_danych) != set(manifest):
            blad("Zawartość archiwum nie zgadza się z manifestem")

        for nazwa, wpis in manifest.items():
            element = elementy_danych[nazwa]
            if element.size != wpis["size"]:
                blad(f"Niepoprawny rozmiar {nazwa}")
            if sha256_z_tar(archiwum, element) != wpis["sha256"]:
                blad(f"Niepoprawna suma SHA-256 {nazwa}")

        for nazwa, element in elementy_danych.items():
            docelowy = cel.joinpath(*pathlib.PurePosixPath(nazwa).parts)
            docelowy.parent.mkdir(parents=True, exist_ok=True)
            zrodlo = archiwum.extractfile(element)
            if zrodlo is None:
                blad(f"Nie można odczytać {nazwa}")
            with docelowy.open("wb") as wyjscie:
                while fragment := zrodlo.read(1024 * 1024):
                    wyjscie.write(fragment)
            docelowy.chmod(0o600)

    (cel / "archiwum-zdjec").mkdir(exist_ok=True)
    sys.stdout.write(
        f"OK: wersja {manifest_surowy.get('app_version', '?')}, "
        f"plików {len(manifest)}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pakiet", type=pathlib.Path)
    parser.add_argument("cel", type=pathlib.Path)
    args = parser.parse_args()
    try:
        zweryfikuj_i_rozpakuj(args.pakiet, args.cel)
    except (OSError, tarfile.TarError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"BŁĄD: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
