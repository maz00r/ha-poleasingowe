from __future__ import annotations

import logging
import pathlib

from app.infrastructure import dziennik
from app.infrastructure.redakcja import Redakcja

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def test_plik_logu_filtr_poziomu_traceback_i_redakcja(tmp_path: pathlib.Path) -> None:
    (tmp_path / "aplikacja.log.1").write_text(
        "2026-10-26 10:00:00,000 INFO stare: z poprzedniej kopii\n", encoding="utf-8"
    )
    (tmp_path / "aplikacja.log").write_text(
        "2026-10-26 10:00:01,000 INFO a: zwykły wpis\n"
        "2026-10-26 10:00:02,000 ERROR b: padło hasło tajne123\n"
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1\n'
        "2026-10-26 10:00:03,000 DEBUG c: szczegóły\n",
        encoding="utf-8",
    )

    wszystko = dziennik.ostatnie(minimalny="DEBUG", katalog=tmp_path)
    assert wszystko[0].endswith("z poprzedniej kopii") and len(wszystko) == 6

    bledy = dziennik.ostatnie(
        minimalny="ERROR", katalog=tmp_path, redakcja=Redakcja(["tajne123"])
    )
    assert len(bledy) == 3 and "tajne123" not in bledy[0]
    assert bledy[1].startswith("Traceback")

    assert dziennik.ostatnie(minimalny="INFO", linie=2, katalog=tmp_path)[
        -1
    ].startswith("  File")


def test_brak_katalogu_to_cisza(tmp_path: pathlib.Path) -> None:
    assert dziennik.ostatnie(katalog=tmp_path / "nie-ma") == []


def test_dodaj_plik_zapisuje_po_filtrze(tmp_path: pathlib.Path) -> None:
    class Zaslon(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.msg = str(record.msg).replace("sekret", "***")
            return True

    sciezka = dziennik.dodaj_plik(FORMAT, Zaslon(), katalog=tmp_path)
    assert sciezka == tmp_path / "aplikacja.log"
    korzen = logging.getLogger()
    uchwyt = korzen.handlers[-1]
    poprzedni = korzen.level
    korzen.setLevel(logging.INFO)
    try:
        logging.getLogger("test").info("to jest sekret")
        uchwyt.flush()
    finally:
        korzen.removeHandler(uchwyt)
        uchwyt.close()
        korzen.setLevel(poprzedni)
    linie = dziennik.ostatnie(katalog=tmp_path)
    assert len(linie) == 1 and linie[0].endswith("to jest ***")
