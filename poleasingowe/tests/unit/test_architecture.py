"""Testy pilnujące, że szkielet warstw jest tym, za co się podaje.

Kontrakt warstw egzekwuje import-linter (SPEC.md §6.1); te testy łapią to,
czego import-linter nie widzi: że warstwa domenowa nie ma zależności spoza
biblioteki standardowej i że pakiety w ogóle się importują.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

import pytest

LAYERS = [
    "app.domain",
    "app.application",
    "app.infrastructure",
    "app.interfaces",
]

# Paczki zewnetrzne, ktorych domain/ nie ma prawa importowac (SPEC.md §6).
ZABRONIONE_W_DOMENIE = frozenset(
    {
        "httpx",
        "psycopg",
        "psycopg_pool",
        "fastapi",
        "starlette",
        "jinja2",
        "selectolax",
        "pydantic",
    }
)


@pytest.mark.parametrize("nazwa", LAYERS)
def test_warstwa_sie_importuje(nazwa: str) -> None:
    importlib.import_module(nazwa)


def test_domena_nie_ciagnie_zaleznosci_zewnetrznych() -> None:
    """`domain/` ma stać wyłącznie na bibliotece standardowej (SPEC.md §6).

    Importujemy każdy moduł domeny w świeżym stanie i sprawdzamy, co przez to
    weszło do `sys.modules`. To wyłapuje zależność wciągniętą pośrednio, której
    lista importów w pliku nie pokazuje.
    """
    domain = importlib.import_module("app.domain")
    przed = set(sys.modules)
    for info in pkgutil.walk_packages(domain.__path__, f"{domain.__name__}."):
        importlib.import_module(info.name)
    nowe = {m.split(".")[0] for m in set(sys.modules) - przed}
    naruszenia = sorted(nowe & ZABRONIONE_W_DOMENIE)
    assert not naruszenia, f"domain/ wciągnął zależności zewnętrzne: {naruszenia}"
