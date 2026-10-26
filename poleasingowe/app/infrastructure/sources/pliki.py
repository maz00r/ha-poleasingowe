"""Pobieranie plików dołączonych do aukcji (ekspertyzy PDF, 0.31.0).

Wspólne dla adapterów, bo zasady są te same: plik tylko z hostów źródła,
twardy limit rozmiaru i sprawdzenie, że to naprawdę PDF. Strona błędu albo
WAF zapisany jako „ekspertyza" byłby gorszy niż brak pliku — wyglądałby
jak dokument.
"""

from __future__ import annotations

from collections.abc import Collection
from urllib.parse import urlsplit

import httpx

from app.domain.errors import ParseFailed, SourceUnavailable

LIMIT_BAJTOW = 25 * 1024 * 1024
"""Największa zmierzona ekspertyza miała 7,9 MB (Leasygroup, 13 stron)."""


def dozwolony(url: str, hosty: Collection[str]) -> bool:
    adres = urlsplit(url)
    return adres.scheme == "https" and adres.hostname in hosty


async def pobierz_pdf(
    klient: httpx.AsyncClient,
    url: str,
    *,
    hosty: Collection[str],
    zrodlo: str,
    limit: int = LIMIT_BAJTOW,
) -> bytes:
    """Bajty PDF-a albo wyjątek — nigdy nic pośredniego."""
    if not dozwolony(url, hosty):
        raise ParseFailed(f"{zrodlo}: plik spoza hostów źródła: {url}")
    bufor = bytearray()
    try:
        async with klient.stream("GET", url) as odpowiedz:
            odpowiedz.raise_for_status()
            async for kawalek in odpowiedz.aiter_bytes():
                bufor.extend(kawalek)
                if len(bufor) > limit:
                    raise ParseFailed(f"{zrodlo}: plik większy niż {limit} B")
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"{zrodlo}: plik {url}: {exc}") from exc
    if not bytes(bufor[:1024]).lstrip().startswith(b"%PDF"):
        raise ParseFailed(f"{zrodlo}: plik nie jest PDF-em: {url}")
    return bytes(bufor)
