"""Aplikacja FastAPI wystawiana przez Ingress (SPEC.md §7.1).

Ingress zapewnia uwierzytelnienie Home Assistant — **nie dokładamy własnego
logowania**. Prefiks ścieżki jest dynamiczny i przychodzi w nagłówku
`X-Ingress-Path`, więc w szablonach wolno używać wyłącznie `url_for`,
nigdy ścieżek na sztywno.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import pathlib
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.application.ports import FabrykaKontekstu
from app.infrastructure.supervisor.options import Opcje
from app.interfaces.web.widoki import router as router_web

STATYKI = pathlib.Path(__file__).resolve().parent / "static"

log = logging.getLogger(__name__)


class StanAplikacji:
    """Współdzielony stan procesu. Jeden proces, jeden event loop (§7.1)."""

    def __init__(self, opcje: Opcje) -> None:
        self.opcje = opcje
        self.baza_dostepna = False
        self.ostatni_blad_bazy: str | None = None


ZadanieTla = Callable[[], Coroutine[Any, Any, None]]


def utworz_aplikacje(
    opcje: Opcje,
    zadania_tla: list[ZadanieTla] | None = None,
    fabryka: FabrykaKontekstu | None = None,
    galeria: Any = None,
) -> FastAPI:
    """Buduje aplikację. Zależności wstrzykiwane, bez globalnych singletonów.

    `zadania_tla` startują w `lifespan` i są anulowane przy zamknięciu.
    Uwaga: gdy aplikacja ma `lifespan`, Starlette **ignoruje** handlery
    `on_startup` — dlatego zadania trzeba podać tutaj, a nie dopinać po
    utworzeniu aplikacji.

    `fabryka` może być `None` — wtedy interfejs wstaje bez bazy i mówi,
    dlaczego jej nie ma (SPEC.md §2 pkt 8, §12). To nie jest tryb testowy,
    tylko normalny stan po restarcie Home Assistanta, gdy Postgres wstaje
    wolniej niż add-on.
    """
    zadania_tla = zadania_tla or []

    @contextlib.asynccontextmanager
    async def cykl_zycia(_: FastAPI) -> AsyncIterator[None]:
        log.info(
            "start add-onu; baza: %s, poziom logów: %s",
            opcje.bezpieczny_opis(),
            opcje.log_level,
        )
        uruchomione = [asyncio.create_task(z()) for z in zadania_tla]
        try:
            yield
        finally:
            # SPEC.md §7.1 — poprawne zamkniecie na SIGTERM: dispatcher konczy
            # biezacy odpyt, sesje httpx i pula polaczen domykaja sie czysto.
            log.info("zatrzymywanie add-onu")
            for zadanie in uruchomione:
                zadanie.cancel()
            for zadanie in uruchomione:
                with contextlib.suppress(asyncio.CancelledError):
                    await zadanie
            if fabryka is not None:
                await fabryka.zamknij()
            if galeria is not None:
                await galeria.zamknij()

    app = FastAPI(
        title="Aukcje poleasingowe",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=cykl_zycia,
    )
    app.state.stan = StanAplikacji(opcje)
    app.state.fabryka = fabryka
    # Galeria zdjęć jest opcjonalna: bez niej karta aukcji po prostu
    # nie pokazuje zdjęć (SPEC.md §12).
    app.state.galeria = galeria
    # `html=False`: to katalog na CSS i HTMX, nie na strony. Bez tego
    # StaticFiles zaczalby serwowac index.html z dowolnego podkatalogu.
    app.mount("/static", StaticFiles(directory=str(STATYKI), html=False), name="static")

    @app.get("/zdrowie")
    async def zdrowie(request: Request) -> dict[str, Any]:
        """Stan procesu dla panelu diagnostycznego (SPEC.md §12).

        Celowo NIE zwraca DSN-u ani hasła — tylko bezpieczny opis połączenia
        (§10.2).
        """
        stan: StanAplikacji = request.app.state.stan
        return {
            "baza": {
                "polaczenie": stan.opcje.bezpieczny_opis(),
                "dostepna": stan.baza_dostepna,
                "ostatni_blad": stan.ostatni_blad_bazy,
            },
            "zrodla": [z.key for z in stan.opcje.sources if z.enabled],
            "debug_dumps": stan.opcje.debug_dumps,
        }

    # Router na koncu: `/zdrowie` i `/static` maja pierwszenstwo przed
    # trasami widokow, zeby zadna zmiana w §12 ich nie przeslonila.
    app.include_router(router_web)
    return app
