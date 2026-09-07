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
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from typing import Any

from fastapi import FastAPI, Request, Response

from app.infrastructure.supervisor.options import Opcje

log = logging.getLogger(__name__)

NAGLOWEK_INGRESS = "X-Ingress-Path"


class StanAplikacji:
    """Współdzielony stan procesu. Jeden proces, jeden event loop (§7.1)."""

    def __init__(self, opcje: Opcje) -> None:
        self.opcje = opcje
        self.baza_dostepna = False
        self.ostatni_blad_bazy: str | None = None


def _middleware_ingress(app: FastAPI) -> None:
    """Ustawia `root_path` z nagłówka, żeby `url_for` budował dobre adresy.

    Home Assistant montuje add-on pod losowym prefiksem, innym po każdym
    restarcie, więc prefiksu nie da się skonfigurować z góry.
    """

    @app.middleware("http")
    async def ustaw_prefiks(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        prefiks = request.headers.get(NAGLOWEK_INGRESS)
        if prefiks:
            request.scope["root_path"] = prefiks.rstrip("/")
        return await call_next(request)


ZadanieTla = Callable[[], Coroutine[Any, Any, None]]


def utworz_aplikacje(
    opcje: Opcje, zadania_tla: list[ZadanieTla] | None = None
) -> FastAPI:
    """Buduje aplikację. Zależności wstrzykiwane, bez globalnych singletonów.

    `zadania_tla` startują w `lifespan` i są anulowane przy zamknięciu.
    Uwaga: gdy aplikacja ma `lifespan`, Starlette **ignoruje** handlery
    `on_startup` — dlatego zadania trzeba podać tutaj, a nie dopinać po
    utworzeniu aplikacji.
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

    app = FastAPI(
        title="Aukcje poleasingowe",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=cykl_zycia,
    )
    app.state.stan = StanAplikacji(opcje)
    _middleware_ingress(app)

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

    return app
