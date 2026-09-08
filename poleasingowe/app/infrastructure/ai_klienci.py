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


class BladOdpowiedzi(BladModelu):
    """Dostawca odmówił — z jego własnym uzasadnieniem.

    Samo „HTTP 400" jest bezużyteczne dokładnie wtedy, gdy jest potrzebne:
    tym kodem dostawca odpowiada i na nieznany model, i na nieobsługiwany
    `response_format`, a to dwie zupełnie różne naprawy. Powód stoi w treści
    odpowiedzi, więc niesiemy ją dalej zamiast wyrzucać.
    """

    def __init__(self, status: int, tresc: str) -> None:
        self.status = status
        self.tresc = tresc
        super().__init__(f"HTTP {status}: {tresc}" if tresc else f"HTTP {status}")

    @property
    def powod(self) -> str:
        """Krótkie zdanie dla użytkownika, bez echa poświadczeń.

        Przy 401/403 nie pokazujemy treści od dostawcy: bywa w niej fragment
        klucza (DeepSeek odsyła `Your api key: ****lowy is invalid`), a klucz
        nie ma prawa trafić na ekran ani do logu (SPEC.md §10.2).
        """
        if self.status in (401, 403):
            return "dostawca odrzucił klucz API"
        return self.tresc or f"dostawca odpowiedział kodem {self.status}"


MAKS_TRESCI_BLEDU = 300
"""Ile znaków uzasadnienia od dostawcy przepuszczamy dalej. Tyle wystarcza
na `Model Not Exist` czy `response_format is not supported`, a nie zaleje
logu stroną HTML od pośredniczącego proxy."""


def _komunikat_bledu(odpowiedz: httpx.Response) -> str:
    """Wyciąga zdanie z odpowiedzi błędu, niezależnie od jej kształtu.

    Dostawcy pakują to różnie: OpenAI i DeepSeek w `error.message`, część
    bramek w `message` albo `detail`, a lokalne serwery bywają, że w czysty
    tekst. Zaglądamy po kolei, a w ostateczności bierzemy początek treści.
    """
    try:
        dane = odpowiedz.json()
    except ValueError:
        return odpowiedz.text.strip()[:MAKS_TRESCI_BLEDU]
    if isinstance(dane, dict):
        blad = dane.get("error")
        if isinstance(blad, dict) and blad.get("message"):
            return str(blad["message"])[:MAKS_TRESCI_BLEDU]
        for klucz in ("message", "detail", "error"):
            if isinstance(dane.get(klucz), str) and dane[klucz]:
                return str(dane[klucz])[:MAKS_TRESCI_BLEDU]
    return odpowiedz.text.strip()[:MAKS_TRESCI_BLEDU]


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
        if odpowiedz.is_error:
            raise BladOdpowiedzi(odpowiedz.status_code, _komunikat_bledu(odpowiedz))
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

    Wymuszanie struktury jest tu **niejednolite**, i to jest cała trudność
    tej klasy. `json_schema` rozumie OpenAI i część bramek; DeepSeek
    dokumentuje wyłącznie `{"type": "json_object"}`, a lokalne serwery bywają
    na to obojętne albo odrzucają nieznany typ błędem 400.

    Stąd dwa zabezpieczenia, które działają niezależnie od siebie:

    1. **Schemat idzie zawsze w treści polecenia**, nie tylko w
       `response_format`. Dostawca, który to pole ignoruje, i tak wie, czego
       od niego chcemy.
    2. Gdy dostawca **odrzuci** `json_schema` (400/422), ponawiamy raz
       w trybie `json_object`. To jest jedyna sytuacja, w której ponawiamy:
       401 czy 429 znaczą co innego i powtórka nic by nie dała.

    Kształt wyniku sprawdzamy i tak po swojej stronie (`wycena_ai`), więc
    nawet dostawca bez żadnego trybu JSON jest użyteczny, o ile odpowie
    obiektem.
    """

    async def json_wg_schematu(
        self,
        *,
        instrukcje: str,
        wejscie: str,
        nazwa_schematu: str,
        schemat: dict[str, Any],
    ) -> str:
        # Słowo „JSON" w poleceniu jest wymogiem części dostawców (m.in.
        # DeepSeeka) przy trybie `json_object` — bez niego odmawiają.
        tresc: dict[str, Any] = {
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
        }
        naglowki = {"Authorization": f"Bearer {self._api_key}"}
        try:
            odpowiedz = await self._poslij(
                "/chat/completions", naglowki=naglowki, tresc=tresc
            )
        except BladOdpowiedzi as exc:
            if exc.status not in (400, 422):
                raise
            tresc["response_format"] = {"type": "json_object"}
            odpowiedz = await self._poslij(
                "/chat/completions", naglowki=naglowki, tresc=tresc
            )

        wybory = odpowiedz.get("choices") or []
        if not wybory:
            raise BladModelu("Dostawca nie zwrócił żadnej odpowiedzi.")
        wiadomosc = (wybory[0].get("message") or {}).get("content")
        if not wiadomosc:
            raise BladModelu("Dostawca zwrócił pustą odpowiedź.")
        return str(wiadomosc)


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
