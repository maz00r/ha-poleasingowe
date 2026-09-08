"""Rozpoznanie rodzaju pojazdu (SPEC.md §6.2, §12).

Trzeci słownik obok `marki.py` i `paliwa.py`, z tego samego powodu: serwisy
mówią o tej samej rzeczy różnymi słowami, a interfejs ma pokazywać jedną.
Tutaj stawka jest wyższa niż przy paliwie — bez rodzaju pojazdu lista miesza
samochody osobowe z naczepami i ciągnikami siodłowymi, a filtr po marce nie
pomaga, bo naczepa też ma markę.

**Każde źródło mówi to inaczej** (zmierzone w `fixtures/`, 2026-09-08):

| Serwis | Skąd wiadomo | Przykłady |
|---|---|---|
| autoprzetarg.pl | kategoria w adresie | `Samochody-osobowe`, `Motocykle` |
| aukcje.efl.com.pl | pole `Rodzaj pojazdu` | `osobowy`, `Samochód osobowy` |
| poleasingowe.pl | **tylko nazwa** | `MAN TGX CIĄGNIK SIODŁOWY` |

Stąd dwa wejścia, w tej kolejności ważności:

1. `z_kategorii` — deklaracja źródła. Pewna, więc ma pierwszeństwo.
2. `z_nazwy` — słowa nadwozia w tytule. Dla poleasingowe jedyne wyjście:
   serwis wrzuca do kategorii `vehicles` osobowe, dostawcze i ciężarowe
   razem, a `Typ` (nadwozie) jest wyłącznie na stronie szczegółów, której
   dla większości aukcji nigdy nie pobieramy (§11.2).

Czego tu nie ma: zgadywania po masie, liczbie miejsc czy mocy. Nierozpoznane
zostaje `NIEZNANY`, bo filtr, który po cichu chowa auta, jest gorszy niż
filtr, który czasem pokaże za dużo.
"""

from __future__ import annotations

import re

from app.domain.enums import RodzajPojazdu

# Kategorie deklarowane przez serwisy. Klucz jest znormalizowany
# (`_klucz`), więc `Samochody-ciężarowe`, `SAMOCHODY CIEZAROWE`
# i `samochody_ciezarowe` trafiają w ten sam wpis.
KATEGORIE: dict[str, RodzajPojazdu] = {
    # autoprzetarg.pl — segment kategorii w adresie aukcji
    "samochody osobowe": RodzajPojazdu.OSOBOWY,
    "samochody dostawcze": RodzajPojazdu.DOSTAWCZY,
    "samochody ciezarowe": RodzajPojazdu.CIEZAROWY,
    "naczepy i przyczepy": RodzajPojazdu.PRZYCZEPA,
    "motocykle": RodzajPojazdu.MOTOCYKL,
    "autobusy": RodzajPojazdu.AUTOBUS,
    "maszyny": RodzajPojazdu.INNY,
    "inne": RodzajPojazdu.INNY,
    # aukcje.efl.com.pl — pole `Rodzaj pojazdu`, trzy warianty zapisu
    # tej samej rzeczy w jednej liście
    "osobowy": RodzajPojazdu.OSOBOWY,
    "samochod osobowy": RodzajPojazdu.OSOBOWY,
    "ciezarowy": RodzajPojazdu.CIEZAROWY,
    "samochod ciezarowy": RodzajPojazdu.CIEZAROWY,
    "dostawczy": RodzajPojazdu.DOSTAWCZY,
    "samochod dostawczy": RodzajPojazdu.DOSTAWCZY,
    "ciagnik siodlowy": RodzajPojazdu.CIEZAROWY,
    "przyczepa": RodzajPojazdu.PRZYCZEPA,
    "naczepa": RodzajPojazdu.PRZYCZEPA,
    "motocykl": RodzajPojazdu.MOTOCYKL,
    "motorower": RodzajPojazdu.MOTOCYKL,
    "autobus": RodzajPojazdu.AUTOBUS,
    # poleasingowe.pl — ścieżki list kategorii (RECON.md §4.2).
    # `vehicles` CELOWO mapuje się na `NIEZNANY`: to worek na wszystko na
    # kołach — osobowe, dostawcze i ciągniki siodłowe razem. Wpis stoi tu po
    # to, żeby nikt nie dopisał jej kiedyś jako `OSOBOWY`.
    "vehicles": RodzajPojazdu.NIEZNANY,
    "ecr motorcycles": RodzajPojazdu.MOTOCYKL,
    "ecr trailers1": RodzajPojazdu.PRZYCZEPA,
    "ecr bus": RodzajPojazdu.AUTOBUS,
}

# Nadwozia rozpoznawane w nazwie. Kolejność MA ZNACZENIE: dłuższe wyrażenia
# stoją przed krótszymi, bo „CIĄGNIK SIODŁOWY" zawiera „CIĄGNIK", a decyduje
# pierwsze trafienie.
NADWOZIA: tuple[tuple[str, RodzajPojazdu], ...] = (
    ("ciagnik siodlowy", RodzajPojazdu.CIEZAROWY),
    ("ciagnik rolniczy", RodzajPojazdu.INNY),
    ("furgon blaszak", RodzajPojazdu.DOSTAWCZY),
    ("furgon", RodzajPojazdu.DOSTAWCZY),
    ("blaszak", RodzajPojazdu.DOSTAWCZY),
    ("plandeka", RodzajPojazdu.DOSTAWCZY),
    ("brygadowka", RodzajPojazdu.DOSTAWCZY),
    ("izoterma", RodzajPojazdu.DOSTAWCZY),
    ("wywrotka", RodzajPojazdu.CIEZAROWY),
    ("podnosnik koszowy", RodzajPojazdu.CIEZAROWY),
    ("naczepa", RodzajPojazdu.PRZYCZEPA),
    ("przyczepa", RodzajPojazdu.PRZYCZEPA),
    ("autobus", RodzajPojazdu.AUTOBUS),
    ("kamper", RodzajPojazdu.INNY),
    ("quad", RodzajPojazdu.INNY),
    ("motocykl", RodzajPojazdu.MOTOCYKL),
    ("skuter", RodzajPojazdu.MOTOCYKL),
    # Nadwozia jednoznacznie osobowe. Bez nich „SKODA SUPERB KOMBI" byłaby
    # `NIEZNANY`, czyli poza domyślnym filtrem — a to jest zwykłe auto.
    ("kombi", RodzajPojazdu.OSOBOWY),
    ("sedan", RodzajPojazdu.OSOBOWY),
    ("hatchback", RodzajPojazdu.OSOBOWY),
    ("liftback", RodzajPojazdu.OSOBOWY),
    ("coupe", RodzajPojazdu.OSOBOWY),
    ("kabriolet", RodzajPojazdu.OSOBOWY),
    ("cabrio", RodzajPojazdu.OSOBOWY),
    ("roadster", RodzajPojazdu.OSOBOWY),
    ("suv", RodzajPojazdu.OSOBOWY),
    ("crossover", RodzajPojazdu.OSOBOWY),
    ("minivan", RodzajPojazdu.OSOBOWY),
    ("kompakt", RodzajPojazdu.OSOBOWY),
)

# Zamiana polskich znaków na łacińskie. `unaccent` jest zabroniony (§8.3),
# a lista jest krótka i zamknięta — to nie jest transliteracja ogólna.
_BEZ_OGONKOW = str.maketrans("ąćęłńóśżźĄĆĘŁŃÓŚŻŹ", "acelnoszzACELNOSZZ")


def _klucz(tekst: str) -> str:
    """Postać do porównań: bez ogonków, bez interpunkcji, małymi literami."""
    bez_ogonkow = tekst.translate(_BEZ_OGONKOW).lower()
    return re.sub(r"[\s_/,.-]+", " ", bez_ogonkow).strip()


def z_kategorii(wartosc: str | None) -> RodzajPojazdu:
    """Rodzaj z deklaracji źródła: kategorii w adresie albo pola w tabeli."""
    if not wartosc:
        return RodzajPojazdu.NIEZNANY
    return KATEGORIE.get(_klucz(wartosc), RodzajPojazdu.NIEZNANY)


def z_nazwy(nazwa: str | None) -> RodzajPojazdu:
    """Rodzaj z nazwy pojazdu — jedyne wyjście dla poleasingowe.pl.

    Dopasowanie jest **na całe słowa**: `re.escape` plus granice, żeby „SUV"
    nie trafiło w środek modelu, a „kombi" w „kombinat".
    """
    if not nazwa:
        return RodzajPojazdu.NIEZNANY
    tekst = _klucz(nazwa)
    for wzorzec, rodzaj in NADWOZIA:
        if re.search(rf"(?<![a-z0-9]){re.escape(wzorzec)}(?![a-z0-9])", tekst):
            return rodzaj
    return RodzajPojazdu.NIEZNANY


def rozpoznaj(
    *, kategoria: str | None = None, nazwa: str | None = None
) -> RodzajPojazdu:
    """Rodzaj pojazdu: najpierw deklaracja źródła, potem nazwa.

    Deklaracja wygrywa, bo pochodzi od serwisu, a nazwa jest naszym
    odczytem. Wyjątek jest jeden i wynika z pomiaru: kategoria `vehicles`
    poleasingowe.pl **nie jest** deklaracją rodzaju, tylko workiem na
    wszystko na kołach — mapuje się na `NIEZNANY` i wtedy decyduje nazwa.
    """
    z_deklaracji = z_kategorii(kategoria)
    if z_deklaracji is not RodzajPojazdu.NIEZNANY:
        return z_deklaracji
    return z_nazwy(nazwa)
