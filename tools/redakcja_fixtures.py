#!/usr/bin/env python3
"""Redakcja fixtures przed opublikowaniem repo (SPEC.md §10.2).

Repo add-onu jest publiczne, a Home Assistant klonuje je przy kazdej
aktualizacji. Fixtures to zrzuty z czterech serwisow aukcyjnych; zanim trafia
na GitHub, podmieniamy dane identyfikujace konkretne pojazdy i osoby:

  * numery VIN,
  * numery rejestracyjne,
  * loginy licytujacych.

Czego NIE redagujemy i dlaczego: nazwy i adresy firm sprzedajacych to dane
kontaktowe przedsiebiorstw publikowane wprost w ofercie, a testy parsera
opieraja sie na polu "Lokalizacja". Podmiana zepsulaby je bez zysku dla
czyjejkolwiek prywatnosci.

Trzy wlasnosci, na ktorych zalezy:

1. **Deterministycznosc** — ten sam VIN daje zawsze ten sam zamiennik, takze
   miedzy plikami. Bez tego rozsypuja sie scenariusze deduplikacji po VIN
   (SPEC.md §8.4), gdzie ta sama aukcja wystepuje w dwoch serwisach.
2. **Zachowanie dlugosci, co do bajta** — zamiennik ma tyle samo znakow co
   oryginal, a pliki czytamy i piszemy z `newline=""` (przez open, bo
   Path.read_text nie przyjmuje tego argumentu przed Pythonem 3.13). Bez tego
   tryb tekstowy zamienia CRLF na LF i fixtures przestaja byc wiernym zrzutem.
3. **Idempotencja** — wyprodukowane zamienniki laduja w rejestrze, wiec drugie
   uruchomienie ich nie rusza. Bez rejestru sztuczny VIN pasuje do tego samego
   wzorca co prawdziwy i przy kazdym przebiegu dostawalby nowa wartosc.

Uzycie:
    python3 tools/redakcja_fixtures.py --sprawdz     # tylko raport
    python3 tools/redakcja_fixtures.py               # redaguj w miejscu
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

KATALOG = pathlib.Path(__file__).resolve().parent.parent / "fixtures"

# Rejestr WYPRODUKOWANYCH zamiennikow. Zawiera wylacznie wartosci sztuczne —
# nigdy oryginalow — wiec mozna go trzymac w repo.
#
# Lezy obok skryptu, a NIE w fixtures/. Trzymany w fixtures/ ginal przy kazdym
# przywroceniu tego katalogu z kopii, a wtedy juz zredagowane wartosci byly
# redagowane ponownie i powstawaly artefakty podwojnej redakcji.
REJESTR = pathlib.Path(__file__).resolve().parent / "redakcja-zamienniki.json"

# Alfabet VIN: bez I, O i Q — wykluczone norma, zeby nie mylic z 1 i 0.
ALFABET_VIN = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
CYFRY = "0123456789"
LITERY = "ABCDEFGHJKLMNPRSTUVWXYZ"
PREFIKS_LOGINU = "uzytkownik"

VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")

# Miedzy etykieta a wartoscia potrafi stac kilka tagow: EFL ma </td><td>,
# autoprzetarg </b>, leasygroup </p> <p class="...">. Uzywamy leniwego,
# ograniczonego odstepu zamiast zagniezdzonych kwantyfikatorow — wariant
# `(?:<tag>|\s+){0,12}` wygladal precyzyjniej, ale powodowal katastrofalne
# nawracanie i zawieszal skrypt na dlugich wcieciach.
ETYKIETA_REJESTRACJI = re.compile(
    r"Nr\s+rej(?:\.|estracyjny)?\s*:?.{0,200}?([A-Z]{2,3}[0-9A-Z]{4,5})\b",
    re.S,
)

# EFL wstawia tablice takze do tytulu i slugu, w formacie "... 2022r. WND3999C
# ...", i bywa, ze w danym pliku nie ma jej nigdzie z etykieta. Bez tej kotwicy
# takie tablice przezylyby redakcje.
TABLICA_PO_ROCZNIKU = re.compile(r"\d{4}r\.[-\s]+([A-Z]{2,3}[0-9A-Z]{4,5})\b")

# poleasingowe.pl wystawia PELNY login zwyciezcy obok zamaskowanego
# winner_formated (RECON.md §4.2) — to dane osoby trzeciej.
WINNER = re.compile(r"(winner:\s*')([^']*)(')")
BD_NAME = re.compile(r'("bd_name"\s*:\s*")([^"]*)(")')

ROZSZERZENIA = {".html", ".json", ".js", ".txt"}


def _wczytaj(sciezka: pathlib.Path) -> str:
    """Czyta bez tlumaczenia koncow linii — CRLF ma zostac CRLF."""
    with sciezka.open(encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def _zapisz(sciezka: pathlib.Path, tresc: str) -> None:
    with sciezka.open("w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(tresc)


def _strumien(zrodlo: str, alfabet: str) -> str:
    """Deterministyczny ciag znakow z alfabetu, dlugosci co najmniej 64."""
    wynik: list[str] = []
    licznik = 0
    while len(wynik) <= 64:
        skrot = hashlib.sha256(f"{zrodlo}:{licznik}".encode()).digest()
        wynik.extend(alfabet[bajt % len(alfabet)] for bajt in skrot)
        licznik += 1
    return "".join(wynik)


def zamien_vin(oryginal: str) -> str:
    """Zachowuje WMI (pierwsze trzy znaki), resztę losuje deterministycznie.

    WMI identyfikuje producenta, nie egzemplarz, więc jego zachowanie nie
    mówi nic o konkretnym pojeździe, a fixtures pozostają realistyczne.
    """
    strumien = _strumien(f"vin:{oryginal}", ALFABET_VIN)
    return oryginal[:3] + strumien[: len(oryginal) - 3]


def zamien_rejestracje(oryginal: str) -> str:
    """Litera zostaje literą, cyfra cyfrą — kształt tablicy się nie zmienia."""
    litery = _strumien(f"rej-l:{oryginal}", LITERY)
    cyfry = _strumien(f"rej-c:{oryginal}", CYFRY)
    wynik: list[str] = []
    il = ic = 0
    for znak in oryginal:
        if znak.isdigit():
            wynik.append(cyfry[ic])
            ic += 1
        else:
            wynik.append(litery[il])
            il += 1
    return "".join(wynik)


def _login_pelny(oryginal: str) -> str:
    strumien = _strumien(f"login:{oryginal}", CYFRY)
    return (PREFIKS_LOGINU + strumien)[: max(len(oryginal), 4)]


def zamien_login(oryginal: str) -> str:
    """Zachowuje długość i kształt maskowania używanego przez serwis."""
    if not oryginal.strip() or oryginal.strip() == "-":
        return oryginal  # placeholder serwisu — nie ma czego chronic
    if oryginal.startswith(PREFIKS_LOGINU):
        return oryginal  # juz zredagowany
    if "..." in oryginal:
        # Serwis sam maskuje login do postaci "d...6" — odtwarzamy ten ksztalt.
        zastepczy = _login_pelny(oryginal)
        return f"{zastepczy[0]}...{zastepczy[-1]}"
    return _login_pelny(oryginal)


def zbierz_rejestracje(teksty: list[str]) -> dict[str, str]:
    """Buduje mapowanie tablica → zamiennik.

    Zbieramy z pól oznaczonych etykietą i z tytułów, a podmieniamy wszędzie —
    dzięki temu tablica znika też ze slugu URL-a i z atrybutów `alt`, a przy
    okazji nie trafiamy w identyfikator CSS ani w skrót w skrypcie.
    """
    mapowanie: dict[str, str] = {}
    for tekst in teksty:
        for wzorzec in (ETYKIETA_REJESTRACJI, TABLICA_PO_ROCZNIKU):
            for tablica in wzorzec.findall(tekst):
                mapowanie.setdefault(tablica, zamien_rejestracje(tablica))
    return mapowanie


def redaguj(
    tekst: str, rejestracje: dict[str, str], znane: set[str]
) -> tuple[str, dict[str, int]]:
    liczniki = {"vin": 0, "rejestracja": 0, "login": 0}

    def _vin(m: re.Match[str]) -> str:
        if m.group(0) in znane:
            return m.group(0)
        liczniki["vin"] += 1
        nowy = zamien_vin(m.group(0))
        znane.add(nowy)
        return nowy

    def _login(m: re.Match[str]) -> str:
        # Zamaskowany zamiennik ("u...4") nadal zawiera "...", wiec bez tego
        # sprawdzenia drugi przebieg mapowalby go ponownie i redakcja
        # przestawalaby byc idempotentna.
        if m.group(2) in znane:
            return m.group(0)
        nowy = zamien_login(m.group(2))
        if nowy != m.group(2):
            liczniki["login"] += 1
            znane.add(nowy)
        return m.group(1) + nowy + m.group(3)

    tekst = VIN.sub(_vin, tekst)
    for tablica, zamiennik in rejestracje.items():
        ile = tekst.count(tablica)
        if ile:
            liczniki["rejestracja"] += ile
            tekst = tekst.replace(tablica, zamiennik)
    tekst = WINNER.sub(_login, tekst)
    tekst = BD_NAME.sub(_login, tekst)
    return tekst, liczniki


def wczytaj_rejestr() -> set[str]:
    if not REJESTR.is_file():
        return set()
    return set(json.loads(REJESTR.read_text(encoding="utf-8")))


def zapisz_rejestr(zamienniki: set[str]) -> None:
    REJESTR.write_text(
        json.dumps(sorted(zamienniki), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sprawdz", action="store_true", help="tylko raport, bez zapisu")
    args = ap.parse_args()

    if not KATALOG.is_dir():
        print(f"brak katalogu {KATALOG}", file=sys.stderr)
        return 1

    pliki = [
        p
        for p in sorted(KATALOG.rglob("*"))
        if p.is_file()
        and p.suffix in ROZSZERZENIA
        and "-auth-" not in p.name  # i tak poza repo (SPEC.md §10.2)
    ]
    # Path.read_text(newline=...) istnieje dopiero od Pythona 3.13, a celem
    # jest 3.12 (obraz bazowy HA, SPEC.md §7) — stad zwykly open().
    tresci = {p: _wczytaj(p) for p in pliki}

    znane = wczytaj_rejestr()
    rejestracje = {
        oryginal: zamiennik
        for oryginal, zamiennik in zbierz_rejestracje(list(tresci.values())).items()
        if oryginal not in znane
    }
    znane.update(rejestracje.values())

    suma = {"vin": 0, "rejestracja": 0, "login": 0}
    zmienione = 0
    for plik in pliki:
        oryginal = tresci[plik]
        nowy, liczniki = redaguj(oryginal, rejestracje, znane)
        for klucz, wartosc in liczniki.items():
            suma[klucz] += wartosc
        if nowy == oryginal:
            continue
        zmienione += 1
        if len(nowy) != len(oryginal):
            print(
                f"  UWAGA {plik.name}: długość {len(oryginal)} -> {len(nowy)} "
                "— zamiennik nie zachował długości",
                file=sys.stderr,
            )
        if not args.sprawdz:
            _zapisz(plik, nowy)

    if not args.sprawdz:
        zapisz_rejestr(znane)

    tryb = "do zredagowania" if args.sprawdz else "zredagowano"
    print(f"{tryb}: {zmienione} plików")
    print(f"  VIN-y:            {suma['vin']}")
    print(f"  nr rejestracyjne: {suma['rejestracja']}")
    print(f"  loginy:           {suma['login']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
