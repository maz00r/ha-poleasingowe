"""Pętla harmonogramu (SPEC.md §11.1, §11.3, §13).

Jedna pętla asyncio. Bierze aukcje z `next_poll_at <= now()` posortowane po
`ends_at`, przepuszcza przez kubełek tokenów źródła, po każdym odpycie
przelicza `next_poll_at` czystą polityką z `domain/harmonogram.py`.

**Pętla śpi do najbliższego terminu**, nie budzi się na stałym ticku. W
spoczynku koszt CPU jest zerowy, a baza — dzielona z TeslaMate — nie dostaje
zapytań bez powodu.

Każde źródło dostaje własne zadanie w tej samej pętli asyncio. Może więc
czekać na swój limiter albo wolną odpowiedź, gdy pozostałe źródła dalej
obsługują pilne odczyty. `bramka_sieci` zachowuje mimo tego `concurrency = 1`
na serwis, a awaria jednego źródła ląduje w `run_log` i otwiera bezpiecznik
wyłącznie dla niego.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace

from app.application.ports import (
    AuctionSource,
    FabrykaKontekstu,
    KontekstBazy,
    ZrodloZOfertami,
)
from app.domain.domkniecie import (
    nastepny_krok_drabinki,
    po_odczycie_po_terminie,
    po_wyczerpaniu_drabinki,
    w_domykaniu,
)
from app.domain.entities import (
    Auction,
    PriceSnapshot,
    RunLog,
    Source,
    z_cena_wywolawcza,
)
from app.domain.enums import AuctionStatus, PollTier, SweepStatus
from app.domain.errors import DomainError
from app.domain.harmonogram import floor_zrodla, nastepny_odpyt, tier
from app.domain.logowanie import ZrodloZablokowane
from app.infrastructure.kopia import BladKopii, KopiaZapasowa
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
        kopia: KopiaZapasowa | None = None,
    ) -> None:
        self._fabryka = fabryka
        self._zrodla = zrodla
        self._limit = limit_partii
        # `None` znaczy „bez kopii" — tak chodzą testy i tryb bez `/share`.
        self._kopia = kopia
        self._kubelki: dict[str, KubelekTokenow] = {}
        self._bezpieczniki: dict[str, Bezpiecznik] = {}
        self._blokady_sieci: dict[str, asyncio.Lock] = {}
        self._zrodla_dla_tempa: dict[str, Source] = {}
        # Zbiór aukcji w locie trzymany w pamięci — proces jest jeden, więc
        # to wystarcza. SPEC.md §11.1 zabrania budowania blokad w bazie
        # „na przyszłość".
        self._w_locie: set[int] = set()
        # Priorytet sprawdzamy ponownie po każdej stronie listy. Aukcja,
        # której odczyt właśnie się nie udał, pozostaje formalnie zaległa;
        # nie próbujemy jednak drugi raz w tym samym obrocie.
        self._odczytane_w_obrocie: set[int] = set()
        self._budzik = asyncio.Event()
        self._zadanie_kopii: asyncio.Task[None] | None = None
        self._blad_kopii: str | None = None
        self._kolejne_bledy_kopii = 0
        self._ponow_kopie_po: dt.datetime | None = None

    # ------------------------------------------------------------------
    # Pętla
    # ------------------------------------------------------------------

    def obudz(self) -> None:
        """Przerywa sen pętli — implementacja portu `Budzik` (§11.1).

        Wywoływane, gdy ktoś otworzy kartę aukcji i chce świeżych danych.
        Samo w sobie NIE wysyła żadnego żądania i nie omija limitów tempa:
        prosi tylko o wcześniejszy obrót, a o tym, co w tym obrocie poleci,
        decyduje jak zawsze kubełek tokenów.
        """
        self._budzik.set()

    @contextlib.asynccontextmanager
    async def bramka_sieci(
        self, source_key: str, *, domykanie: bool = False
    ) -> AsyncIterator[None]:
        """Wspólna bramka dla list, szczegółów i galerii danego serwisu.

        Jedna blokada obejmuje całe żądanie HTTP. Dzięki temu wejście na kartę
        ze zdjęciami nie otwiera drugiego równoległego połączenia do serwisu,
        gdy dispatcher właśnie odczytuje tę samą aukcję.
        """
        zrodlo = self._zrodla_dla_tempa.get(source_key)
        if zrodlo is None:
            yield
            return
        blokada = self._blokady_sieci.setdefault(source_key, asyncio.Lock())
        kubelek = self._kubelki.setdefault(
            source_key, KubelekTokenow(zrodlo.rate_limit_per_minute)
        )
        async with blokada:
            if not domykanie:
                czekaj = kubelek.ile_czekac()
                if czekaj > 0:
                    await asyncio.sleep(czekaj)
            kubelek.zuzyj(pozycz=domykanie)
            yield

    async def uruchom(self) -> None:
        """Pętla główna. Kończy się wyłącznie przez `CancelledError`."""
        log.info("dispatcher wystartował, źródła: %s", ", ".join(sorted(self._zrodla)))
        try:
            while True:
                sen = await self.jeden_obrot()
                # Sen PRZERYWALNY. `asyncio.sleep` bez tego kazałby czekać
                # do minuty na dane, o które ktoś właśnie poprosił, patrząc
                # na otwartą kartę.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._budzik.wait(), sen)
                self._budzik.clear()
        except asyncio.CancelledError:
            log.info("dispatcher zatrzymany")
            await self._anuluj_kopie()
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
            await self._przebieg(teraz)
            async with self._fabryka() as kontekst:
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

    async def _przebieg(self, teraz: dt.datetime) -> None:
        self._odczytane_w_obrocie.clear()
        async with self._fabryka() as kontekst, kontekst.uow as uow:
            zrodla = {z.key: z for z in await uow.source.wlaczone()}
            po_id = {z.id: z for z in zrodla.values() if z.id is not None}
        self._zrodla_dla_tempa = zrodla

        # Aukcja nieobserwowana nie jest odpytywana po raz drugi (§11.2),
        # a marker końca stoi wyłącznie na stronie szczegółów — bez tego
        # kroku zostawałaby `ACTIVE` na zawsze i siedziała w widoku
        # „Aktywne" długo po swoim terminie.
        async with self._fabryka() as kontekst, kontekst.uow as uow:
            zamkniete = await uow.auction.zamknij_po_terminie(teraz)
        if zamkniete:
            log.info("zamknięto %s aukcji po terminie", zamkniete)

        # Cena końcowa ma pierwszeństwo przed odkrywaniem kolejnych aukcji.
        # To dotyczy zwłaszcza autoprzetargu, gdzie okno po końcu trwa sekundy.
        await self._obsluz_zalegle(po_id)

        # Skan jest stronicowany. Po każdej stronie wracamy do zaległych
        # odczytów, więc wolne źródło ani długa lista nie blokują dogrywki.
        await asyncio.gather(
            *(
                self._moze_przemiec(do_przemiatu, po_id, teraz)
                for do_przemiatu in zrodla.values()
            )
        )

        # Backup nigdy nie stoi na krytycznej ścieżce odczytów.
        await self._moze_zrobic_kopie(teraz)

    async def _obsluz_zalegle(self, po_id: Mapping[int, Source]) -> None:
        """Obsługuje dojrzałe odpyty, najpierw domykanie i obserwowane."""
        async with self._fabryka() as kontekst, kontekst.uow as uow:
            teraz = await kontekst.zapytania.czas_serwera()
            zalegle = await uow.auction.do_odpytu(teraz, self._limit)

        do_zrobienia = [
            a
            for a in zalegle
            if a.id not in self._w_locie and a.id not in self._odczytane_w_obrocie
        ]
        if not do_zrobienia:
            return
        self._odczytane_w_obrocie.update(
            aukcja.id for aukcja in do_zrobienia if aukcja.id is not None
        )

        # Grupujemy po źródle, żeby `concurrency = 1` na serwis wynikało
        # z kształtu pętli, a nie z dyscypliny wywołań (§13).
        wedlug_zrodla: dict[int, list[Auction]] = {}
        for aukcja in do_zrobienia:
            wedlug_zrodla.setdefault(aukcja.source_id, []).append(aukcja)

        zadania = []
        for source_id, aukcje in wedlug_zrodla.items():
            zrodlo = po_id.get(source_id)
            if zrodlo is None:
                log.warning(
                    "aukcje wskazują na wyłączone źródło %s — pomijam", source_id
                )
                continue
            zadania.append(self._obsluz_zrodlo_niezaleznie(zrodlo, aukcje, teraz))
        if zadania:
            await asyncio.gather(*zadania)

    async def _obsluz_zrodlo_niezaleznie(
        self, zrodlo: Source, aukcje: list[Auction], teraz: dt.datetime
    ) -> None:
        """Daje źródłu osobne połączenie, aby czekanie nie blokowało sąsiadów."""
        async with self._fabryka() as kontekst:
            await self._obsluz_zrodlo(kontekst, zrodlo, aukcje, teraz)

    async def _moze_zrobic_kopie(self, teraz: dt.datetime) -> None:
        """Uruchamia najwyżej jeden backup; jego I/O nie zatrzymuje dispatchera."""
        if self._zadanie_kopii is not None and self._zadanie_kopii.done():
            try:
                self._zadanie_kopii.result()
            except (BladKopii, OSError) as exc:
                self._kolejne_bledy_kopii += 1
                odstep = min(300 * (2 ** (self._kolejne_bledy_kopii - 1)), 21_600)
                self._ponow_kopie_po = teraz + dt.timedelta(seconds=odstep)
                self._blad_kopii = str(exc)
                if self._kopia is not None:
                    self._kopia.odnotuj_blad(self._blad_kopii)
                log.warning(
                    "kopia bazy nie powiodła się: %s; ponowię za %s s", exc, odstep
                )
            else:
                self._kolejne_bledy_kopii = 0
                self._ponow_kopie_po = None
                self._blad_kopii = None
                if self._kopia is not None:
                    self._kopia.odnotuj_blad(None)
            self._zadanie_kopii = None

        if self._kopia is None or self._zadanie_kopii is not None:
            return
        if self._ponow_kopie_po is not None and teraz < self._ponow_kopie_po:
            return
        if not self._kopia.czas_na_kopie(teraz):
            return

        async def wykonaj() -> None:
            assert self._kopia is not None
            await self._kopia.wykonaj(teraz)

        self._zadanie_kopii = asyncio.create_task(
            wykonaj(), name="poleasingowe-pg_dump"
        )

    async def _anuluj_kopie(self) -> None:
        if self._zadanie_kopii is None:
            return
        self._zadanie_kopii.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._zadanie_kopii
        self._zadanie_kopii = None

    async def _moze_przemiec(
        self,
        zrodlo: Source,
        po_id: Mapping[int, Source],
        teraz: dt.datetime,
    ) -> None:
        """Zbiorczy przemiat listy, jeśli minął interwał źródła (SPEC.md §11.2).

        „Aukcje nieobserwowane nie są odpytywane pojedynczo w ogóle.
        Wystarcza im zbiorczy przemiat listy raz na kilka godzin. To główna
        oszczędność całego systemu — koszt rośnie z liczbą obserwowanych,
        nie z liczbą ofert w serwisie."

        `last_sweep_attempt_at = NULL` znaczy „nigdy", więc świeżo zainstalowany
        dodatek zapełnia się przy pierwszym obrocie, a nie po sześciu
        godzinach patrzenia na pustą listę.
        """
        if zrodlo.last_sweep_attempt_at is not None:
            od_ostatniego = (teraz - zrodlo.last_sweep_attempt_at).total_seconds()
            if od_ostatniego < zrodlo.sweep_interval_seconds:
                return

        adapter = self._zrodla.get(zrodlo.key)
        if adapter is None or zrodlo.id is None:
            return

        zegar_mono = asyncio.get_running_loop().time
        bezpiecznik = self._bezpieczniki.setdefault(zrodlo.key, Bezpiecznik())
        if bezpiecznik.otwarty(zegar_mono()):
            return

        async with self._fabryka() as kontekst, kontekst.uow as uow:
            przebieg = await uow.run_log.rozpocznij(
                RunLog(source_id=zrodlo.id, started_at=teraz)
            )

        nowe = 0
        bledy: list[str] = []
        liczba_pozycji = 0
        liczba_stron = 0
        status = SweepStatus.COMPLETE
        try:
            strony = adapter.strony_przemiatu()
            while True:
                try:
                    async with self.bramka_sieci(zrodlo.key):
                        strona = await anext(strony)
                except StopAsyncIteration:
                    break
                # Nowe aukcje dostają jeden odpyt szczegółów, ale dopiero po
                # krytycznych pozycjach istniejących już przed skanem.
                pozycje = [
                    replace(
                        z_cena_wywolawcza(adapter.na_aukcje(s, zrodlo.id, teraz)),
                        next_poll_at=teraz,
                        poll_tier=PollTier.FAR,
                    )
                    for s in strona.pozycje
                ]
                async with self._fabryka() as kontekst, kontekst.uow as uow:
                    nowe += await uow.auction.zapisz_z_przemiatu(pozycje)
                liczba_pozycji += len(pozycje)
                liczba_stron += 1
                await self._obsluz_zalegle(po_id)
            bezpiecznik.zglos_sukces()
        except (DomainError, OSError) as exc:
            bledy.append(f"przemiat: {type(exc).__name__}: {exc}")
            status = SweepStatus.PARTIAL if liczba_stron else SweepStatus.FAILED
            bezpiecznik.zglos_blad(zegar_mono())
            log.warning("przemiat listy %s nie powiódł się: %s", zrodlo.key, exc)

        zniknione = 0
        async with self._fabryka() as kontekst, kontekst.uow as uow:
            # Dwa PEŁNE skany są dowodem. Stan UNKNOWN po migracji i
            # PARTIAL po błędzie zrywają tę sekwencję.
            if (
                status is SweepStatus.COMPLETE
                and zrodlo.last_sweep_status is SweepStatus.COMPLETE
                and zrodlo.last_sweep_at is not None
                and zrodlo.id is not None
            ):
                zniknione = await uow.auction.oznacz_zniknione(
                    zrodlo.id, zrodlo.last_sweep_at, teraz
                )
            await uow.source.zapisz(
                replace(
                    zrodlo,
                    last_sweep_at=teraz
                    if status is SweepStatus.COMPLETE
                    else zrodlo.last_sweep_at,
                    last_sweep_attempt_at=teraz,
                    last_sweep_status=status,
                )
            )
            await uow.run_log.zakoncz(
                replace(
                    przebieg,
                    finished_at=await kontekst.zapytania.czas_serwera(),
                    new_count=nowe,
                    changed_count=liczba_pozycji,
                    error_count=len(bledy),
                    errors=bledy,
                    rss_bytes=rss_bajty(),
                    database_bytes=await kontekst.zapytania.rozmiar_bazy(),
                    notes=f"przemiat {status.value.lower()}",
                )
            )
        log.info(
            "przemiat %s: %s (%s stron), %s nowych%s",
            zrodlo.key,
            status.value,
            liczba_stron,
            nowe,
            f", {zniknione} zniknęło z listy" if zniknione else "",
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
                if await self._odpytaj(kontekst, adapter, zrodlo, aukcja):
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
    ) -> bool:
        """Jeden odpyt aukcji. Zwraca, czy coś się zmieniło."""
        teraz_wstepnie = await kontekst.zapytania.czas_serwera()
        domykanie = w_domykaniu(aukcja, teraz_wstepnie)

        # SPEC.md §11.5 — próby domknięcia są ZWOLNIONE z czekania na token.
        # Okno, w którym widać cenę końcową, trwa u autoprzetargu 15 sekund
        # (RECON.md §3.4); przeczekanie go w kolejce znaczy, że nie ma po co
        # było wysyłać żądania. Token i tak zużywamy, więc budżet źródła
        # pozostaje policzony — pożyczamy z przyszłości, nie udajemy, że
        # żądania nie było.
        # SPEC.md §11.3 krok 2: adapter porównuje hash surowych bajtów PRZED
        # parsowaniem i zwraca `None`, gdy treść się nie zmieniła. Krok 1
        # (żądanie warunkowe) jest martwy — żaden z czterech serwisów nie
        # zwraca `ETag` ani `Last-Modified` (RECON.md §3.1).
        async with self.bramka_sieci(zrodlo.key, domykanie=domykanie):
            surowa = await adapter.pobierz_szczegoly(
                aukcja.external_id, aukcja.content_hash, url=aukcja.url
            )

        teraz = await kontekst.zapytania.czas_serwera()
        assert aukcja.id is not None

        if surowa is None:
            # Treść bez zmian. W fazie domknięcia to nie jest „nic się nie
            # dzieje": serwis może jeszcze nie zdążyć oznaczyć aukcji jako
            # zakończonej (EFL robi to 5-7 minut po terminie), więc drabinka
            # ma się kręcić dalej.
            async with kontekst.uow as uow:
                obserwowana = await uow.watchlist.obserwowana(aukcja.id)
                await uow.auction.odnotuj_widziana(aukcja.id, teraz)
                await uow.auction.zapisz(
                    self._termin_po_odpycie(aukcja, zrodlo, teraz, obserwowana)
                )
            return False

        swieza = z_cena_wywolawcza(
            adapter.na_aukcje(surowa, zrodlo.id or aukcja.source_id, teraz)
        )

        async with kontekst.uow as uow:
            obserwowana = await uow.watchlist.obserwowana(aukcja.id)
            scalona = self._scal(aukcja, swieza, zrodlo, teraz, obserwowana)
            zapisana = await uow.auction.zapisz(scalona)
            zmieniony = None
            nowe_oferty = 0
            if zapisana.id is not None:
                # SPEC.md §11.8 — lista ofert ze strony aukcji ma pierwszeństwo
                # przed zgadywaniem z różnic `bid_count`. Przyszła tą samą
                # odpowiedzią co cena, więc nie kosztuje ani jednego żądania
                # więcej. Źródła, które jej nie podają, dają pustą krotkę.
                nowe_oferty = await uow.oferta.zapisz_nowe(
                    adapter.na_oferty(surowa, zapisana.id, teraz)
                    if isinstance(adapter, ZrodloZOfertami)
                    else ()
                )
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
                    ),
                    licznik_liczy_oferty=zrodlo.liczy_oferty,
                )
        # Nowa oferta to zmiana warta odnotowania nawet wtedy, gdy cena
        # została ta sama — przy licytacji proxy przebita oferta niższa
        # od maksimum zwycięzcy nie rusza ceny (RECON.md §3.5).
        return zmieniony is not None or nowe_oferty > 0

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

        Po terminie decyduje **drabinka domknięcia** z §11.5, a nie tabela
        progów: liczy się trafienie w okno, w którym serwis jeszcze pokazuje
        cenę końcową. Gdy drabinka się wyczerpie, aukcja zostaje zamknięta
        z ceną `LAST_SEEN` — bo tyle udało się zobaczyć.
        """
        if not obserwowana:
            return replace(
                aukcja, last_seen_at=teraz, next_poll_at=None, poll_tier=PollTier.IDLE
            )

        # „LAST MINUTE” nie niesie dokładnego terminu. Dla obserwowanej
        # aukcji nie czekamy więc do następnego pełnego skanu ani nie
        # wymyślamy `ends_at`: ponawiamy po bezpiecznym floorze źródła.
        if aukcja.status is AuctionStatus.ACTIVE and aukcja.ends_at is None:
            return replace(
                aukcja,
                last_seen_at=teraz,
                next_poll_at=teraz + dt.timedelta(seconds=floor_zrodla(zrodlo)),
                poll_tier=PollTier.ENDGAME,
            )

        if w_domykaniu(aukcja, teraz):
            nastepna = nastepny_krok_drabinki(aukcja, zrodlo, teraz)
            if nastepna is None:
                return po_wyczerpaniu_drabinki(aukcja, teraz)
            return replace(
                aukcja,
                last_seen_at=teraz,
                next_poll_at=nastepna,
                poll_tier=PollTier.CLOSING,
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

        # SPEC.md §11.5 — serwis SAM mówi, że aukcja się skończyła
        # (`auction_pending:false`, nagłówek `Zakończona`). Dopiero wtedy
        # widziana cena jest ceną KOŃCOWĄ, a nie ostatnią zaobserwowaną;
        # §9 liczy z tego rozróżnienia osobne mediany rynku.
        if swieza.status is AuctionStatus.ENDED:
            potwierdzona = po_odczycie_po_terminie(
                scalona, teraz, serwis_potwierdza_koniec=True
            )
            log.info(
                "cena końcowa potwierdzona w %s/%s: %s",
                zrodlo.key,
                stara.external_id,
                potwierdzona.price_current,
            )
            return potwierdzona
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
