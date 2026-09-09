"""Własny backup bazy (SPEC.md §7.1).

DLACZEGO WŁASNY, SKORO HOME ASSISTANT MA SNAPSHOTY. Snapshot dodatku
PostgreSQL obejmuje **obie bazy naraz** — naszą i TeslaMate. Odtworzenie
z niego samej bazy `poleasingowe` cofnęłoby też TeslaMate, czyli dane,
których nie da się odtworzyć z żadnego innego źródła (§2). Dlatego zrzucamy
wyłącznie swoją bazę i nigdy nie wołamy `pg_dumpall`.

`-Fc` (format custom), bo pozwala odtworzyć pojedynczą tabelę i kompresuje
w locie. Hasło idzie przez `PGPASSWORD` w środowisku procesu potomnego,
nigdy w linii poleceń — ta jest widoczna w `ps` dla każdego w kontenerze
(§10.2).

Stanu „kiedy ostatnio" nie trzymamy nigdzie osobno: rozstrzyga o tym czas
modyfikacji najnowszego pliku w katalogu kopii. Znacznik w bazie albo w pliku
obok mógłby się rozjechać z rzeczywistością — plik nie może.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import pathlib
from dataclasses import dataclass

from psycopg.conninfo import conninfo_to_dict

log = logging.getLogger(__name__)

KATALOG = pathlib.Path("/share/poleasingowe/backup")
ILE_TRZYMAC = 7
"""SPEC.md §7.1 — siedem kopii. Tydzień wstecz przy dobowym rytmie."""
CO_ILE_S = 24 * 3600
LIMIT_CZASU_S = 900.0
"""Sufit na jeden `pg_dump`. Baza ma kilkanaście MB (§1.1), więc kwadrans to
i tak bardzo dużo — ale wisząca w nieskończoność kopia blokowałaby pętlę."""

WZORZEC = "poleasingowe-*.dump"


class BladKopii(RuntimeError):
    """`pg_dump` nie powiódł się. Treść zawiera jego własny komunikat."""


@dataclass(slots=True, frozen=True)
class Kopia:
    """Jeden plik kopii — tyle, ile trzeba pokazać w diagnostyce (§12)."""

    sciezka: pathlib.Path
    utworzono: dt.datetime
    bajtow: int


class KopiaZapasowa:
    """Dobowy `pg_dump -Fc` własnej bazy do `/share` (SPEC.md §7.1)."""

    def __init__(
        self,
        dsn: str,
        *,
        katalog: pathlib.Path = KATALOG,
        ile_trzymac: int = ILE_TRZYMAC,
        limit_czasu_s: float = LIMIT_CZASU_S,
    ) -> None:
        self._dsn = dsn
        self._katalog = katalog
        self._ile_trzymac = ile_trzymac
        self._limit = limit_czasu_s
        self._ostatni_blad: str | None = None

    @property
    def ostatni_blad(self) -> str | None:
        """Ostatni błąd zadania kopii, do pokazania operatorowi w diagnostyce."""
        return self._ostatni_blad

    def odnotuj_blad(self, blad: str | None) -> None:
        self._ostatni_blad = blad

    def _srodowisko(self) -> dict[str, str]:
        """Parametry połączenia dla `pg_dump` w postaci zmiennych PG*."""
        pola = conninfo_to_dict(self._dsn)
        srodowisko = {
            "PGHOST": str(pola.get("host", "")),
            "PGPORT": str(pola.get("port", "") or "5432"),
            "PGUSER": str(pola.get("user", "")),
            "PGDATABASE": str(pola.get("dbname", "")),
            "PGCONNECT_TIMEOUT": "10",
        }
        haslo = pola.get("password")
        if haslo:
            srodowisko["PGPASSWORD"] = str(haslo)
        return {k: v for k, v in srodowisko.items() if v}

    def ostatnia(self) -> Kopia | None:
        """Najnowsza kopia albo `None`. Źródłem prawdy jest katalog."""
        kopie = self._kopie()
        return kopie[0] if kopie else None

    def czas_na_kopie(self, teraz: dt.datetime) -> bool:
        """Czy minęła doba od ostatniej. Brak kopii znaczy „najwyższy czas"."""
        ostatnia = self.ostatnia()
        if ostatnia is None:
            return True
        return (teraz - ostatnia.utworzono).total_seconds() >= CO_ILE_S

    async def wykonaj(self, teraz: dt.datetime) -> Kopia:
        """Robi kopię i kasuje najstarsze ponad limit. Zwraca nową kopię."""
        self._katalog.mkdir(parents=True, exist_ok=True)
        cel = self._katalog / f"poleasingowe-{teraz:%Y%m%d-%H%M%S}.dump"
        # Plik tymczasowy: przerwana kopia nie ma prawa wyglądać jak dobra.
        tymczasowy = cel.with_suffix(".dump.czesciowy")

        proces = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(tymczasowy),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Parametry połączenia — RAZEM Z HASŁEM — idą środowiskiem, nie
            # argumentami. Linia poleceń procesu jest widoczna w `ps` dla
            # wszystkiego, co biegnie w kontenerze (SPEC.md §10.2).
            env={**os.environ, **self._srodowisko()},
        )
        try:
            _, blad = await asyncio.wait_for(proces.communicate(), self._limit)
        except TimeoutError:
            await self._przerwij(proces, tymczasowy)
            raise BladKopii(f"pg_dump przekroczył {self._limit:.0f} s") from None
        except asyncio.CancelledError:
            # SIGTERM add-onu anuluje zadanie kopii. Bez oczekiwania na proces
            # potomny mógłby on dalej pisać po zatrzymaniu dispatchera.
            await self._przerwij(proces, tymczasowy)
            raise

        if proces.returncode != 0:
            tymczasowy.unlink(missing_ok=True)
            raise BladKopii(
                blad.decode("utf-8", "replace").strip() or "pg_dump zwrócił błąd"
            )

        tymczasowy.replace(cel)
        self._posprzataj()
        kopia = Kopia(cel, teraz, cel.stat().st_size)
        log.info("kopia bazy: %s (%s B)", cel.name, kopia.bajtow)
        return kopia

    @staticmethod
    async def _przerwij(
        proces: asyncio.subprocess.Process, tymczasowy: pathlib.Path
    ) -> None:
        if proces.returncode is None:
            proces.terminate()
            try:
                await asyncio.wait_for(proces.wait(), timeout=5.0)
            except TimeoutError:
                proces.kill()
                await proces.wait()
        tymczasowy.unlink(missing_ok=True)

    def _kopie(self) -> list[Kopia]:
        try:
            pliki = sorted(
                self._katalog.glob(WZORZEC),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            # Katalog na `/share` bywa niedostępny — brak kopii to stan,
            # nie awaria dodatku.
            return []
        return [
            Kopia(
                p,
                dt.datetime.fromtimestamp(p.stat().st_mtime, dt.UTC),
                p.stat().st_size,
            )
            for p in pliki
        ]

    def _posprzataj(self) -> None:
        """Zostawia `ile_trzymac` najnowszych kopii, kasuje resztę."""
        for stara in self._kopie()[self._ile_trzymac :]:
            try:
                stara.sciezka.unlink()
                log.info("skasowano starą kopię %s", stara.sciezka.name)
            except OSError as exc:
                log.warning("nie udało się skasować %s: %s", stara.sciezka, exc)
