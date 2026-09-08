"""Wycena AI: format żądania, walidacja odpowiedzi i cache."""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest

from app.application.read_models import PorownanieRynkowe, PozycjaListy, Szczegoly
from app.domain.enums import AuctionStatus
from app.infrastructure.ai_klienci import (
    BladModelu,
    KlientAnthropic,
    KlientOpenAI,
    KlientZgodnyZOpenAI,
    utworz_klienta,
)
from app.infrastructure.wycena_ai import BladWyceny, WycenaAI


def _dane() -> Szczegoly:
    return Szczegoly(
        pozycja=PozycjaListy(
            id=7,
            source_key="efl",
            external_id="tajny-identyfikator",
            url="https://example.test/7",
            status=AuctionStatus.ACTIVE,
            make="Škoda",
            model="Octavia",
            variant="Style",
            year=2022,
            mileage_km=120_000,
            fuel="Diesel",
            gearbox="Automatyczna",
        ),
        vin="TMBTA7NE0N0123456",
        seller="Jan Kowalski",
        engine_ccm=1968,
        engine_hp=150,
        body="Kombi",
    )


async def test_wycena_ma_strukturalny_wynik_i_nie_wysyla_vin(
    tmp_path: pathlib.Path,
) -> None:
    zadania: list[dict[str, object]] = []

    def odpowiedz(request: httpx.Request) -> httpx.Response:
        zadania.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "wartosc": 78000,
                                        "minimum": 72000,
                                        "maksimum": 84000,
                                        "pewnosc": "średnia",
                                        "uzasadnienie": "Dwie podobne aukcje.",
                                        "zalozenia": ["Brak poważnych szkód."],
                                    }
                                ),
                            }
                        ],
                    }
                ]
            },
        )

    klient = httpx.AsyncClient(transport=httpx.MockTransport(odpowiedz))
    usluga = WycenaAI(
        KlientOpenAI(
            "sekretny-klucz",
            model="model-testowy",
            base_url="https://api.openai.com/v1",
            klient=klient,
        ),
        katalog=tmp_path,
    )
    porownania = (PorownanieRynkowe(2022, 77_000, 2, None, 0),)

    wynik = await usluga.wycen(_dane(), porownania)
    ponownie = await usluga.wycen(_dane(), porownania)

    assert wynik.wartosc == 78_000
    assert ponownie == wynik
    assert len(zadania) == 1, "drugi odczyt powinien trafić do cache'u"
    wyslane = json.dumps(zadania, ensure_ascii=False)
    assert "TMBTA7NE0N0123456" not in wyslane
    assert "Jan Kowalski" not in wyslane
    assert zadania[0]["store"] is False
    assert zadania[0]["text"]["format"]["type"] == "json_schema"  # type: ignore[index]

    await klient.aclose()


# --------------------------------------------------------------------------
# Wybór dostawcy: ten sam wynik, trzy różne protokoły
# --------------------------------------------------------------------------

WYNIK = {
    "wartosc": 78000,
    "minimum": 72000,
    "maksimum": 84000,
    "pewnosc": "średnia",
    "uzasadnienie": "Dwie podobne aukcje.",
    "zalozenia": ["Brak poważnych szkód."],
}


def _przechwyc(
    odpowiedz_json: dict[str, object],
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    zadania: list[httpx.Request] = []

    def obsluz(request: httpx.Request) -> httpx.Response:
        zadania.append(request)
        return httpx.Response(200, json=odpowiedz_json)

    return httpx.AsyncClient(transport=httpx.MockTransport(obsluz)), zadania


async def test_anthropic_wymusza_strukture_narzedziem(
    tmp_path: pathlib.Path,
) -> None:
    """Anthropic nie ma `response_format` — kształt wymusza się narzędziem,
    a wynik przychodzi jako `input` bloku `tool_use`, nie jako tekst."""
    klient, zadania = _przechwyc(
        {
            "content": [
                {"type": "text", "text": "chwila"},
                {"type": "tool_use", "name": "wycena_pojazdu", "input": WYNIK},
            ]
        }
    )
    usluga = WycenaAI(
        KlientAnthropic(
            "klucz-anthropic",
            model="claude-sonnet-5",
            base_url="https://api.anthropic.com/v1",
            klient=klient,
        ),
        katalog=tmp_path,
    )

    wynik = await usluga.wycen(_dane(), ())
    assert wynik.wartosc == 78_000
    assert wynik.model == "claude-sonnet-5"

    zadanie = zadania[0]
    assert zadanie.url.path.endswith("/messages")
    # Klucz idzie nagłówkiem `x-api-key`, nie `Authorization` — inny protokół,
    # inne uwierzytelnienie.
    assert zadanie.headers["x-api-key"] == "klucz-anthropic"
    assert zadanie.headers["anthropic-version"]
    tresc = json.loads(zadanie.content)
    assert tresc["tool_choice"] == {"type": "tool", "name": "wycena_pojazdu"}
    assert tresc["tools"][0]["input_schema"]["required"]
    assert "TMBTA7NE0N0123456" not in zadanie.content.decode()

    await klient.aclose()


async def test_dostawca_zgodny_z_openai_uzywa_chat_completions(
    tmp_path: pathlib.Path,
) -> None:
    """OpenRouter, Groq, DeepSeek, Ollama — wszystkie mówią tym samym
    protokołem, więc różnicą jest wyłącznie adres."""
    klient, zadania = _przechwyc(
        {"choices": [{"message": {"content": json.dumps(WYNIK)}}]}
    )
    usluga = WycenaAI(
        KlientZgodnyZOpenAI(
            "klucz",
            model="qwen3:14b",
            base_url="http://localhost:11434/v1",
            klient=klient,
        ),
        katalog=tmp_path,
    )

    wynik = await usluga.wycen(_dane(), ())
    assert wynik.wartosc == 78_000
    assert str(zadania[0].url) == "http://localhost:11434/v1/chat/completions"
    tresc = json.loads(zadania[0].content)
    # Schemat idzie i w `response_format`, i w poleceniu — część dostawców
    # ignoruje to pierwsze.
    assert tresc["response_format"]["json_schema"]["name"] == "wycena_pojazdu"
    assert "wartosc" in tresc["messages"][0]["content"]

    await klient.aclose()


async def test_niespojny_przedzial_jest_odrzucany(tmp_path: pathlib.Path) -> None:
    """Schematu pilnuje dostawca, ale `minimum <= wartosc <= maksimum` nie da
    się w nim wyrazić — a taki wynik wygląda wiarygodnie i byłby mylący."""
    klient, _ = _przechwyc(
        {
            "choices": [
                {"message": {"content": json.dumps({**WYNIK, "minimum": 90_000})}}
            ]
        }
    )
    usluga = WycenaAI(
        KlientZgodnyZOpenAI(
            "klucz", model="m", base_url="http://localhost:1234/v1", klient=klient
        ),
        katalog=tmp_path,
    )
    with pytest.raises(BladWyceny):
        await usluga.wycen(_dane(), ())
    await klient.aclose()


async def test_brak_narzedzia_w_odpowiedzi_to_blad_a_nie_cisza(
    tmp_path: pathlib.Path,
) -> None:
    klient, _ = _przechwyc({"content": [{"type": "text", "text": "nie umiem"}]})
    usluga = KlientAnthropic(
        "k", model="m", base_url="https://api.anthropic.com/v1", klient=klient
    )
    with pytest.raises(BladModelu):
        await usluga.json_wg_schematu(
            instrukcje="i", wejscie="{}", nazwa_schematu="wycena_pojazdu", schemat={}
        )
    await klient.aclose()


def test_wlasny_dostawca_bez_adresu_jest_bledem_konfiguracji() -> None:
    """Domyślne wpadnięcie w OpenAI byłoby wysłaniem danych gdzie indziej,
    niż użytkownik chciał (SPEC.md §7.1)."""
    with pytest.raises(ValueError, match="ai_base_url"):
        utworz_klienta("zgodny_z_openai", "klucz", model="m")
    with pytest.raises(ValueError, match="ai_model"):
        utworz_klienta("zgodny_z_openai", "klucz", base_url="http://x/v1")
    with pytest.raises(ValueError, match="nieznany dostawca"):
        utworz_klienta("wymyslony", "klucz")  # type: ignore[arg-type]


def test_dostawcy_maja_domyslne_adresy_i_modele() -> None:
    """Użytkownik podaje sam klucz i to ma wystarczyć."""
    assert utworz_klienta("openai", "k").model == "gpt-5.4-mini"
    assert utworz_klienta("anthropic", "k").model == "claude-sonnet-5"


async def test_odrzucony_json_schema_konczy_sie_ponowieniem_w_json_object(
    tmp_path: pathlib.Path,
) -> None:
    """DeepSeek dokumentuje wyłącznie `json_object`, a nieznany typ
    `response_format` bywa odrzucany błędem 400 — nie ignorowany.

    Bez tego ponowienia wybór takiego dostawcy dawałby wycenę, która nigdy
    się nie udaje, i komunikat „nie udało się" bez wskazania przyczyny.
    """
    formaty: list[str] = []

    def obsluz(request: httpx.Request) -> httpx.Response:
        tresc = json.loads(request.content)
        formaty.append(tresc["response_format"]["type"])
        if tresc["response_format"]["type"] == "json_schema":
            return httpx.Response(
                400,
                json={"error": {"message": "response_format.type unsupported"}},
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(WYNIK)}}]}
        )

    klient = httpx.AsyncClient(transport=httpx.MockTransport(obsluz))
    usluga = WycenaAI(
        KlientZgodnyZOpenAI(
            "sk-deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            klient=klient,
        ),
        katalog=tmp_path,
    )

    wynik = await usluga.wycen(_dane(), ())
    assert wynik.wartosc == 78_000
    assert formaty == ["json_schema", "json_object"]
    await klient.aclose()


async def test_zly_klucz_nie_jest_ponawiany(tmp_path: pathlib.Path) -> None:
    """401 znaczy co innego niż „nie umiem tego formatu" — powtórka nic by
    nie dała, a podwoiłaby żądania przy każdej próbie wyceny."""
    proby = 0

    def obsluz(request: httpx.Request) -> httpx.Response:
        nonlocal proby
        proby += 1
        return httpx.Response(401, json={"error": {"message": "zły klucz"}})

    klient = httpx.AsyncClient(transport=httpx.MockTransport(obsluz))
    usluga = WycenaAI(
        KlientZgodnyZOpenAI(
            "zly",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            klient=klient,
        ),
        katalog=tmp_path,
    )
    with pytest.raises(BladWyceny):
        await usluga.wycen(_dane(), ())
    assert proby == 1
    await klient.aclose()


async def test_powod_odmowy_dociera_do_uzytkownika(tmp_path: pathlib.Path) -> None:
    """Zgłoszenie z użytkowania: w logu było samo „400 Bad Request".

    Tym kodem dostawca odpowiada i na nieznany model, i na nieobsługiwany
    format odpowiedzi — a to dwie różne naprawy. Bez treści odpowiedzi nie
    da się zgadnąć, którą wykonać.
    """

    def obsluz(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Model Not Exist"}})

    klient = httpx.AsyncClient(transport=httpx.MockTransport(obsluz))
    usluga = WycenaAI(
        KlientZgodnyZOpenAI(
            "k",
            model="zly-model",
            base_url="https://api.deepseek.com/v1",
            klient=klient,
        ),
        katalog=tmp_path,
    )
    with pytest.raises(BladWyceny, match="Model Not Exist"):
        await usluga.wycen(_dane(), ())
    await klient.aclose()


async def test_odmowa_klucza_nie_odbija_tresci_od_dostawcy(
    tmp_path: pathlib.Path,
) -> None:
    """DeepSeek odsyła przy 401 fragment klucza (`Your api key: ****lowy`).

    Klucz nie ma prawa trafić na ekran ani do logu (SPEC.md §10.2), więc
    przy 401/403 pokazujemy własne zdanie zamiast cytatu od dostawcy.
    """

    def obsluz(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Authentication Fails, Your api key: ****jne"}},
        )

    klient = httpx.AsyncClient(transport=httpx.MockTransport(obsluz))
    usluga = WycenaAI(
        KlientZgodnyZOpenAI(
            "sk-tajne",
            model="m",
            base_url="https://api.deepseek.com/v1",
            klient=klient,
        ),
        katalog=tmp_path,
    )
    with pytest.raises(BladWyceny) as blad:
        await usluga.wycen(_dane(), ())
    assert "odrzucił klucz" in str(blad.value)
    assert "****jne" not in str(blad.value)
    await klient.aclose()


def test_komunikat_bledu_radzi_sobie_z_roznymi_ksztaltami() -> None:
    """Każdy dostawca pakuje powód gdzie indziej; nie-JSON też się zdarza."""
    from app.infrastructure.ai_klienci import _komunikat_bledu

    assert (
        _komunikat_bledu(httpx.Response(400, json={"error": {"message": "a"}})) == "a"
    )
    assert _komunikat_bledu(httpx.Response(400, json={"detail": "b"})) == "b"
    assert _komunikat_bledu(httpx.Response(502, text="<html>proxy</html>")).startswith(
        "<html>"
    )
