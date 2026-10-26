"""Wycena AI: format żądania, walidacja odpowiedzi i cache."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import replace
from decimal import Decimal

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
from app.infrastructure.otomoto import PojazdDoPorownania
from app.infrastructure.wycena_ai import BladWyceny, WycenaAI

TERAZ = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.UTC)


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
    )
    porownania = (PorownanieRynkowe(2022, 77_000, 2, None, 0),)

    wynik = await usluga.wycen(_dane(), porownania, teraz=TERAZ)

    assert wynik.wartosc.amount == 78_000
    assert wynik.auction_id == 7
    assert wynik.utworzono == TERAZ
    # Trwałość NIE jest już rolą tej klasy — wycena zapisuje się w bazie
    # (`test_repozytoria.py`), a karta liczy ją tylko wtedy, gdy jeszcze
    # jej nie ma (`test_interfejs_e2e.py`).
    assert len(zadania) == 1
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
    )

    wynik = await usluga.wycen(_dane(), (), teraz=TERAZ)
    assert wynik.wartosc.amount == 78_000
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
    )

    wynik = await usluga.wycen(_dane(), (), teraz=TERAZ)
    assert wynik.wartosc.amount == 78_000
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
    )
    with pytest.raises(BladWyceny):
        await usluga.wycen(_dane(), (), teraz=TERAZ)
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
    )

    wynik = await usluga.wycen(_dane(), (), teraz=TERAZ)
    assert wynik.wartosc.amount == 78_000
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
    )
    with pytest.raises(BladWyceny):
        await usluga.wycen(_dane(), (), teraz=TERAZ)
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
    )
    with pytest.raises(BladWyceny, match="Model Not Exist"):
        await usluga.wycen(_dane(), (), teraz=TERAZ)
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
    )
    with pytest.raises(BladWyceny) as blad:
        await usluga.wycen(_dane(), (), teraz=TERAZ)
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


# --------------------------------------------------------------------------
# Poziom cen na portalach ogłoszeniowych — szacunek, nie pomiar
# --------------------------------------------------------------------------


def test_prompt_pyta_o_portale_i_zabrania_zmyslania_ogloszen() -> None:
    """Model nie ma dostępu do sieci, więc pytanie musi to mówić wprost.

    Bez tego zdania model dopisuje konkretne oferty z cenami, których nikt
    nigdy nie widział — a one wyglądają jak dowód. Zakaz jest tu ważniejszy
    niż samo pytanie o poziom cen.
    """
    from app.infrastructure.wycena_ai import INSTRUKCJE, SCHEMAT, WERSJA_PROMPTU

    assert "OtoMoto" in INSTRUKCJE and "OLX" in INSTRUKCJE
    assert "NIE MASZ dostępu do internetu" in INSTRUKCJE
    assert "nie podawaj konkretnych ogłoszeń" in INSTRUKCJE
    # Pole musi dopuszczać `null`: „nie umiem oszacować" to poprawna
    # odpowiedź, a wymuszona liczba byłaby zmyślona.
    assert SCHEMAT["properties"]["cena_portale"]["type"] == ["integer", "null"]
    assert "cena_portale" in SCHEMAT["required"]
    # Zmiana polecenia musi unieważnić cache, inaczej stare wyceny udawałyby
    # nowe (i nie miałyby tego pola).
    assert WERSJA_PROMPTU >= 2


async def test_poziom_cen_z_portali_trafia_do_wyniku(
    tmp_path: pathlib.Path,
) -> None:
    klient, _ = _przechwyc(
        {
            "choices": [
                {"message": {"content": json.dumps({**WYNIK, "cena_portale": 91000})}}
            ]
        }
    )
    usluga = WycenaAI(
        KlientZgodnyZOpenAI(
            "k", model="m", base_url="https://api.deepseek.com/v1", klient=klient
        ),
    )
    wynik = await usluga.wycen(_dane(), (), teraz=TERAZ)
    assert wynik.cena_portale is not None
    assert wynik.cena_portale.amount == 91_000
    await klient.aclose()


@pytest.mark.parametrize("odpowiedz", [None, 0, "nie wiem", "", True])
async def test_brak_szacunku_nie_wywraca_wyceny(
    tmp_path: pathlib.Path, odpowiedz: object
) -> None:
    """„Nie wiem" w tym polu jest poprawną odpowiedzią.

    Część dostawców odeśle zamiast `null` pusty napis albo zero — jedno
    i drugie znaczy „nie oszacowałem", a nie „auto warte 0 zł". Reszta
    wyceny jest wtedy nadal użyteczna.
    """
    klient, _ = _przechwyc(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({**WYNIK, "cena_portale": odpowiedz})
                    }
                }
            ]
        }
    )
    usluga = WycenaAI(
        KlientZgodnyZOpenAI(
            "k", model="m", base_url="https://api.deepseek.com/v1", klient=klient
        ),
    )
    wynik = await usluga.wycen(_dane(), (), teraz=TERAZ)
    assert wynik.cena_portale is None
    assert wynik.wartosc.amount == 78_000
    await klient.aclose()


async def test_wycena_z_otomoto_i_ekspertyza() -> None:
    """Mediana Otomoto zastępuje szacunek modelu; ekspertyza idzie do modelu."""
    from app.infrastructure.ekspertyza import OcenionyDokument
    from app.infrastructure.otomoto import Ogloszenie, WynikOtomoto

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
                                        "wartosc": 70000,
                                        "minimum": 65000,
                                        "maksimum": 76000,
                                        "cena_portale": 1,
                                        "pewnosc": "wysoka",
                                        "uzasadnienie": "Otomoto i ekspertyza.",
                                        "zalozenia": [],
                                    }
                                ),
                            }
                        ],
                    }
                ]
            },
        )

    usluga = WycenaAI(
        KlientOpenAI(
            "k",
            model="m",
            base_url="https://api.openai.com/v1",
            klient=httpx.AsyncClient(transport=httpx.MockTransport(odpowiedz)),
        )
    )
    ogloszenia = tuple(
        Ogloszenie(
            f"Skoda Octavia {i}", 80_000 + i, 2022, 120_000, f"https://o.test/{i}"
        )
        for i in range(20)
    )
    wynik = await usluga.wycen(
        _dane(),
        (),
        teraz=TERAZ,
        otomoto=WynikOtomoto(
            "https://www.otomoto.pl/x", ogloszenia, 80_010, 80_005, 80_015
        ),
        ekspertyzy=(OcenionyDokument("RST1.pdf", "uszkodzony zderzak", 20, True),),
    )

    wejscie = json.dumps(zadania[0], ensure_ascii=False)
    assert "uszkodzony zderzak" in wejscie and "ogloszenia_otomoto" in wejscie
    assert "TMBTA7NE0N0123456" not in wejscie
    assert wynik.zrodlo == "OTOMOTO"
    assert wynik.cena_portale is not None and wynik.cena_portale.amount == 80_010
    assert wynik.otomoto_liczba == 20
    assert len(wynik.otomoto_ogloszenia) == 15
    assert wynik.ekspertyzy == ("RST1.pdf",)


def _usluga_z_odpowiedzia(tekst: str, zadania: list[dict[str, object]]) -> WycenaAI:
    def odpowiedz(request: httpx.Request) -> httpx.Response:
        zadania.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": tekst}],
                    }
                ]
            },
        )

    return WycenaAI(
        KlientOpenAI(
            "k",
            model="m",
            base_url="https://api.openai.com/v1",
            klient=httpx.AsyncClient(transport=httpx.MockTransport(odpowiedz)),
        )
    )


def _bmw() -> PojazdDoPorownania:
    return PojazdDoPorownania(
        "BMW",
        "750",
        2021,
        90_000,
        "Benzyna",
        "Automatyczna",
        False,
        "Li xDrive",
        "BMW 750 Li xDrive",
    )


async def test_ai_wybiera_model_otomoto_z_zamknietej_listy() -> None:
    zadania: list[dict[str, object]] = []
    usluga = _usluga_z_odpowiedzia(json.dumps({"model_id": "seria-7"}), zadania)
    modele = [("seria-5", "Seria 5"), ("seria-7", "Seria 7")]

    assert await usluga.wybierz_model_otomoto(_bmw(), modele) == "seria-7"
    schemat = json.dumps(zadania[0])
    assert '"seria-7"' in schemat and '"brak"' in schemat


@pytest.mark.parametrize(
    "tekst", [json.dumps({"model_id": "brak"}), json.dumps({"model_id": "x5"}), "{"]
)
async def test_ai_bez_pewnego_modelu_to_none(tekst: str) -> None:
    usluga = _usluga_z_odpowiedzia(tekst, [])
    modele = [("seria-7", "Seria 7")]

    assert await usluga.wybierz_model_otomoto(_bmw(), modele) is None


async def test_wczesniejsze_niesprzedane_wystawienie_trafia_do_modelu() -> None:
    from app.application.read_models import PewnoscPowiazania, PowiazaneWystawienie
    from app.domain.enums import AuctionStatus, Currency
    from app.domain.value_objects import Money

    def wystawienie(
        i: int, status: AuctionStatus, *, pozniejsze: bool = False
    ) -> PowiazaneWystawienie:
        return PowiazaneWystawienie(
            id=i,
            source_key="efl",
            external_id=str(i),
            url=f"https://x.test/{i}",
            status=status,
            pewnosc=PewnoscPowiazania.VIN,
            pozniejsze=pozniejsze,
            ends_at=dt.datetime(2026, 9, i, tzinfo=dt.UTC),
            price_current=Money(Decimal("71000"), Currency.PLN),
            mileage_km=118_000,
            price_start=Money(Decimal("71000"), Currency.PLN),
            bid_count=0,
        )

    zadania: list[dict[str, object]] = []
    usluga = _usluga_z_odpowiedzia(
        json.dumps(
            {
                "wartosc": 68000,
                "minimum": 64000,
                "maksimum": 70000,
                "cena_portale": None,
                "pewnosc": "średnia",
                "uzasadnienie": "Nie sprzedał się za 71 000.",
                "zalozenia": [],
            }
        ),
        zadania,
    )
    await usluga.wycen(
        _dane(),
        (),
        teraz=TERAZ,
        wystawienia=(
            wystawienie(1, AuctionStatus.ENDED),
            # Niedomknięta, termin minął — historyczna, więc niesprzedana.
            wystawienie(2, AuctionStatus.ACTIVE),
            # Równoległa, wciąż trwająca w innym serwisie — pomijana.
            replace(
                wystawienie(3, AuctionStatus.ACTIVE, pozniejsze=True),
                ends_at=TERAZ + dt.timedelta(days=3),
            ),
        ),
    )

    wejscie = json.dumps(zadania[0], ensure_ascii=False)
    assert "wczesniejsze_wystawienia_tego_auta" in wejscie
    assert "GÓRNĄ granicę" in wejscie
    # Historyczne liczą się bez względu na status; trwające odpada.
    assert wejscie.count("cena_wywolawcza") == 2
    assert "efl" not in wejscie


async def test_bez_wczesniejszych_wystawien_prompt_bez_dopisku() -> None:
    zadania: list[dict[str, object]] = []
    usluga = _usluga_z_odpowiedzia("{", zadania)
    with pytest.raises(BladWyceny):
        await usluga.wycen(_dane(), (), teraz=TERAZ)
    wejscie = json.dumps(zadania[0], ensure_ascii=False)
    assert "wczesniejsze_wystawienia" not in wejscie


async def test_uzasadnienie_jako_tablica_punktow_i_ponowienie_po_zlym_json() -> None:
    odpowiedzi = iter(
        [
            '{"wartosc": 1, "uzasadnienie": "- Rynek: x", - Przebieg: y}',
            json.dumps(
                {
                    "wartosc": 70000,
                    "minimum": 65000,
                    "maksimum": 76000,
                    "cena_portale": None,
                    "pewnosc": "średnia",
                    "uzasadnienie": [
                        "Rynek: mediana 80 000 zł.",
                        "Wniosek: 70 000 zł.",
                    ],
                    "zalozenia": [],
                }
            ),
        ]
    )
    zadania: list[dict[str, object]] = []

    def odpowiedz(request: httpx.Request) -> httpx.Response:
        zadania.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": next(odpowiedzi)}],
                    }
                ]
            },
        )

    usluga = WycenaAI(
        KlientOpenAI(
            "k",
            model="m",
            base_url="https://api.openai.com/v1",
            klient=httpx.AsyncClient(transport=httpx.MockTransport(odpowiedz)),
        )
    )
    wynik = await usluga.wycen(_dane(), (), teraz=TERAZ)

    assert len(zadania) == 2
    assert wynik.uzasadnienie == "- Rynek: mediana 80 000 zł.\n- Wniosek: 70 000 zł."


async def test_instrukcja_ocenia_silnik_wg_rynku_wtornego() -> None:
    zadania: list[dict[str, object]] = []
    usluga = _usluga_z_odpowiedzia("{", zadania)
    with pytest.raises(BladWyceny):
        await usluga.wycen(_dane(), (), teraz=TERAZ)
    wyslane = json.dumps(zadania[0], ensure_ascii=False)
    assert "awaryjność" in wyslane and "Silnik:" in wyslane
