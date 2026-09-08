"""Spójność opakowania add-onu (SPEC.md §7, §7.1).

Add-on ma trzy opisy tej samej rzeczy: `config.yaml` (widzi go Home Assistant),
model pydantic (widzi go kod) i `requirements.txt` (widzi go Docker). Każdy da
się zmienić osobno, więc każdy może się rozjechać po cichu. Te testy pilnują,
żeby się nie rozjechały.
"""

from __future__ import annotations

import pathlib
import re
import tomllib
from typing import Any

import pytest
import yaml

KORZEN = pathlib.Path(__file__).resolve().parents[3]
ADDON = KORZEN / "poleasingowe"
CONFIG = ADDON / "config.yaml"
REQUIREMENTS = ADDON / "requirements.txt"
PYPROJECT = KORZEN / "pyproject.toml"


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    wczytane: dict[str, Any] = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return wczytane


def test_ingress_wlaczony_i_nic_nie_publikujemy(config: dict[str, Any]) -> None:
    """SPEC.md §7.1 — Ingress uwierzytelnia, `ports` zostaje puste."""
    assert config["ingress"] is True
    assert config["ingress_port"] == 8099
    assert config["panel_icon"] == "mdi:car-search"
    assert (
        "ports" not in config
    ), "nic nie publikujemy na zewnątrz — obecność `ports` to błąd"


def test_bez_podniesionych_uprawnien(config: dict[str, Any]) -> None:
    """SPEC.md §7.1 — bez `privileged`, `host_network`, `full_access`."""
    for zakazane in ("privileged", "host_network", "full_access", "docker_api"):
        assert zakazane not in config, f"{zakazane} nie ma prawa się tu znaleźć"
    # `apparmor` to BOOLEAN. Wpisana tu nazwa profilu wyglada sensownie
    # i jest tak opisana w dokumentacji, ale Supervisor ja odrzuca — dodatek
    # znika wtedy ze sklepu bez sladu w interfejsie, z bledem wylacznie
    # w dzienniku Supervisora. Profil wlasny idzie przez plik apparmor.txt.
    assert config["apparmor"] is True
    assert config["init"] is False


def test_homeassistant_api_domyslnie_wylaczone(config: dict[str, Any]) -> None:
    """SPEC.md §7.1 — jedyne zastosowanie to alarm operacyjny, nie encje."""
    assert config["homeassistant_api"] is False


def test_port_ingress_zgadza_sie_z_kodem(config: dict[str, Any]) -> None:
    from app.interfaces.main import PORT_INGRESS

    assert config["ingress_port"] == PORT_INGRESS


def test_opcje_w_config_pokrywaja_sie_z_modelem(config: dict[str, Any]) -> None:
    """`config.yaml` i model pydantic opisują ten sam zestaw opcji."""
    from app.infrastructure.supervisor.options import Opcje

    z_configu = set(config["options"])
    z_modelu = set(Opcje.model_fields)
    assert z_configu == z_modelu, (
        f"tylko w config.yaml: {sorted(z_configu - z_modelu)}; "
        f"tylko w modelu: {sorted(z_modelu - z_configu)}"
    )


def test_schema_opisuje_wszystkie_opcje(config: dict[str, Any]) -> None:
    """Bez wpisu w `schema` Home Assistant nie pokaże pola w interfejsie."""
    assert set(config["schema"]) == set(config["options"])


def test_domyslne_z_configu_zgadzaja_sie_z_sekcja_0(config: dict[str, Any]) -> None:
    """SPEC.md §0 — te wartości są ustalone na serwerze, nie do zgadywania."""
    opcje = config["options"]
    assert opcje["db_host"] == "db21ed7f-postgres-latest"
    assert opcje["db_port"] == 5432
    assert opcje["db_name"] == "poleasingowe"
    assert opcje["db_user"] == "poleasingowe_app"


def test_haslo_ma_typ_password_w_schemacie(config: dict[str, Any]) -> None:
    """SPEC.md §10.2 — typ `password` sprawia, że HA nie pokazuje go jawnie."""
    assert config["schema"]["db_password"] == "password"
    assert config["schema"]["openai_api_key"] == "password?"
    assert config["schema"]["credentials"][0]["password"] == "password"


def _wersje(tekst: str) -> dict[str, str]:
    wynik: dict[str, str] = {}
    for linia in tekst.splitlines():
        linia = linia.strip().strip('",')
        if not linia or linia.startswith("#"):
            continue
        dopasowanie = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([0-9.]+)$", linia)
        if dopasowanie:
            wynik[dopasowanie.group(1).lower()] = dopasowanie.group(2)
    return wynik


def test_requirements_zgadza_sie_z_pyproject() -> None:
    """Docker instaluje z `requirements.txt`, testy chodzą na `pyproject`.

    Rozjazd między nimi znaczy, że obraz dostaje inne wersje niż te, na
    których cokolwiek sprawdziliśmy.
    """
    dane = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    z_pyproject = _wersje("\n".join(dane["project"]["dependencies"]))
    z_requirements = _wersje(REQUIREMENTS.read_text(encoding="utf-8"))

    wspolne = set(z_pyproject) & set(z_requirements)
    assert wspolne, "nie znalazłem wspólnych zależności — sprawdź parsowanie"
    rozjazdy = {
        n: (z_pyproject[n], z_requirements[n])
        for n in wspolne
        if z_pyproject[n] != z_requirements[n]
    }
    assert not rozjazdy, f"różne wersje w pyproject vs requirements: {rozjazdy}"

    # Sprawdzamy OBA kierunki. Pierwsza wersja tego testu patrzyla tylko
    # w jedna strone i przepuscila realny blad: fastapi, uvicorn i jinja2
    # byly w requirements.txt, ale nie w pyproject, wiec CI budowalo
    # srodowisko bez nich i mypy nie znajdowal modulow. Lokalnie tego nie
    # bylo widac, bo mialem je doinstalowane recznie w swoim venv.
    brak_w_requirements = set(z_pyproject) - set(z_requirements)
    assert not brak_w_requirements, (
        f"zależności runtime nieobecne w requirements.txt: "
        f"{sorted(brak_w_requirements)} — obraz ich nie zainstaluje"
    )

    brak_w_pyproject = set(z_requirements) - set(z_pyproject)
    assert not brak_w_pyproject, (
        f"zależności obrazu nieobecne w pyproject: {sorted(brak_w_pyproject)} "
        "— czyste środowisko (CI) ich nie dostanie, a testy i mypy się wywalą"
    )


def test_dockerfile_wymusza_gotowe_kola() -> None:
    """SPEC.md §5 — brak koła ma wywalić budowanie, nie uruchomić kompilator.

    Kompilacja na Pentium Silver J5005 trwa nieakceptowalnie długo, więc
    `--only-binary=:all:` jest tu bramką, nie optymalizacją.
    """
    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    assert "--only-binary=:all:" in dockerfile
    assert dockerfile.count("FROM ") >= 2, "obraz ma być wieloetapowy (SPEC.md §7.1)"


def test_usluga_s6_jest_kompletna() -> None:
    """Bez wpisu w `user/contents.d` s6 nie uruchomi usługi w ogóle."""
    s6 = ADDON / "rootfs/etc/s6-overlay/s6-rc.d"
    assert (s6 / "poleasingowe/type").read_text(encoding="utf-8").strip() == "longrun"
    assert (s6 / "user/contents.d/poleasingowe").exists()
    uruchom = (s6 / "poleasingowe/run").read_text(encoding="utf-8")
    assert (
        "exec python3 -m app.interfaces.main" in uruchom
    ), "exec jest konieczny, żeby SIGTERM trafiał do Pythona, a nie do powłoki"


def test_wersje_addonu_sa_spojne(config: dict[str, Any]) -> None:
    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    assert f'io.hass.version="{config["version"]}"' in dockerfile


def test_repozytorium_wskazuje_ten_sam_adres(config: dict[str, Any]) -> None:
    repo: dict[str, Any] = yaml.safe_load(
        (KORZEN / "repository.yaml").read_text(encoding="utf-8")
    )
    assert repo["url"] == config["url"]


def test_zasoby_interfejsu_trafiaja_do_obrazu() -> None:
    """Szablony i statyki muszą wejść do obrazu razem z kodem (SPEC.md §12).

    Leżą pod `app/`, więc obejmuje je `COPY app`. Ten test pilnuje, żeby
    nikt ich stamtąd nie wyniósł bez dopisania drugiego `COPY` — brakujący
    szablon ujawnia się dopiero przy pierwszym wejściu na stronę.
    """
    interfejs = ADDON / "app/interfaces"
    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY app /opt/poleasingowe/app" in dockerfile
    for zasob in ("templates/base.html", "static/styl.css", "static/htmx.min.js"):
        assert (interfejs / zasob).exists(), f"brak {zasob}"


def test_htmx_jest_w_repo_a_nie_w_cdn() -> None:
    """SPEC.md §12 — HTMX z lokalnego pliku statycznego, żadnych CDN-ów."""
    htmx = ADDON / "app/interfaces/static/htmx.min.js"
    assert htmx.stat().st_size > 10_000, "to nie wygląda na pełny plik HTMX"
    assert "htmx" in htmx.read_text(encoding="utf-8")[:200]

    for szablon in (ADDON / "app/interfaces/templates").rglob("*.html"):
        tresc = szablon.read_text(encoding="utf-8")
        for zakazane in ("cdnjs", "unpkg", "jsdelivr", "//cdn."):
            assert zakazane not in tresc, f"{szablon.name} ładuje coś z CDN-u"


def test_szablony_nie_maja_sciezek_na_sztywno() -> None:
    """SPEC.md §7.1 — prefiks Ingressu jest dynamiczny, adres ma być względny.

    Ścieżka wpisana na sztywno działa lokalnie i prowadzi donikąd po
    instalacji, bo Home Assistant montuje add-on pod losowym prefiksem.
    """
    for szablon in (ADDON / "app/interfaces/templates").rglob("*.html"):
        tresc = szablon.read_text(encoding="utf-8")
        for wzorzec in ('href="/', 'src="/', 'action="/', 'hx-get="/', 'hx-post="/'):
            assert (
                wzorzec not in tresc
            ), f"{szablon.name} ma adres na sztywno ({wzorzec})"


OBRAZ_BAZOWY = re.compile(
    r"^ghcr\.io/home-assistant/(?P<arch>\w+)-base-python:"
    r"(?P<python>\d+\.\d+)-alpine\d+\.\d+"
)


def _build() -> dict[str, Any]:
    wczytane: dict[str, Any] = yaml.safe_load(
        (ADDON / "build.yaml").read_text(encoding="utf-8")
    )
    return wczytane


def test_tag_obrazu_bazowego_ma_pelna_postac() -> None:
    """Tag musi mieć wersję Alpine, nie samo `3.12-alpine`.

    Skrócona postać wygląda poprawnie i taki tag po prostu nie istnieje
    w GHCR. Skutek widać dopiero na maszynie docelowej, po kilku minutach
    czekania na budowanie:
    `ghcr.io/home-assistant/amd64-base-python:3.12-alpine: not found`.
    """
    obraz = _build()["build_from"]["amd64"]
    assert OBRAZ_BAZOWY.match(obraz), f"zły tag obrazu bazowego: {obraz}"


def test_dockerfile_i_build_yaml_wskazuja_ten_sam_obraz() -> None:
    """Rozjazd znaczy, że lokalny build testuje co innego niż Supervisor.

    Supervisor przekazuje `BUILD_FROM` z `build.yaml`, a domyślna wartość
    w Dockerfile obowiązuje tylko przy budowaniu ręcznym.
    """
    z_build = _build()["build_from"]["amd64"]
    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG BUILD_FROM={z_build}" in dockerfile


def test_python_w_obrazie_zgadza_sie_z_celem_mypy() -> None:
    """Obraz jest jedynym środowiskiem, w którym ten kod naprawdę działa.

    Gdy mypy sprawdza pod inną wersję niż obraz, przepuszcza składnię
    i funkcje, których nie ma w środowisku docelowym — tak przeszło kiedyś
    `Path.read_text(newline=...)`, dostępne dopiero od 3.13.
    """
    dopasowanie = OBRAZ_BAZOWY.match(_build()["build_from"]["amd64"])
    assert dopasowanie is not None
    dane = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    cel = dane["tool"]["mypy"]["python_version"]
    assert (
        dopasowanie.group("python") == cel
    ), f"obraz ma Pythona {dopasowanie.group('python')}, a mypy celuje w {cel}"


def _apparmor() -> str:
    return (ADDON / "apparmor.txt").read_text(encoding="utf-8")


def _reguly_apparmor() -> str:
    """Sam profil, bez komentarzy — te wymieniają katalogi, których zakazują."""
    return "\n".join(
        linia
        for linia in _apparmor().splitlines()
        if not linia.lstrip().startswith("#")
    )


def test_profil_apparmor_nazywa_sie_jak_slug(config: dict[str, Any]) -> None:
    """Supervisor ładuje profil pod slugiem dodatku i sprawdza nazwę w pliku."""
    assert config["apparmor"] is True, "nazwa profilu idzie z pliku, nie z config.yaml"
    assert f"profile {config['slug']} flags=" in _apparmor()


def test_init_ma_prawo_odczytu_a_nie_samo_wykonanie() -> None:
    """`/init` w s6-overlay v3 jest skryptem powłoki, nie binarką.

    Jądro uruchamia wtedy `/bin/sh /init`, a powłoka musi ten plik
    **odczytać**. Samo `ix` daje wykonanie bez odczytu i kończy się
    komunikatem, który w niczym nie wskazuje na AppArmora:
    `/bin/sh: can't open '/init': Permission denied`.
    """
    tresc = _apparmor()
    assert re.search(
        r"^\s*/init\s+rix,", tresc, re.MULTILINE
    ), "/init potrzebuje `rix`, nie `ix`"


@pytest.mark.parametrize(
    "sciezka",
    ["/bin/**", "/usr/bin/**", "/usr/local/bin/**", "/package/**", "/command/**"],
)
def test_katalogi_wykonywalne_daja_takze_odczyt(sciezka: str) -> None:
    """Ten sam problem co z `/init` — s6 i bashio to skrypty, nie binarki."""
    wzorzec = re.escape(sciezka) + r"\s+r?w?ix,"
    tresc = _apparmor()
    assert re.search(
        rf"^\s*{re.escape(sciezka)}\s+\w*rix,", tresc, re.MULTILINE
    ), f"{sciezka} musi mieć `rix`, inaczej skrypty się nie wczytają ({wzorzec})"


def test_profil_nie_ma_blankietowego_dostepu_do_plikow() -> None:
    """`file,` daje wszystko łącznie z zapisem i czyni profil dekoracją.

    Szablon w dokumentacji HA go ma; my nie, bo cała wartość tego profilu
    siedzi w ograniczeniu ZAPISU (SPEC.md §7.1).
    """
    assert not re.search(r"^\s*file,\s*$", _reguly_apparmor(), re.MULTILINE)


@pytest.mark.parametrize("katalog", ["/ssl", "/config", "/media", "/addons", "/backup"])
def test_dodatek_nie_pisze_po_cudzych_katalogach(katalog: str) -> None:
    """Add-on jest do odczytu i ma własny podkatalog w `/share` (§7.1, §12)."""
    assert katalog not in _reguly_apparmor()


def test_zapis_do_share_ograniczony_do_wlasnego_podkatalogu() -> None:
    tresc = _reguly_apparmor()
    assert "/share/poleasingowe/" in tresc
    assert not re.search(
        r"^\s*/share/\*\*", tresc, re.MULTILINE
    ), "w /share mieszkają też inne dodatki"


def test_wersja_w_kodzie_zgadza_sie_z_config_yaml(config: dict[str, Any]) -> None:
    """`app/wersja.py` niesie tę samą liczbę co `config.yaml`.

    Interfejs dokleja ją do adresów arkusza stylów i HTMX-a jako znacznik
    cache'u. Rozjazd nie wywala niczego od razu — po prostu przeglądarka
    zostaje przy starym arkuszu do nowego HTML-a, co wygląda jak zepsuty
    panel. Dlatego pilnuje tego test, a nie czujność.
    """
    from app.wersja import WERSJA

    assert config["version"] == WERSJA


@pytest.mark.parametrize("jezyk", ["pl", "en"])
def test_tlumaczenia_opisuja_wszystkie_opcje(
    config: dict[str, Any], jezyk: str
) -> None:
    """Każda opcja ma nazwę i opis w obu językach.

    Bez tego Home Assistant pokazuje w konfiguracji surowy klucz
    (`ai_provider`) i opis poprzedniej opcji — użytkownik widzi wtedy pole
    „Model OpenAI" przy DeepSeeku i słusznie nie wie, co wpisać. To się
    właśnie zdarzyło, więc pilnuje tego test, a nie pamięć.
    """
    plik = ADDON / "translations" / f"{jezyk}.yaml"
    tlumaczenia = yaml.safe_load(plik.read_text(encoding="utf-8"))["configuration"]

    for opcja in config["options"]:
        assert opcja in tlumaczenia, f"{jezyk}: brak tłumaczenia opcji {opcja}"
        assert tlumaczenia[opcja].get("name"), f"{jezyk}: {opcja} bez nazwy"
        assert tlumaczenia[opcja].get("description"), f"{jezyk}: {opcja} bez opisu"

    nadmiarowe = set(tlumaczenia) - set(config["options"])
    assert (
        not nadmiarowe
    ), f"{jezyk}: tłumaczenia opcji, których już nie ma: {nadmiarowe}"
