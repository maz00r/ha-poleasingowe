"""Pętla harmonogramu (SPEC.md §11.1, §11.3, §13).

Jedna pętla asyncio. Bierze aukcje z `next_poll_at <= now()` posortowane po
`ends_at`, przepuszcza przez kubełek tokenów źródła, po każdym odpycie
przelicza `next_poll_at` czystą polityką z `domain/harmonogram.py`.

**Pętla śpi do najbliższego terminu**, nie budzi się na stałym ticku. W
spoczynku koszt CPU jest zerowy, a baza — dzielona z TeslaMate — nie dostaje
zapytań bez powodu.

Źródła obsługujemy **po kolei, nie równolegle**. §13 wymaga `concurrency = 1`
na serwis; równoległość *między* serwisami byłaby dozwolona, ale na czterech
słabych rdzeniach (§1) kupowałaby kilka sekund latencji za realną komplikację
w izolacji błędów. Awaria jednego źródła i tak nie przerywa przebiegu — ląduje
w `run_log` i otwiera bezpiecznik tylko dla siebie.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
from collections.abc import Mapping
from dataclasses import replace

from app.application.ports import (
    AuctionSource,
    FabrykaKontekstu,
    KontekstBazy,
)
from app.domain.entities import Auction, PriceSnapshot, RunLog, Source
from app.domain.enums import AuctionStatus, PollTier
from app.domain.errors import DomainError
from app.domain.harmonogram import nastepny_odpyt, tier
from app.domain.logowanie import ZrodloZablokowane
from app.infrastructure.scheduler.tempo import Bezpiecznik, KubelekTokenow
from app.infrastructure.supervisor.proces import rss_bajty

log = logging.getLogger(__name__)

MAKS_SEN_S = 60.0
"""SPEC.md §11.1 — `sleep(min(next_due - now, 60))`.

Sufit istnieje, żeby nowa aukcja dodana w międzyczasie nie czekała pół dnia
na obudzenie pętli.
"""
MIN_SEN_S = 0.5
"""Bez tego pętla z zaległym terminem kręciłaby się bez oddechu."""

LIMIT_PARTII = 100
"""Ile aukcji bierzemy na jeden obrót. Reszta poczeka do następnego."""


class Dispatcher:
    """Jedna pętla asyncio odpytująca aukcje obserwowane (SPEC.md §11.1)."""

    def __init__(
        self,
        fabryka: FabrykaKontekstu,
        zrodla: Mapping[str, AuctionSource],
        *,
        limit_partii: int = LIMIT_PARTII,
    ) -> None:
        self._fabryka = fabryka
        self._zrodla = zrodla
        self._limit = limit_partii
        self._kubelki: dict[str, KubelekTokenow] = {}
        self._bezpieczniki: dict[str, Bezpiecznik] = {}
        # Zbiór aukcji w locie trzymany w pamięci — proces jest jeden, więc
        # to wystarcza. SPEC.md §11.1 zabrania budowania blokad w bazie
        # „na przyszłość".
        self._w_locie: set[int] = set()

    # ------------------------------------------------------------------
    # Pętla
    # ------------------------------------------------------------------

    async def uruchom(self) -> None:
        """Pętla główna. Kończy się wyłącznie przez `CancelledError`."""
        log.info("dispatcher wystartował, źródła: %s", ", ".join(sorted(self._zrodla)))
        try:
            while True:
                sen = await self.jeden_obrot()
                await asyncio.sleep(sen)
        except asyncio.CancelledError:
            log.info("dispatcher zatrzymany")
            raise

    async def jeden_obrot(self) -> float:
        """Jeden przebieg. Zwraca, ile sekund spać do następnego.

        Wyjątki łapiemy szeroko i celowo: pojedyncza awaria — bazy, sieci,
        parsera — nie ma prawa zatrzymać pętli. Zatrzymana pętla to add-on,
        który wygląda na działający i nic nie robi.
        """
        try:
            async with self._fabryka() as kontekst:
                teraz = await kontekst.zapytania.czas_serwera()
                await self._przebieg(kontekst, teraz)
                return await self._ile_spac(kontekst, teraz)
        except Exception:
            log.exception(
                "obrót dispatchera nie powiódł się; ponawiam za %s s", MAKS_SEN_S
            )
            return MAKS_SEN_S

    async def _ile_spac(self, kontekst: KontekstBazy, teraz: dt.datetime) -> float:
        """SPEC.md §11.1 — `min(next_due - now, 60)`, nigdy stały tick."""
        async with kontekst.uow as uow:
            najblizszy = await uow.auction.najblizszy_termin()
        if najblizszy is None:
            return MAKS_SEN_S
        do_terminu = (najblizszy - teraz).total_seconds()
        return max(MIN_SEN_S, min(do_terminu, MAKS_SEN_S))

    # ------------------------------------------------------------------
    # Przebieg
    # ------------------------------------------------------------------

    async def _przebieg(self, kontekst: KontekstBazy, teraz: dt.datetime) -> None:
        async with kontekst.uow as uow:
            zrodla = {z.key: z for z in await uow.source.wlaczone()}
            po_id = {z.id: z for z in zrodla.values() if z.id is not None}

        # Przemiat listy PRZED odpytem szczegółów — to on w ogóle wprowadza
        # aukcje do bazy. Bez niego dispatcher odświeżałby wyłącznie to, co
        # ktoś tam wcześniej wstawił, czyli nic.
        for do_przemiatu in zrodla.values():
            await self._moze_przemiec(kontekst, do_przemiatu, teraz)

        # Aukcja nieobserwowana nie jest odpytywana po raz drugi (§11.2),
        # a marker końca stoi wyłącznie na stronie szczegółów — bez tego
        # kroku zostawałaby `ACTIVE` na zawsze i siedziała w widoku
        # „Aktywne" długo po swoim terminie.
        async with kontekst.uow as uow:
            zamkniete = await uow.auction.zamknij_po_terminie(teraz)
        if zamkniete:
            log.info("zamknięto %s aukcji po terminie", zamkniete)

        async with kontekst.uow as uow:
            zalegle = await uow.auction.do_odpytu(teraz, self._limit)

        do_zrobienia = [a for a in zalegle if a.id not in self._w_locie]
        if not do_zrobienia:
            return

        # Grupujemy po źródle, żeby `concurrency = 1` na serwis wynikało
        # z kształtu pętli, a nie z dyscypliny wywołań (§13).
        wedlug_zrodla: dict[int, list[Auction]] = {}
        for aukcja in do_zrobienia:
            wedlug_zrodla.setdefault(aukcja.source_id, []).append(aukcja)

        for source_id, aukcje in wedlug_zrodla.items():
            zrodlo = po_id.get(source_id)
            if zrodlo is None:
                log.warning(
                    "aukcje wskazują na wyłączone źródło %s — pomijam", source_id
                )
                continue
            await self._obsluz_zrodlo(kontekst, zrodlo, aukcje, teraz)

    async def _moze_przemiec(
        self, kontekst: KontekstBazy, zrodlo: Source, teraz: dt.datetime
    ) -> None:
        """Zbiorczy przemiat listy, jeśli minął interwał źródła (SPEC.md §11.2).

        „Aukcje nieobserwowane nie są odpytywane pojedynczo w ogóle.
        Wystarcza im zbiorczy przemiat listy raz na kilka godzin. To główna
        oszczędność całego systemu — koszt rośnie z liczbą obserwowanych,
        nie z liczbą ofert w serwisie."

        `last_sweep_at = NULL` znaczy „nigdy", więc świeżo zainstalowany
        dodatek zapełnia się przy pierwszym obrocie, a nie po sześciu
        godzinach patrzenia na pustą listę.
        """
        if zrodlo.last_sweep_at is not None:
            od_ostatniego = (teraz - zrodlo.last_sweep_at).total_seconds()
            if od_ostatniego < zrodlo.sweep_interval_seconds:
                return

        adapter = self._zrodla.get(zrodlo.key)
        if adapter is None or zrodlo.id is None:
            return

        zegar_mono = asyncio.get_running_loop().time
        bezpiecznik = self._bezpieczniki.setdefault(zrodlo.key, Bezpiecznik())
        if bezpiecznik.otwarty(zegar_mono()):
            return

        async with kontekst.uow as uow:
            przebieg = await uow.run_log.rozpocznij(
                RunLog(source_id=zrodlo.id, started_at=teraz)
            )

        nowe = 0
        bledy: list[str] = []
        pozycje: list[Auction] = []
        try:
            surowe = await adapter.przemiec_liste()
            # Nowo odkryta aukcja dostaje JEDEN odpyt szczegółów, po którym
            # wraca do trybu „tylko przemiat" (patrz `_termin_po_odpycie`).
            # Bez tego lista nie zna godziny zakończenia — poleasingowe podaje
            # na niej wyłącznie datę dzienną (RECON.md §4.2) — i nie da się
            # zdecydować, co warto obserwować.
            pozycje = [
                replace(
                    adapter.na_aukcje(s, zrodlo.id, teraz),
                    next_poll_at=teraz,
                    poll_tier=PollTier.FAR,
                )
                for s in surowe
            ]
            bezpiecznik.zglos_sukces()
        except (DomainError, OSError) as exc:
            bledy.append(f"przemiat: {type(exc).__name__}: {exc}")
            bezpiecznik.zglos_blad(zegar_mono())
            log.warning("przemiat listy %s nie powiódł się: %s", zrodlo.key, exc)

        if pozycje:
            async with kontekst.uow as uow:
                nowe = await uow.auction.zapisz_z_przemiatu(pozycje)
            log.info(
                "przemiat %s: %s pozycji, w tym %s nowych",
                zrodlo.key,
                len(pozycje),
                nowe,
            )

        async with kontekst.uow as uow:
            # Znacznik przesuwamy TAKŻE po nieudanym przemiacie — inaczej
            # padnięty serwis byłby przemiatany przy każdym obrocie pętli.
            # Od dobijania się w kółko jest bezpiecznik, nie brak znacznika.
            await uow.source.zapisz(replace(zrodlo, last_sweep_at=teraz))
            await uow.run_log.zakoncz(
                replace(
                    przebieg,
                    finished_at=await kontekst.zapytania.czas_serwera(),
                    new_count=nowe,
                    changed_count=len(pozycje),
                    error_count=len(bledy),
                    errors=bledy,
                    rss_bytes=rss_bajty(),
                    database_bytes=await kontekst.zapytania.rozmiar_bazy(),
                    notes="przemiat listy",
                )
            )

    async def _obsluz_zrodlo(
        self,
        kontekst: KontekstBazy,
        zrodlo: Source,
        aukcje: list[Auction],
        teraz: dt.datetime,
    ) -> None:
        adapter = self._zrodla.get(zrodlo.key)
        if adapter is None:
            log.warning("brak adaptera dla źródła %s — pomijam", zrodlo.key)
            return

        bezpiecznik = self._bezpieczniki.setdefault(zrodlo.key, Bezpiecznik())
        zegar_mono = asyncio.get_running_loop().time
        if bezpiecznik.otwarty(zegar_mono()):
            log.info(
                "źródło %s odstawione na %.0f s po serii błędów",
                zrodlo.key,
                bezpiecznik.ile_pauzy(zegar_mono()),
            )
            return

        kubelek = self._kubelki.setdefault(
            zrodlo.key, KubelekTokenow(zrodlo.rate_limit_per_minute)
        )

        assert zrodlo.id is not None
        async with kontekst.uow as uow:
            przebieg = await uow.run_log.rozpocznij(
                RunLog(source_id=zrodlo.id, started_at=teraz)
            )

        zmienione = 0
        bledy: list[str] = []
        for aukcja in aukcje:
            if aukcja.id is None:  # pragma: no cover — z bazy zawsze z id
                continue
            self._w_locie.add(aukcja.id)
            try:
                if await self._odpytaj(kontekst, adapter, zrodlo, aukcja, kubelek):
                    zmienione += 1
                bezpiecznik.zglos_sukces()
            except ZrodloZablokowane as exc:
                # Nie awaria źródła, tylko odmowa wykonania (§10.2) — nie
                # otwieramy bezpiecznika, bo sieć działa bez zarzutu.
                bledy.append(str(exc))
                break
            except (DomainError, OSError) as exc:
                bledy.append(f"{aukcja.external_id}: {type(exc).__name__}: {exc}")
                bezpiecznik.zglos_blad(zegar_mono())
                log.warning(
                    "odpyt %s/%s nie powiódł się: %s",
                    zrodlo.key,
                    aukcja.external_id,
                    exc,
                )
                if bezpiecznik.otwarty(zegar_mono()):
                    log.error(
                        "bezpiecznik źródła %s otwarty po %s błędach — pauza %.0f s",
                        zrodlo.key,
                        bezpiecznik.kolejne_bledy,
                        bezpiecznik.ile_pauzy(zegar_mono()),
                    )
                    break
            finally:
                self._w_locie.discard(aukcja.id)

        async with kontekst.uow as uow:
            await uow.run_log.zakoncz(
                replace(
                    przebieg,
                    finished_at=await kontekst.zapytania.czas_serwera(),
                    changed_count=zmienione,
                    error_count=len(bledy),
                    errors=bledy,
                    rss_bytes=rss_bajty(),
                    database_bytes=await kontekst.zapytania.rozmiar_bazy(),
                )
            )

    # ------------------------------------------------------------------
    # Pojedynczy odpyt
    # ------------------------------------------------------------------

    async def _odpytaj(
        self,
        kontekst: KontekstBazy,
        adapter: AuctionSource,
        zrodlo: Source,
        aukcja: Auction,
        kubelek: KubelekTokenow,
    ) -> bool:
        """Jeden odpyt aukcji. Zwraca, czy coś się zmieniło."""
        czekaj = kubelek.ile_czekac()
        if czekaj > 0:
            await asyncio.sleep(czekaj)
        kubelek.zuzyj()

        # SPEC.md §11.3 krok 2: adapter porównuje hash surowych bajtów PRZED
        # parsowaniem i zwraca `None`, gdy treść się nie zmieniła. Krok 1
        # (żądanie warunkowe) jest martwy — żaden z czterech serwisów nie
        # zwraca `ETag` ani `Last-Modified` (RECON.md §3.1).
        surowa = await adapter.pobierz_szczegoly(
            aukcja.external_id, aukcja.content_hash
        )

        teraz = await kontekst.zapytania.czas_serwera()
        assert aukcja.id is not None

        if surowa is None:
            async with kontekst.uow as uow:
                obserwowana = await uow.watchlist.obserwowana(aukcja.id)
                await uow.auction.odnotuj_widziana(aukcja.id, teraz)
                await uow.auction.zapisz(
                    self._termin_po_odpycie(aukcja, zrodlo, teraz, obserwowana)
                )
            return False

        swieza = adapter.na_aukcje(surowa, zrodlo.id or aukcja.source_id, teraz)

        async with kontekst.uow as uow:
            obserwowana = await uow.watchlist.obserwowana(aukcja.id)
            scalona = self._scal(aukcja, swieza, zrodlo, teraz, obserwowana)
            zapisana = await uow.auction.zapisz(scalona)
            zmieniony = None
            if scalona.price_current is not None and zapisana.id is not None:
                # SPEC.md §8.4 — snapshot WYŁĄCZNIE przy zmianie ceny, liczby
                # ofert albo `ends_at`. Bez tej reguły dogrywka generuje setki
                # identycznych wierszy na aukcję.
                zmieniony = await uow.snapshot.zapisz_jesli_zmienil_sie(
                    PriceSnapshot(
                        auction_id=zapisana.id,
                        ts=teraz,
                        price=scalona.price_current,
                        bid_count=scalona.bid_count,
                        ends_at=scalona.ends_at,
                    )
                )
        return zmieniony is not None

    @staticmethod
    def _termin_po_odpycie(
        aukcja: Auction, zrodlo: Source, teraz: dt.datetime, obserwowana: bool
    ) -> Auction:
        """Kiedy odpytać tę aukcję znowu — albo czy w ogóle.

        SPEC.md §11.2: pojedynczo odpytujemy **wyłącznie obserwowane**. Aukcja
        nieobserwowana dostała właśnie swój jeden odpyt po odkryciu i wraca do
        trybu „wystarcza przemiat listy": `next_poll_at = NULL`. To jest ta
        główna oszczędność systemu — koszt rośnie z liczbą obserwowanych,
        a nie z liczbą ofert w serwisie.
        """
        if not obserwowana:
            return replace(
                aukcja, last_seen_at=teraz, next_poll_at=None, poll_tier=PollTier.IDLE
            )
        return replace(
            aukcja,
            last_seen_at=teraz,
            next_poll_at=nastepny_odpyt(aukcja, zrodlo, teraz),
            poll_tier=tier(aukcja, teraz),
        )

    def _scal(
        self,
        stara: Auction,
        swieza: Auction,
        zrodlo: Source,
        teraz: dt.datetime,
        obserwowana: bool,
    ) -> Auction:
        """Nakłada świeży odczyt na aukcję z bazy, zachowując jej historię.

        `first_seen_at` i `id` pochodzą z bazy — świeży odczyt ich nie zna
        i nadpisanie ich przesunęłoby datę pierwszej obserwacji na dziś.

        `ends_at` bierzemy **zawsze ze świeżego odczytu**: w endgame wartość
        z bazy jest tylko wskazówką (§11.2), a dogrywka realnie przesuwa
        termin — aukcja `9mjrl4k9` przeszła z 12:00 na 12:18 (RECON.md §3.7).
        """
        if swieza.status is AuctionStatus.DISAPPEARED:
            # Aukcja zniknęła z serwisu (autoprzetarg kasuje ją 10-15 s po
            # terminie, RECON.md §3.4). Ten odczyt nie niesie danych, tylko
            # sam fakt — więc zapisujemy WYŁĄCZNIE status i przestajemy
            # odpytywać. Nadpisanie reszty wyczyściłoby ostatnią znaną cenę,
            # czyli jedyne, co nam po tej aukcji zostało (§8.4).
            log.info("aukcja %s/%s zniknęła z serwisu", zrodlo.key, stara.external_id)
            return replace(
                stara,
                status=AuctionStatus.DISAPPEARED,
                last_seen_at=teraz,
                next_poll_at=None,
                poll_tier=PollTier.IDLE,
            )

        scalona = replace(
            swieza,
            id=stara.id,
            first_seen_at=stara.first_seen_at,
            last_seen_at=teraz,
            duplicate_of=stara.duplicate_of,
        )
        if swieza.ends_at is not None and stara.ends_at is not None:
            if swieza.ends_at > stara.ends_at:
                log.info(
                    "dogrywka w %s/%s: koniec przesunięty o %.0f s",
                    zrodlo.key,
                    stara.external_id,
                    (swieza.ends_at - stara.ends_at).total_seconds(),
                )
        elif swieza.ends_at is None:
            scalona = replace(scalona, ends_at=stara.ends_at)

        return self._termin_po_odpycie(scalona, zrodlo, teraz, obserwowana)


@contextlib.asynccontextmanager
async def uruchom_w_tle(dispatcher: Dispatcher):  # type: ignore[no-untyped-def]
    """Startuje pętlę jako zadanie i domyka ją czysto na wyjściu."""
    zadanie = asyncio.create_task(dispatcher.uruchom())
    try:
        yield zadanie
    finally:
        zadanie.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await zadanie
