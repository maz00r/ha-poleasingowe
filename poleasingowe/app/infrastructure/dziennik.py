"""Log aplikacji na dysku i jego podgląd w Diagnostyce (0.32.0).

Aplikacja loguje na stdout (Docker/Supervisor), ale z wnętrza kontenera tego
strumienia nie widać, a `docker compose up` przy deployu go kasuje. Kopia
w `/data/logi/aplikacja.log` (rotacja 2 MB x 5) przeżywa restart i deploy
i da się ją pokazać w panelu.

Do pliku trafia to samo, co na stdout — po tym samym filtrze redakcji
sekretów. Odczyt w panelu redaguje jeszcze raz (`Redakcja`), na wypadek
wpisów sprzed dopisania sekretu do listy.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import pathlib
from collections import deque

from app.infrastructure.redakcja import Redakcja

KATALOG = pathlib.Path(os.environ.get("POLEASINGOWE_LOGI", "/data/logi"))
PLIK = "aplikacja.log"
ROZMIAR = 2 * 1024 * 1024
KOPIE = 5
POZIOMY = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def dodaj_plik(
    format_logu: str, filtr: logging.Filter, katalog: pathlib.Path | None = None
) -> pathlib.Path | None:
    """Podpina rotowany plik do głównego loggera. `None`, gdy katalog
    niedostępny (np. testy, uruchomienie bez `/data`) — wtedy tylko stdout."""
    katalog = katalog or KATALOG
    try:
        katalog.mkdir(parents=True, exist_ok=True)
        uchwyt = logging.handlers.RotatingFileHandler(
            katalog / PLIK, maxBytes=ROZMIAR, backupCount=KOPIE, encoding="utf-8"
        )
    except OSError:
        return None
    uchwyt.setFormatter(logging.Formatter(format_logu))
    uchwyt.addFilter(filtr)
    logging.getLogger().addHandler(uchwyt)
    return katalog / PLIK


def _poziom(linia: str) -> str | None:
    czesci = linia.split(maxsplit=3)
    return czesci[2] if len(czesci) >= 3 and czesci[2] in POZIOMY else None


def ostatnie(
    *,
    minimalny: str = "INFO",
    linie: int = 300,
    katalog: pathlib.Path | None = None,
    redakcja: Redakcja | None = None,
) -> list[str]:
    """Ostatnie wpisy od poziomu `minimalny` wzwyż, najnowsze na końcu.

    Linie kontynuacji (np. traceback) idą razem z wpisem, do którego należą.
    Czyta bieżący plik i — gdy to za mało — poprzednią kopię rotacji.
    """
    katalog = katalog or KATALOG
    prog = POZIOMY.index(minimalny) if minimalny in POZIOMY else 1
    pliki = [katalog / f"{PLIK}.1", katalog / PLIK]
    wynik: deque[str] = deque(maxlen=linie)
    biezacy_poziom: str | None = None
    for plik in pliki:
        try:
            with plik.open(encoding="utf-8", errors="replace") as uchwyt:
                for linia in uchwyt:
                    linia = linia.rstrip("\n")
                    poziom = _poziom(linia)
                    if poziom is not None:
                        biezacy_poziom = poziom
                    if (
                        biezacy_poziom is not None
                        and POZIOMY.index(biezacy_poziom) >= prog
                    ):
                        wynik.append(linia)
        except OSError:
            continue
    redaktor = redakcja or Redakcja()
    return [redaktor.zastosuj(linia) for linia in wynik]
