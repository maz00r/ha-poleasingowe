"""Klienci modeli językowych (SPEC.md §7.1).

Wycena potrzebuje jednej rzeczy: **JSON-a zgodnego z podanym schematem**.
To, kto go wyprodukuje, jest wyborem użytkownika, nie założeniem aplikacji —
stąd ten moduł. `wycena_ai.py` zajmuje się promptem, cache'em i sprawdzeniem
wyniku; tutaj mieszka wyłącznie transport i dziwactwa protokołów.

Trzy protokoły pokrywają praktycznie cały rynek:

| `ai_provider` | Endpoint | Kto |
|---|---|---|
| `openai` | `/v1/responses` | OpenAI |
| `anthropic` | `/v1/messages` | Anthropic |
| `zgodny_z_openai` | `/v1/chat/completions` | OpenRouter, Groq, DeepSeek,
  Mistral, xAI, Together, Google (endpoint zgodny), a także Ollama
  i LM Studio na własnym serwerze |

Każdy z nich wymusza strukturę wyniku **po swojemu** i to jest jedyny
powód, dla którego są tu trzy klasy, a nie jedna z parametrem adresu:

- OpenAI: `text.format` z `json_schema` i `strict`,
- zgodny z OpenAI: `response_format` z `json_schema` w treści `message`,
- Anthropic: wymuszone narzędzie (`tool_choice`), a wynik siedzi
  w `input` bloku `tool_use` — nie w tekście.

Model lokalny (Ollama, LM Studio) jest tu pełnoprawnym wyborem: `ai_base_url`
wskazujący na `http://…:11434/v1` sprawia, że dane pojazdu nie opuszczają
sieci domowej. Add-on nie ma jednak prawa dobierać niczego za użytkownika —
adres musi podać sam.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

import httpx

Dostawca = Literal["openai", "anthropic", "zgodny_z_openai"]

DOSTAWCY: tuple[Dostawca, ...] = ("openai", "anthropic", "zgodny_z_openai")

ADRESY: dict[Dostawca, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}
"""Adresy domyślne. `zgodny_z_openai` celowo go nie ma — to właśnie ten
wybór znaczy „podaję własny adres"."""

MODELE: dict[Dostawca, str] = {
    "openai": "gpt-5.4-mini",
    "anthropic": "claude-sonnet-5",
}
"""Model używany, gdy `ai_model` zostanie pusty."""

WERSJA_ANTHROPIC = "2023-06-01"
MAKS_TOKENOW = 2048
"""Sufit odpowiedzi. Wycena to kilka zdań i sześć pól — z zapasem."""


class BladModelu(RuntimeError):
    """Model nie oddał wyniku w umówionym kształcie."""


class KlientModelu(Protocol):
    """Jedna operacja: zapytaj i oddaj JSON wg schematu."""

    model: str

    async def json_wg_schematu(
        self,
        *,
        instrukcje: str,
        wejscie: str,
        nazwa_schematu: str,
        schemat: dict[str, Any],
    ) -> str: ...

    async def zamknij(self) -> None: ...


class _Bazowy:
    """Wspólny kawałek: leniwy klient HTTP i jego domykanie.

    Klient tworzy się przy pierwszym użyciu, bo wycena bywa nigdy nie
    wywołana — a wtedy nie ma powodu trzymać otwartej puli połączeń.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        base_url: str,
        klient: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._klient = klient
        self._wlasny_klient = klient is None
        self._timeout = timeout

    async def _http(self) -> httpx.AsyncClient:
        if self._klient is None:
            self._klient = httpx.AsyncClient(timeout=self._timeout)
        return self._klient

    async def zamknij(self) -> None:
        if self._wlasny_klient and self._klient is not None:
            await self._klient.aclose()
            self._klient = None

    async def _poslij(
        self, sciezka: str, *, naglowki: dict[str, str], tresc: dict[str, Any]
    ) -> dict[str, Any]:
        klient = await self._http()
        odpowiedz = await klient.post(
            f"{self._base_url}{sciezka}", headers=naglowki, json=tresc
        )
        odpowiedz.raise_for_status()
        wynik: dict[str, Any] = odpowiedz.json()
        return wynik


class KlientOpenAI(_Bazowy):
    """OpenAI Responses API."""

    async def json_wg_schematu(
        self,
        *,
        instrukcje: str,
        wejscie: str,
        nazwa_schematu: str,
        schemat: dict[str, Any],
    ) -> str:
        odpowiedz = await self._poslij(
            "/responses",
            naglowki={"Authorization": f"Bearer {self._api_key}"},
            tresc={
                "model": self.model,
                # Zapytanie nie ma zostawiać śladu po stronie dostawcy —
                # to dane o pojeździe użytkownika, nie materiał treningowy.
                "store": False,
                "instructions": instrukcje,
                "input": wejscie,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": nazwa_schematu,
                        "strict": True,
                        "schema": schemat,
                    }
                },
            },
        )
        for element in odpowiedz.get("output", []):
            for tresc in element.get("content", []):
                if tresc.get("type") == "output_text" and tresc.get("text"):
                    return str(tresc["text"])
        raise BladModelu("OpenAI nie zwróciło treści wyceny.")


class KlientZgodnyZOpenAI(_Bazowy):
    """Endpoint `chat/completions` — wspólny mianownik reszty rynku.

    Nie każdy dostawca honoruje `json_schema`; część zna tylko
    `{"type": "json_object"}`, a część nic. Dlatego schemat idzie
    **dodatkowo w treści polecenia** — wtedy nawet zignorowany
    `response_format` nie psuje wyniku, bo model i tak wie, czego od niego
    chcemy. Walidacja i tak stoi po naszej stronie (`wycena_ai`).
    """

    async def json_wg_schematu(
        self,
        *,
        instrukcje: str,
        wejscie: str,
        nazwa_schematu: str,
        schemat: dict[str, Any],
    ) -> str:
        odpowiedz = await self._poslij(
            "/chat/completions",
            naglowki={"Authorization": f"Bearer {self._api_key}"},
            tresc={
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"{instrukcje}\n\nOdpowiedz WYŁĄCZNIE obiektem JSON "
                            f"zgodnym ze schematem:\n"
                            f"{json.dumps(schemat, ensure_ascii=False)}"
                        ),
                    },
                    {"role": "user", "content": wejscie},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": nazwa_schematu,
                        "strict": True,
                        "schema": schemat,
                    },
                },
            },
        )
        wybory = odpowiedz.get("choices") or []
        if not wybory:
            raise BladModelu("Dostawca nie zwrócił żadnej odpowiedzi.")
        tresc = (wybory[0].get("message") or {}).get("content")
        if not tresc:
            raise BladModelu("Dostawca zwrócił pustą odpowiedź.")
        return str(tresc)


class KlientAnthropic(_Bazowy):
    """Anthropic Messages API — struktura przez wymuszone narzędzie.

    Anthropic nie ma `response_format`; kanoniczny sposób na sztywny kształt
    wyniku to narzędzie z `input_schema` i `tool_choice` wskazujące na nie.
    Wynik przychodzi wtedy jako `input` bloku `tool_use`, czyli **gotowy
    obiekt**, a nie tekst do sparsowania — dlatego pakujemy go z powrotem
    do JSON-a, żeby warstwa wyżej miała jedno wejście dla wszystkich
    dostawców.
    """

    async def json_wg_schematu(
        self,
        *,
        instrukcje: str,
        wejscie: str,
        nazwa_schematu: str,
        schemat: dict[str, Any],
    ) -> str:
        odpowiedz = await self._poslij(
            "/messages",
            naglowki={
                "x-api-key": self._api_key,
                "anthropic-version": WERSJA_ANTHROPIC,
            },
            tresc={
                "model": self.model,
                "max_tokens": MAKS_TOKENOW,
                "system": instrukcje,
                "messages": [{"role": "user", "content": wejscie}],
                "tools": [
                    {
                        "name": nazwa_schematu,
                        "description": "Zapisz wycenę pojazdu.",
                        "input_schema": schemat,
                    }
                ],
                "tool_choice": {"type": "tool", "name": nazwa_schematu},
            },
        )
        for blok in odpowiedz.get("content", []):
            if blok.get("type") == "tool_use" and blok.get("name") == nazwa_schematu:
                return json.dumps(blok.get("input"), ensure_ascii=False)
        raise BladModelu("Anthropic nie użyło narzędzia wyceny.")


def utworz_klienta(
    dostawca: Dostawca,
    api_key: str,
    *,
    model: str = "",
    base_url: str = "",
    klient: httpx.AsyncClient | None = None,
) -> KlientModelu:
    """Buduje klienta dla wybranego dostawcy.

    Rzuca `ValueError` przy `zgodny_z_openai` bez adresu i bez modelu —
    tego add-on nie ma prawa zgadnąć, a domyślny OpenAI byłby wtedy
    wysłaniem danych gdzie indziej, niż użytkownik chciał (§7.1: błędna
    konfiguracja to jasny komunikat, nigdy ciche wartości domyślne).
    """
    if dostawca not in DOSTAWCY:
        raise ValueError(f"nieznany dostawca AI: {dostawca!r}")

    adres = base_url.strip() or ADRESY.get(dostawca, "")
    if not adres:
        raise ValueError(
            "ai_provider = 'zgodny_z_openai' wymaga podania ai_base_url "
            "(np. https://openrouter.ai/api/v1 albo http://localhost:11434/v1)"
        )
    nazwa_modelu = model.strip() or MODELE.get(dostawca, "")
    if not nazwa_modelu:
        raise ValueError(
            f"dostawca {dostawca!r} nie ma modelu domyślnego — uzupełnij ai_model"
        )

    klasy: dict[Dostawca, type[_Bazowy]] = {
        "openai": KlientOpenAI,
        "anthropic": KlientAnthropic,
        "zgodny_z_openai": KlientZgodnyZOpenAI,
    }
    utworzony = klasy[dostawca](
        api_key, model=nazwa_modelu, base_url=adres, klient=klient
    )
    # `_Bazowy` nie zna `json_wg_schematu`; konkretne klasy owszem.
    return utworzony  # type: ignore[return-value]
