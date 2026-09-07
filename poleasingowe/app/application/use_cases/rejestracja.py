"""Rejestracja źródeł w bazie przy starcie (SPEC.md §8.1, §10.2).

Wiersz `source` łączy trzy rzeczy o różnym pochodzeniu:

1. **Fakty o serwisie** z rekonesansu — okno dogrywki, siatka domknięcia,
   semantyka `bid_count`. Odświeżamy je przy każdym starcie, bo mieszkają
   w kodzie i to one są źródłem prawdy.
2. **Ustawienia użytkownika** z opcji add-onu — włączenie, limit tempa, floor.
   Też odświeżamy: użytkownik właśnie je zmienił i restartował add-on.
3. **Stan uwierzytelnienia** — `auth_state` i licznik nieudanych logowań.
   Tego **nie wolno ruszać**.

Punkt trzeci jest tu całym powodem istnienia tego modułu. `zapisz()` robi
upsert nadpisujący wszystkie kolumny, więc naiwna rejestracja przy starcie
zerowałaby licznik z §10.2 — a pętla restartów kontenera obeszłaby wtedy
twardy limit trzech nieudanych logowań i doprowadziła do zablokowania konta
w serwisie. Dokładnie przed tym spec ostrzega, pisząc, że licznik ma być
trwały.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import replace

from app.application.ports import UnitOfWork
from app.domain.entities import Source

log = logging.getLogger(__name__)


async def zarejestruj_zrodla(
    uow: UnitOfWork,
    zadane: Iterable[Source],
) -> Sequence[Source]:
    """Zapisuje źródła, **zachowując ich stan uwierzytelnienia**.

    `zadane` to wiersze zbudowane z faktów o serwisach i opcji użytkownika.
    Dla źródła, które już jest w bazie, przepisujemy z niego `auth_state`
    i `consecutive_auth_failures` — reszta kolumn pochodzi z `zadane`.
    """
    wynik: list[Source] = []
    for nowe in zadane:
        istniejace = await uow.source.po_kluczu(nowe.key)
        if istniejace is None:
            wynik.append(await uow.source.zapisz(nowe))
            log.info("zarejestrowano źródło %s", nowe.key)
            continue

        zachowane = replace(
            nowe,
            id=istniejace.id,
            auth_state=istniejace.auth_state,
            consecutive_auth_failures=istniejace.consecutive_auth_failures,
        )
        if istniejace.consecutive_auth_failures:
            log.info(
                "źródło %s zachowuje %s nieudanych logowań i stan %s "
                "(SPEC.md §10.2 — licznik jest trwały)",
                nowe.key,
                istniejace.consecutive_auth_failures,
                istniejace.auth_state.value,
            )
        wynik.append(await uow.source.zapisz(zachowane))
    return wynik
