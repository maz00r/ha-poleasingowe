"""Wybór ekspertyzy z dokumentów aukcji — po TREŚCI, nie po nazwie (0.31.0).

Nazwy plików niczego nie przesądzają: dawro nazywa ekspertyzę numerem
rejestracyjnym (`RST41910.pdf`), Leasygroup — `zalacznik-67298.pdf`, a mLeasing
dołącza czasem pliki niezwiązane z autem (`…Kriolipoliza CRIO PREMIUM
OCENA.pdf`). Dlatego czytamy tekst i liczymy punkty za znaczniki raportu
rzeczoznawcy, które stały we WSZYSTKICH trzech zmierzonych próbkach
(dawro/Stellantis, Leasygroup, mLeasing/DEKRA, 2026-10-26).

Najmocniejszy sygnał to VIN tej aukcji w tekście — wtedy wiemy, że raport
dotyczy tego auta. Inny VIN to dyskwalifikacja: ekspertyza cudzego pojazdu
jest gorsza niż żadna, bo wygląda jak dowód.

**Raport autoDNA to osobny rodzaj dokumentu** (0.35.0). Mówi o historii auta,
nie o jego stanie, więc nie konkuruje z ekspertyzą o miejsce ani o limit
tekstu: wcześniej ekspertyza zjadała cały limit i raport nie docierał do
modelu. Przebiegi i szkody leżą w nim daleko od początku, stąd duży limit.
Zmierzone 2026-11-04 na trzech raportach z poleasingowe.pl: 9-18 tys. znaków,
VIN na każdej stronie, nazwa pliku `raport-autodna-<vin>-<skrót>.pdf`.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, replace

from pypdf import PdfReader
from pypdf.errors import PdfReadError

log = logging.getLogger(__name__)

ZNACZNIKI: tuple[str, ...] = (
    "rzeczoznaw",
    "ocena techniczna",
    "ocena stanu",
    "wycena",
    "ekspertyz",
    "dekra",
    "info-ekspert",
    "eurotax",
    "wskazanie drogomierza",
    "dane identyfikacyjne pojazdu",
    "dane pojazdu",
    "stan techniczny",
    "pierwszej rejestracji",
    "uszkodze",
)
PROG = 4
"""Minimum punktów bez potwierdzonego VIN-u. Regulamin czy wzór umowy
zbiera najwyżej jeden-dwa znaczniki (słowo „pojazd" nie jest znacznikiem)."""
PREMIA_VIN = 10
KARA_OBCY_VIN = -100
MAKS_STRON = 40
MAKS_ZNAKOW = 12_000
MAKS_ZNAKOW_HISTORII = 30_000

_TYTUL_HISTORII = re.compile(r"(?i)raport\s+historii\s+pojazdu\s+autodna")
_VIN_PRZY_ETYKIECIE = re.compile(
    r"\bVIN\b[\s:.]{0,8}((?:[A-HJ-NPR-Z0-9][ \t]?){17})(?![A-Z0-9])"
)


@dataclass(slots=True, frozen=True)
class OcenionyDokument:
    nazwa: str
    tekst: str
    punkty: int
    vin_zgodny: bool
    raport_historii: bool = False


def tekst_pdf(dane: bytes) -> str:
    """Warstwa tekstowa PDF-a; pusty napis, gdy jej nie ma albo plik jest zły."""
    try:
        czytnik = PdfReader(io.BytesIO(dane))
        strony = [strona.extract_text() or "" for strona in czytnik.pages[:MAKS_STRON]]
    except (PdfReadError, ValueError, TypeError, KeyError, OSError) as exc:
        log.warning("nie udało się odczytać PDF-a: %s", exc)
        return ""
    return re.sub(r"[ \t]+", " ", "\n".join(strony)).strip()


def _vin_w_tekscie(tekst: str) -> set[str]:
    return {
        re.sub(r"\s", "", trafienie.group(1)).upper()
        for trafienie in _VIN_PRZY_ETYKIECIE.finditer(tekst)
    }


def ocen_dokument(nazwa: str, tekst: str, vin: str | None) -> OcenionyDokument:
    male = tekst.lower()
    punkty = sum(1 for znacznik in ZNACZNIKI if znacznik in male)
    zgodny = False
    if vin:
        cel = vin.upper()
        znalezione = _vin_w_tekscie(tekst)
        bez_spacji = re.sub(r"\s", "", tekst).upper()
        if cel in znalezione or cel in bez_spacji:
            zgodny = True
            punkty += PREMIA_VIN
        elif znalezione:
            punkty += KARA_OBCY_VIN
    return OcenionyDokument(
        nazwa=nazwa,
        tekst=tekst,
        punkty=punkty,
        vin_zgodny=zgodny,
        raport_historii=_jest_raportem_historii(nazwa, tekst),
    )


def _jest_raportem_historii(nazwa: str, tekst: str) -> bool:
    """Po nazwie albo tytule: samo słowo „autoDNA" bywa i w ekspertyzie."""
    return "autodna" in nazwa.lower() or _TYTUL_HISTORII.search(tekst) is not None


def wybierz(
    pliki: list[tuple[str, str]], vin: str | None
) -> tuple[OcenionyDokument, ...]:
    """Ekspertyza i raport autoDNA tego auta, każde z własnym limitem tekstu.

    Ekspertyza to najlepiej oceniony dokument powyżej progu plus drugi o tym
    samym VIN-ie, razem do `MAKS_ZNAKOW` — model ma dostać raport, nie
    trzydzieści stron tabel wyposażenia.

    Raport autoDNA drukuje VIN na każdej stronie, więc przy znanym VIN-ie
    aukcji musi go zawierać: historia cudzego auta wyglądałaby jak dowód.
    """
    ocenione = sorted(
        (ocen_dokument(nazwa, tekst, vin) for nazwa, tekst in pliki if tekst),
        key=lambda d: d.punkty,
        reverse=True,
    )
    wynik = _ekspertyzy([d for d in ocenione if not d.raport_historii])
    for dokument in ocenione:
        if dokument.raport_historii and (dokument.vin_zgodny or not vin):
            wynik.append(replace(dokument, tekst=dokument.tekst[:MAKS_ZNAKOW_HISTORII]))
            break
    return tuple(wynik)


def _ekspertyzy(ocenione: list[OcenionyDokument]) -> list[OcenionyDokument]:
    wybrane = [d for d in ocenione if d.punkty >= PROG]
    if not wybrane:
        return []
    wynik = [wybrane[0]]
    if len(wybrane) > 1 and wybrane[1].vin_zgodny:
        wynik.append(wybrane[1])
    zostalo = MAKS_ZNAKOW
    przyciete: list[OcenionyDokument] = []
    for dokument in wynik:
        tekst = dokument.tekst[:zostalo]
        zostalo -= len(tekst)
        przyciete.append(replace(dokument, tekst=tekst))
        if zostalo <= 0:
            break
    return przyciete
