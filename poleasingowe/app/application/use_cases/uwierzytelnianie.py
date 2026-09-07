"""Utrzymanie sesji w serwisie (SPEC.md §10.2).

Spina cztery rzeczy, które osobno są proste, a razem łatwo pomylić:
reguły przejść z `domain/logowanie.py`, trwały magazyn ciasteczek, marker
wygaśnięcia definiowany przez adapter i trwały licznik nieudanych prób.

Najważniejsza decyzja projektowa siedzi w rozróżnieniu **wygaśnięcia sesji**
od **odrzucenia poświadczeń**. Pierwsze jest normalne i nie rusza licznika;
drugie zbliża źródło do blokady. Potraktowanie awarii serwisu jak złego
hasła zablokowałoby konto po trzech niedostępnościach, których nikt nie
zawinił.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.application.ports import (
    AuthenticatedSource,
    MagazynSesji,
    OdpowiedzHttp,
)
from app.domain.errors import AuthenticationFailed, SessionExpired
from app.domain.logowanie import (
    StanLogowania,
    ZrodloZablokowane,
    po_nieudanym_logowaniu,
    po_udanym_logowaniu,
    po_wygasnieciu_sesji,
    wolno_probowac,
)

log = logging.getLogger(__name__)

Operacja = Callable[[], Awaitable[OdpowiedzHttp]]
ZapisStanu = Callable[[StanLogowania], Awaitable[None]]


class MenedzerSesji:
    """Jedna sesja per źródło na cały czas życia procesu (SPEC.md §10.2).

    `zapisz_stan` trafia do bazy przez `UnitOfWork` warstwy wyżej. Licznik
    **musi** być trwały: gdyby żył w pamięci procesu, pętla restartów
    kontenera zerowałaby go co restart i obeszła limit z §10.2.
    """

    def __init__(
        self,
        zrodlo: AuthenticatedSource,
        *,
        login: str,
        haslo: str,
        magazyn: MagazynSesji,
        zapisz_stan: ZapisStanu,
    ) -> None:
        self._zrodlo = zrodlo
        self._login = login
        self._haslo = haslo
        self._magazyn = magazyn
        self._zapisz_stan = zapisz_stan

    def przywroc_sesje(self) -> None:
        """Wczytuje ciasteczka z dysku przy starcie procesu.

        Restart add-onu nie ma powodować ponownego logowania — każde zbędne
        logowanie to kolejna próba na imiennym koncie.
        """
        ciastka = self._magazyn.wczytaj(self._zrodlo.key)
        if ciastka:
            self._zrodlo.przywroc_sesje(ciastka)
            log.info("przywrócono sesję %s z dysku", self._zrodlo.key)

    async def wykonaj(self, stan: StanLogowania, operacja: Operacja) -> OdpowiedzHttp:
        """Wykonuje żądanie, dbając o sesję. Zwraca odpowiedź po zalogowaniu.

        Ponowne logowanie jest **leniwe**: dopiero po wykryciu wygaśnięcia,
        nigdy prewencyjnie (§10.2). Retry jest **jeden** — druga porażka
        z rzędu znaczy, że problem nie leży w sesji.
        """
        if not wolno_probowac(stan):
            raise ZrodloZablokowane(
                f"źródło {self._zrodlo.key} jest zablokowane po "
                f"{stan.nieudane_proby} nieudanych logowaniach; "
                "odblokuj je w panelu diagnostycznym"
            )

        if stan.wymaga_logowania:
            stan = await self._zaloguj(stan)

        odpowiedz = await operacja()
        if not self._zrodlo.czy_sesja_wygasla(odpowiedz):
            return odpowiedz

        log.info("sesja %s wygasła — loguję się ponownie", self._zrodlo.key)
        stan = await self._zapisz(po_wygasnieciu_sesji(stan))
        stan = await self._zaloguj(stan)

        odpowiedz = await operacja()
        if self._zrodlo.czy_sesja_wygasla(odpowiedz):
            # Świeże ciasteczka i dalej wygasła sesja: to nie jest problem
            # sesji. Kolejne logowania tylko zbliżałyby konto do blokady.
            raise SessionExpired(
                f"{self._zrodlo.key}: sesja wygasła mimo świeżego logowania"
            )
        return odpowiedz

    async def _zaloguj(self, stan: StanLogowania) -> StanLogowania:
        try:
            ciastka = await self._zrodlo.zaloguj(self._login, self._haslo)
        except AuthenticationFailed:
            # Tylko odrzucone poświadczenia podbijają licznik. Błąd sieci
            # jest `SourceUnavailable` i przelatuje wyżej nietknięty —
            # inaczej trzy awarie serwisu blokowałyby moje konto.
            nowy = po_nieudanym_logowaniu(stan)
            await self._zapisz(nowy)
            self._magazyn.usun(self._zrodlo.key)
            if nowy.zablokowane:
                log.error(
                    "źródło %s zablokowane po %s nieudanych logowaniach — "
                    "wymaga ręcznego resetu z panelu (SPEC.md §10.2)",
                    self._zrodlo.key,
                    nowy.nieudane_proby,
                )
            raise

        self._magazyn.zapisz(self._zrodlo.key, ciastka)
        log.info("zalogowano w %s", self._zrodlo.key)
        return await self._zapisz(po_udanym_logowaniu(stan))

    async def _zapisz(self, stan: StanLogowania) -> StanLogowania:
        await self._zapisz_stan(stan)
        return stan
