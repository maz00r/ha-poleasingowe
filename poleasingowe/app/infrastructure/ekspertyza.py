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
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

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

_VIN_PRZY_ETYKIECIE = re.compile(
    r"\bVIN\b[\s:.]{0,8}((?:[A-HJ-NPR-Z0-9][ \t]?){17})(?![A-Z0-9])"
)


@dataclass(slots=True, frozen=True)
class OcenionyDokument:
    nazwa: str
    tekst: str
    punkty: int
    vin_zgodny: bool


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
    return OcenionyDokument(nazwa=nazwa, tekst=tekst, punkty=punkty, vin_zgodny=zgodny)


def wybierz(
    pliki: list[tuple[str, str]], vin: str | None
) -> tuple[OcenionyDokument, ...]:
    """Najlepiej oceniony dokument powyżej progu, plus drugi o tym samym VIN-ie.

    Łączna długość tekstu ograniczona do `MAKS_ZNAKOW` — model ma dostać
    raport, nie trzydzieści stron tabel wyposażenia.
    """
    ocenione = sorted(
        (ocen_dokument(nazwa, tekst, vin) for nazwa, tekst in pliki if tekst),
        key=lambda d: d.punkty,
        reverse=True,
    )
    wybrane = [d for d in ocenione if d.punkty >= PROG]
    if not wybrane:
        return ()
    wynik = [wybrane[0]]
    if len(wybrane) > 1 and wybrane[1].vin_zgodny:
        wynik.append(wybrane[1])
    zostalo = MAKS_ZNAKOW
    przyciete: list[OcenionyDokument] = []
    for dokument in wynik:
        tekst = dokument.tekst[:zostalo]
        zostalo -= len(tekst)
        przyciete.append(
            OcenionyDokument(
                dokument.nazwa, tekst, dokument.punkty, dokument.vin_zgodny
            )
        )
        if zostalo <= 0:
            break
    return tuple(przyciete)


_TABLICA = re.compile(
    r"(?i)(nr\.?\s*rej(?:estracyjny)?\.?\s*:?\s*)((?:[A-Z0-9][ \t]?){4,9})"
)
_RZECZOZNAWCA = re.compile(r"(?im)^(\s*rzeczoznawca\s*:?)[^\n]*(\n[^\n]*)?")
# Polska tablica: 2-3 litery wyróżnika i 4-5 znaków, w tym co najmniej
# jedna cyfra. Czasem złapie kod modelu — to tania pomyłka w tę stronę.
_TABLICA_DOWOLNA = re.compile(
    r"(?<![A-Z0-9])[A-Z]{2,3} ?(?=[A-Z]*\d)[0-9A-Z]{4,5}(?![A-Z0-9])"
)
# Nazwiska rzeczoznawców stoją przy etykietach i w stopce każdej strony
# („/ SRTSiRD 11013 Albert Kalużny System INFO-EKSPERT"). Zbieramy je stamtąd
# i zasłaniamy WSZYSTKIE wystąpienia — warstwa tekstowa rozrzuca podpis
# daleko od etykiety.
_IMIE_NAZWISKO = r"([A-ZŁŚŻŹĆŃÓĘĄ][a-ząćęłńóśżź]+[ \t]+[A-ZŁŚŻŹĆŃÓĘĄ][a-ząćęłńóśżź-]+)"
_OSOBA_PRZY_ETYKIECIE = re.compile(
    r"(?i:rzeczoznawca|ogl[eę]dziny wykona[lł]|wycen[aę] wykona(?:[lł]a?|na)|"
    r"SRTSiRD[ \t]+\d+)[ \t]*:?[ \t]*\n?[ \t]*" + _IMIE_NAZWISKO
)
_TELEFON = re.compile(r"(?<!\d)(?:\+?48[ \-]?)?(?:\d[ \-]?){9}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def zredaguj(tekst: str, vin: str | None) -> str:
    """Zdejmuje z tekstu identyfikatory, zanim trafi do dostawcy modelu.

    Wycena z założenia nie wysyła VIN-u ani danych osób (`wycena_ai.py`).
    Ekspertyza niesie VIN (czasem z odstępami między znakami), numer
    rejestracyjny, nazwisko rzeczoznawcy, telefony i adresy e-mail — model
    potrzebuje stanu auta i wartości, nie tego.
    """
    osoby = {m.group(1) for m in _OSOBA_PRZY_ETYKIECIE.finditer(tekst)}
    for osoba in sorted(osoby, key=len, reverse=True):
        tekst = tekst.replace(osoba, "[osoba]")
    if vin:
        wzor_vin = r"\s?".join(re.escape(znak) for znak in vin.upper())
        tekst = re.sub(wzor_vin, "[VIN]", tekst, flags=re.I)
    tekst = _VIN_PRZY_ETYKIECIE.sub("VIN: [VIN]", tekst)
    tekst = _TABLICA.sub(r"\1[nr rej.]", tekst)
    tekst = _TABLICA_DOWOLNA.sub("[nr rej.]", tekst)
    tekst = _RZECZOZNAWCA.sub(r"\1 [rzeczoznawca]", tekst)
    tekst = _EMAIL.sub("[e-mail]", tekst)
    return _TELEFON.sub("[telefon]", tekst)
