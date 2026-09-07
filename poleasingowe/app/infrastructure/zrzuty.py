"""Zrzuty diagnostyczne przy błędzie parsowania (SPEC.md §10.2, §1.1).

Trzy reguły, wszystkie ze spec i wszystkie egzekwowane tutaj, a nie
dyscypliną wywołującego:

1. **Domyślnie wyłączone.** Włącza je opcja `debug_dumps`.
2. **Tylko przy błędzie parsowania.** Nie „przy każdym odpycie na DEBUG" —
   strona po zalogowaniu zawiera moje dane osobowe (§10.2).
3. **Przez ten sam filtr redakcji co logi**, z rotacją i twardym limitem
   rozmiaru katalogu (§1.1: katalog debug w `/data` < 200 MB).

Redakcja jest tu obowiązkowa, nie opcjonalna: zrzut to surowy HTML strony,
na której widnieje mój login, a przy okazji loginy osób trzecich —
zwycięzca w poleasingowe.pl, uczestnicy w autoprzetarg.pl (RECON.md §4.2,
§4.4). Tych ostatnich nie chroni żadne hasło, więc muszą wypaść.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import pathlib
import tempfile

from app.infrastructure.redakcja import Redakcja

log = logging.getLogger(__name__)

KATALOG_ZRZUTOW = pathlib.Path("/data/debug")
LIMIT_KATALOGU_BAJTY = 200 * 1024 * 1024
"""SPEC.md §1.1 — katalog debug w `/data` poniżej 200 MB, z rotacją."""
LIMIT_PLIKU_BAJTY = 2 * 1024 * 1024
"""Strony źródeł mają ~300 kB. Dwa megabajty to zapas, nie norma."""


class ZrzutyDebug:
    """Zapisuje zredagowane zrzuty stron przy błędach parsowania."""

    def __init__(
        self,
        redakcja: Redakcja,
        *,
        wlaczone: bool = False,
        katalog: pathlib.Path = KATALOG_ZRZUTOW,
        limit_katalogu: int = LIMIT_KATALOGU_BAJTY,
        limit_pliku: int = LIMIT_PLIKU_BAJTY,
    ) -> None:
        self._redakcja = redakcja
        self._wlaczone = wlaczone
        self._katalog = katalog
        self._limit_katalogu = limit_katalogu
        self._limit_pliku = limit_pliku

    @property
    def wlaczone(self) -> bool:
        return self._wlaczone

    def zapisz(
        self, source_key: str, identyfikator: str, tresc: str, powod: str
    ) -> pathlib.Path | None:
        """Zapisuje zrzut. Zwraca `None`, gdy zrzuty są wyłączone.

        Błąd zapisu **nie propaguje**: zrzut to pomoc w diagnozie, a nie
        zadanie add-onu. Pełny dysk ma skończyć się ostrzeżeniem w logu,
        a nie przerwaniem odpytu, który poza tym się udał.
        """
        if not self._wlaczone:
            return None
        try:
            return self._zapisz(source_key, identyfikator, tresc, powod)
        except OSError as exc:
            log.warning(
                "nie udało się zapisać zrzutu %s/%s: %s", source_key, identyfikator, exc
            )
            return None

    def _zapisz(
        self, source_key: str, identyfikator: str, tresc: str, powod: str
    ) -> pathlib.Path:
        self._katalog.mkdir(parents=True, exist_ok=True)
        znacznik = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        bezpieczny = (
            "".join(z for z in identyfikator if z.isalnum() or z in "-_") or "brak"
        )
        sciezka = self._katalog / f"{source_key}-{bezpieczny}-{znacznik}.html"

        naglowek = f"<!-- {powod} -->\n"
        zredagowane = self._redakcja.zastosuj(tresc)
        dane = (naglowek + zredagowane).encode("utf-8")[: self._limit_pliku]

        # Zapis atomowy: obciety zrzut jest gorszy niz brak zrzutu, bo
        # wyglada na dowod, ze strona faktycznie tak wygladala.
        uchwyt, tymczasowy = tempfile.mkstemp(dir=self._katalog, suffix=".tmp")
        try:
            with os.fdopen(uchwyt, "wb") as plik:
                plik.write(dane)
            pathlib.Path(tymczasowy).replace(sciezka)
        except BaseException:
            pathlib.Path(tymczasowy).unlink(missing_ok=True)
            raise

        self._obetnij_katalog()
        log.info("zrzut diagnostyczny: %s (%s)", sciezka.name, powod)
        return sciezka

    def _obetnij_katalog(self) -> None:
        """Kasuje najstarsze zrzuty, aż katalog zmieści się w limicie.

        Kasujemy od najstarszych, bo diagnozuje się to, co zepsuło się teraz.
        Limit dotyczy katalogu, nie liczby plików: to rozmiar jest budżetem
        z §1.1, a zrzuty bywają bardzo różnej wielkości.
        """
        pliki = sorted(
            (p for p in self._katalog.glob("*.html") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        laczny = sum(p.stat().st_size for p in pliki)
        for plik in pliki:
            if laczny <= self._limit_katalogu:
                return
            rozmiar = plik.stat().st_size
            plik.unlink(missing_ok=True)
            laczny -= rozmiar
            log.info("rotacja zrzutów: skasowano %s", plik.name)
