"""Trwałe ciasteczka sesji na dysku (SPEC.md §10.2).

`/data/sessions/<source>.json`, uprawnienia **0600**, wczytywane przy
starcie. Restart add-onu nie powoduje ponownego logowania — a każde zbędne
logowanie to kolejna próba na imiennym koncie, przy twardym limicie trzech.

Plik z ciasteczkami jest poświadczeniem: kto go ma, ten jest zalogowany na
moje konto. Stąd 0600 i katalog 0700, ustawiane **przed** zapisem treści,
nie po nim.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import tempfile
from collections.abc import Sequence

import httpx

from app.application.ports import Ciastko

log = logging.getLogger(__name__)

KATALOG_SESJI = pathlib.Path("/data/sessions")
UPRAWNIENIA_PLIKU = 0o600
UPRAWNIENIA_KATALOGU = 0o700


def _bezpieczna_nazwa(source_key: str) -> str:
    """Klucz źródła jako nazwa pliku — bez wychodzenia poza katalog.

    Klucze pochodzą z opcji add-onu, czyli spoza kodu. `../../etc/passwd`
    jako `key` nie ma prawa nic zapisać poza `/data/sessions`.
    """
    czyste = "".join(z for z in source_key if z.isalnum() or z in "-_")
    if not czyste:
        raise ValueError(f"klucz źródła nie nadaje się na nazwę pliku: {source_key!r}")
    return czyste


class PlikowyMagazynSesji:
    """Magazyn sesji w plikach JSON."""

    def __init__(self, katalog: pathlib.Path = KATALOG_SESJI) -> None:
        self._katalog = katalog

    def _sciezka(self, source_key: str) -> pathlib.Path:
        return self._katalog / f"{_bezpieczna_nazwa(source_key)}.json"

    def wczytaj(self, source_key: str) -> tuple[Ciastko, ...]:
        """Wczytuje sesję. Uszkodzony plik znaczy „brak sesji", nie awarię.

        Zepsuty JSON kończy się ponownym zalogowaniem, czyli jedną próbą —
        wywalenie się procesu kończyłoby się pętlą restartów, czego zabrania
        §2 pkt 8.
        """
        sciezka = self._sciezka(source_key)
        try:
            surowe = sciezka.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ()
        except OSError as exc:
            log.warning("nie mogę odczytać sesji %s: %s", source_key, exc)
            return ()

        try:
            dane = json.loads(surowe)
            return tuple(
                Ciastko(
                    nazwa=c["nazwa"],
                    wartosc=c["wartosc"],
                    domena=c.get("domena", ""),
                    sciezka=c.get("sciezka", "/"),
                    wygasa=c.get("wygasa"),
                )
                for c in dane
            )
        except (ValueError, TypeError, KeyError) as exc:
            log.warning(
                "plik sesji %s jest uszkodzony (%s) — loguję się od nowa",
                source_key,
                exc,
            )
            return ()

    def zapisz(self, source_key: str, ciastka: Sequence[Ciastko]) -> None:
        """Zapis atomowy: plik tymczasowy, uprawnienia, dopiero potem `rename`.

        Bez tego przerwany zapis zostawia obcięty JSON, a przy okazji istnieje
        okno, w którym plik z ciasteczkami ma uprawnienia domyślne. Uprawnienia
        ustawiamy **przed** wpisaniem treści.
        """
        self._katalog.mkdir(parents=True, exist_ok=True)
        self._katalog.chmod(UPRAWNIENIA_KATALOGU)
        docelowy = self._sciezka(source_key)

        uchwyt, tymczasowy = tempfile.mkstemp(dir=self._katalog, suffix=".tmp")
        try:
            os.fchmod(uchwyt, UPRAWNIENIA_PLIKU)
            with os.fdopen(uchwyt, "w", encoding="utf-8") as plik:
                json.dump(
                    [
                        {
                            "nazwa": c.nazwa,
                            "wartosc": c.wartosc,
                            "domena": c.domena,
                            "sciezka": c.sciezka,
                            "wygasa": c.wygasa,
                        }
                        for c in ciastka
                    ],
                    plik,
                )
            pathlib.Path(tymczasowy).replace(docelowy)
        except BaseException:
            pathlib.Path(tymczasowy).unlink(missing_ok=True)
            raise

    def usun(self, source_key: str) -> None:
        self._sciezka(source_key).unlink(missing_ok=True)


def z_httpx(ciasteczka: httpx.Cookies) -> tuple[Ciastko, ...]:
    """Przekłada cookie jar `httpx` na postać niezależną od sterownika."""
    return tuple(
        Ciastko(
            nazwa=c.name,
            wartosc=c.value or "",
            domena=c.domain,
            sciezka=c.path,
            wygasa=c.expires,
        )
        for c in ciasteczka.jar
    )


def do_httpx(ciastka: Sequence[Ciastko]) -> httpx.Cookies:
    """Odtwarza cookie jar `httpx` z zapisanej sesji."""
    ciasteczka = httpx.Cookies()
    for c in ciastka:
        ciasteczka.set(c.nazwa, c.wartosc, domain=c.domena, path=c.sciezka)
    return ciasteczka
