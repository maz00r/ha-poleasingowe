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

**Raporty historii to osobny rodzaj dokumentu** (0.35.0). Mówią o przeszłości
auta, nie o jego stanie, więc nie konkurują z ekspertyzą o miejsce ani o limit
tekstu: wcześniej ekspertyza zjadała cały limit i raport nie docierał do
modelu. Przebiegi i szkody leżą w nich daleko od początku, stąd duży limit.
Zmierzone 2026-11-04:

- autoDNA, trzy raporty z poleasingowe.pl: 9-18 tys. znaków, VIN na każdej
  stronie, nazwa pliku `raport-autodna-<vin>-<skrót>.pdf`;
- rządowa usługa Historia pojazdu, 29 raportów z autoprzetargu (0.35.2):
  2,6-8,5 tys. znaków, zawsze z VIN-em aukcji, a nazwa pliku bywa różna.

Oba źródła się uzupełniają, więc aukcja z dwoma raportami przekazuje oba.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import re
from collections.abc import Sequence
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from dataclasses import dataclass, replace

import pypdfium2  # type: ignore[import-untyped]

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

_TYTUL_HISTORII = re.compile(
    r"(?i)raport\s+historii\s+pojazdu\s+autodna"
    r"|raport\s+z\s+usługi\s+historia\s+pojazdu"
)
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
    """Warstwa tekstowa PDF-a; pusty napis, gdy jej nie ma albo plik jest zły.

    Funkcja na szczycie modułu, nie zamknięcie — `czytaj_pdfy` przekazuje ją
    do osobnego procesu, a to wymaga, żeby dało się ją zaimportować po nazwie.
    """
    try:
        dokument = pypdfium2.PdfDocument(dane)
        try:
            strony = [
                dokument[numer].get_textpage().get_text_bounded()
                for numer in range(min(len(dokument), MAKS_STRON))
            ]
        finally:
            dokument.close()
    except (pypdfium2.PdfiumError, ValueError, OSError) as exc:
        log.warning("nie udało się odczytać PDF-a: %s", exc)
        return ""
    # PDFium kończy wiersze znakami CRLF, a reguły wyboru dokumentu widzą LF.
    return re.sub(r"[ \t]+", " ", "\n".join(strony).replace("\r\n", "\n")).strip()


async def czytaj_pdfy(dane: Sequence[bytes]) -> list[str]:
    """Jak `tekst_pdf`, ale poza wątkiem strony — w osobnym procesie.

    Sama szybkość PDFium (0.38.1) by tego nie wymagała, ale to kod natywny
    czytający obce pliki. Jego awaria ma zabić proces pomocniczy, a nie całą
    aplikację: taki dokument jest wtedy pomijany, a wycena idzie dalej. PDFium
    nie jest też bezpieczne wątkowo, więc `max_workers=1` zostaje.

    Proces startuje i kończy się z KAŻDĄ wyceną, a nie żyje między nimi —
    stały proces złamałby budżet RSS w spoczynku (< 170 MB, SPEC.md §1.1).
    `spawn`, nie `fork`: w procesie są już wątki (`asyncio.to_thread` gdzie
    indziej), a fork wielowątkowego procesu bywa niebezpieczny.
    """
    if not dane:
        return []
    petla = asyncio.get_running_loop()
    pula = ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn")
    )
    try:
        wyniki = await asyncio.gather(
            *(petla.run_in_executor(pula, tekst_pdf, plik) for plik in dane),
            return_exceptions=True,
        )
    finally:
        # `shutdown` czeka na zejście procesu — w wątku puli domyślnej,
        # żeby nie blokować pętli zdarzeń strony.
        await petla.run_in_executor(None, pula.shutdown)
    teksty: list[str] = []
    for wynik in wyniki:
        if isinstance(wynik, BrokenExecutor):
            log.warning("proces czytający PDF-y padł, pomijam dokument: %s", wynik)
            teksty.append("")
        elif isinstance(wynik, BaseException):
            raise wynik
        else:
            teksty.append(wynik)
    return teksty


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
    """Po nazwie albo tytule: samo słowo „autoDNA" bywa i w ekspertyzie,
    a raport rządowy wymienia je w przypisie o danych zagranicznych."""
    return "autodna" in nazwa.lower() or _TYTUL_HISTORII.search(tekst) is not None


def wybierz(
    pliki: list[tuple[str, str]], vin: str | None
) -> tuple[OcenionyDokument, ...]:
    """Ekspertyza i raporty historii tego auta, każde z własnym limitem tekstu.

    Ekspertyza to najlepiej oceniony dokument powyżej progu plus drugi o tym
    samym VIN-ie, razem do `MAKS_ZNAKOW` — model ma dostać raport, nie
    trzydzieści stron tabel wyposażenia.

    Raport historii zawsze podaje VIN, więc przy znanym VIN-ie aukcji musi go
    zawierać: historia cudzego auta wyglądałaby jak dowód. Raporty dzielą jeden
    limit, a rządowy mieści się w nim obok najdłuższego autoDNA.
    """
    ocenione = sorted(
        (ocen_dokument(nazwa, tekst, vin) for nazwa, tekst in pliki if tekst),
        key=lambda d: d.punkty,
        reverse=True,
    )
    wynik = _ekspertyzy([d for d in ocenione if not d.raport_historii])
    zostalo = MAKS_ZNAKOW_HISTORII
    for dokument in ocenione:
        if (
            zostalo > 0
            and dokument.raport_historii
            and (dokument.vin_zgodny or not vin)
        ):
            tekst = dokument.tekst[:zostalo]
            zostalo -= len(tekst)
            wynik.append(replace(dokument, tekst=tekst))
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
