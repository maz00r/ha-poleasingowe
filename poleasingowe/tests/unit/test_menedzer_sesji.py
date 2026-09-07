"""Cykl życia sesji (SPEC.md §10.2, §14 pkt 8).

Wymagany cykl: **sesja ważna → wygasła → odnowiona → zablokowana**. Źródło
jest atrapą, bo testowanie tego na prawdziwym serwisie znaczyłoby trzykrotne
wpisanie złego hasła na moim koncie — czyli dokładnie to, czemu ten
mechanizm ma zapobiegać.
"""

from __future__ import annotations

import pathlib

import pytest

from app.application.ports import Ciastko, OdpowiedzHttp
from app.application.use_cases.uwierzytelnianie import MenedzerSesji
from app.domain.enums import AuthState
from app.domain.errors import AuthenticationFailed, SessionExpired, SourceUnavailable
from app.domain.logowanie import StanLogowania, ZrodloZablokowane
from app.infrastructure.auth.sesje import PlikowyMagazynSesji

WAZNA = OdpowiedzHttp(kod=200, tresc="<b>Wyloguj</b>", url_koncowy="https://x/aukcja")
WYGASLA = OdpowiedzHttp(
    kod=200,
    tresc="<form>Zaloguj</form>",
    url_koncowy="https://x/logowanie",
    czy_przekierowano=True,
)


class ZrodloAtrapa:
    """Atrapa `AuthenticatedSource` licząca, co się z nią działo."""

    key = "atrapa"

    def __init__(self, odpowiedzi: list[OdpowiedzHttp], bledy: list[Exception | None]):
        self._odpowiedzi = odpowiedzi
        self._bledy = bledy
        self.logowania = 0
        self.zadania = 0
        self.przywrocone: tuple[Ciastko, ...] = ()

    async def zaloguj(self, login: str, haslo: str) -> tuple[Ciastko, ...]:
        blad = self._bledy.pop(0) if self._bledy else None
        self.logowania += 1
        if blad is not None:
            raise blad
        return (Ciastko(nazwa="sesja", wartosc=f"po-{self.logowania}"),)

    def przywroc_sesje(self, ciastka: object) -> None:
        self.przywrocone = tuple(ciastka)  # type: ignore[arg-type]

    def czy_sesja_wygasla(self, odpowiedz: OdpowiedzHttp) -> bool:
        return odpowiedz.czy_przekierowano

    async def operacja(self) -> OdpowiedzHttp:
        self.zadania += 1
        return self._odpowiedzi.pop(0)


def menedzer(
    zrodlo: ZrodloAtrapa, katalog: pathlib.Path, zapisane: list[StanLogowania]
) -> MenedzerSesji:
    async def zapisz(stan: StanLogowania) -> None:
        zapisane.append(stan)

    return MenedzerSesji(
        zrodlo,  # type: ignore[arg-type]
        login="jan",
        haslo="bardzo-tajne",
        magazyn=PlikowyMagazynSesji(katalog),
        zapisz_stan=zapisz,
    )


async def test_wazna_sesja_nie_powoduje_logowania(tmp_path: pathlib.Path) -> None:
    """SPEC.md §10.2 — ponowne logowanie leniwe, **nigdy prewencyjnie**."""
    zrodlo = ZrodloAtrapa([WAZNA], [])
    stany: list[StanLogowania] = []
    men = menedzer(zrodlo, tmp_path, stany)

    odp = await men.wykonaj(StanLogowania(AuthState.OK), zrodlo.operacja)

    assert odp is WAZNA
    assert zrodlo.logowania == 0, "sesja była ważna, nie ma po co się logować"
    assert stany == [], "stan się nie zmienił, więc nie ma czego zapisywać"


async def test_pelny_cykl_wazna_wygasla_odnowiona(tmp_path: pathlib.Path) -> None:
    """Wygaśnięcie w trakcie pracy: jedno logowanie i powtórzenie żądania."""
    zrodlo = ZrodloAtrapa([WYGASLA, WAZNA], [])
    stany: list[StanLogowania] = []
    men = menedzer(zrodlo, tmp_path, stany)

    odp = await men.wykonaj(StanLogowania(AuthState.OK), zrodlo.operacja)

    assert odp is WAZNA
    assert zrodlo.logowania == 1
    assert zrodlo.zadania == 2, "żądanie ma zostać powtórzone po zalogowaniu"
    assert [s.stan for s in stany] == [AuthState.EXPIRED, AuthState.OK]
    assert stany[-1].nieudane_proby == 0, "wygaśnięcie nie jest nieudanym logowaniem"


async def test_wygasly_stan_loguje_sie_przed_zadaniem(tmp_path: pathlib.Path) -> None:
    zrodlo = ZrodloAtrapa([WAZNA], [])
    stany: list[StanLogowania] = []
    men = menedzer(zrodlo, tmp_path, stany)

    await men.wykonaj(StanLogowania(AuthState.EXPIRED), zrodlo.operacja)

    assert zrodlo.logowania == 1
    assert zrodlo.zadania == 1, "logowanie idzie PRZED żądaniem, nie po nim"


async def test_druga_wygasla_odpowiedz_nie_wywoluje_drugiego_logowania(
    tmp_path: pathlib.Path,
) -> None:
    """Retry jest JEDEN (§10.2).

    Świeże ciasteczka i dalej wygasła sesja znaczą, że problem nie leży
    w sesji. Kolejne logowania tylko zbliżałyby konto do blokady.
    """
    zrodlo = ZrodloAtrapa([WYGASLA, WYGASLA], [])
    men = menedzer(zrodlo, tmp_path, [])

    with pytest.raises(SessionExpired):
        await men.wykonaj(StanLogowania(AuthState.OK), zrodlo.operacja)

    assert zrodlo.logowania == 1


async def test_trzy_odrzucone_logowania_blokuja_zrodlo(tmp_path: pathlib.Path) -> None:
    """Cykl kończy się `LOCKED`, a licznik jest przekazywany do zapisu."""
    stany: list[StanLogowania] = []
    stan = StanLogowania(AuthState.EXPIRED)

    for numer in range(3):
        zrodlo = ZrodloAtrapa([WAZNA], [AuthenticationFailed("złe hasło")])
        men = menedzer(zrodlo, tmp_path, stany)
        with pytest.raises(AuthenticationFailed):
            await men.wykonaj(stan, zrodlo.operacja)
        stan = stany[-1]
        assert stan.nieudane_proby == numer + 1

    assert stan.stan is AuthState.LOCKED


async def test_zablokowane_zrodlo_nie_wysyla_zadnego_zadania(
    tmp_path: pathlib.Path,
) -> None:
    """SPEC.md §10.2 — `LOCKED` wstrzymuje wszystko, także zwykły odpyt."""
    zrodlo = ZrodloAtrapa([WAZNA], [])
    men = menedzer(zrodlo, tmp_path, [])

    with pytest.raises(ZrodloZablokowane, match="odblokuj"):
        await men.wykonaj(StanLogowania(AuthState.LOCKED, 3), zrodlo.operacja)

    assert zrodlo.zadania == 0
    assert zrodlo.logowania == 0


async def test_blad_sieci_nie_podbija_licznika_blokady(tmp_path: pathlib.Path) -> None:
    """Kluczowe rozróżnienie z §10.2.

    Gdyby awaria serwisu liczyła się jak złe hasło, trzy niedostępności
    zablokowałyby moje konto — za cudzą awarię.
    """
    zrodlo = ZrodloAtrapa([WAZNA], [SourceUnavailable("timeout")])
    stany: list[StanLogowania] = []
    men = menedzer(zrodlo, tmp_path, stany)

    with pytest.raises(SourceUnavailable):
        await men.wykonaj(StanLogowania(AuthState.EXPIRED), zrodlo.operacja)

    assert stany == [], "błąd sieci nie zmienia stanu uwierzytelnienia"


async def test_udane_logowanie_zapisuje_sesje_na_dysku(
    tmp_path: pathlib.Path,
) -> None:
    zrodlo = ZrodloAtrapa([WAZNA], [])
    men = menedzer(zrodlo, tmp_path, [])
    await men.wykonaj(StanLogowania(AuthState.EXPIRED), zrodlo.operacja)

    assert PlikowyMagazynSesji(tmp_path).wczytaj("atrapa")[0].wartosc == "po-1"


async def test_odrzucone_poswiadczenia_kasuja_zapisana_sesje(
    tmp_path: pathlib.Path,
) -> None:
    """Sesja po odrzuconym haśle jest bezużyteczna i tylko myli przy starcie."""
    magazyn = PlikowyMagazynSesji(tmp_path)
    magazyn.zapisz("atrapa", [Ciastko(nazwa="sesja", wartosc="stara")])

    zrodlo = ZrodloAtrapa([WAZNA], [AuthenticationFailed("złe hasło")])
    men = menedzer(zrodlo, tmp_path, [])
    with pytest.raises(AuthenticationFailed):
        await men.wykonaj(StanLogowania(AuthState.EXPIRED), zrodlo.operacja)

    assert magazyn.wczytaj("atrapa") == ()


async def test_restart_procesu_nie_wymusza_ponownego_logowania(
    tmp_path: pathlib.Path,
) -> None:
    """SPEC.md §10.2 — sesja przeżywa restart add-onu.

    Każde zbędne logowanie to kolejna próba na imiennym koncie, przy
    twardym limicie trzech.
    """
    PlikowyMagazynSesji(tmp_path).zapisz(
        "atrapa", [Ciastko(nazwa="sesja", wartosc="z-poprzedniego-uruchomienia")]
    )
    zrodlo = ZrodloAtrapa([WAZNA], [])
    men = menedzer(zrodlo, tmp_path, [])

    men.przywroc_sesje()
    await men.wykonaj(StanLogowania(AuthState.OK), zrodlo.operacja)

    assert zrodlo.przywrocone[0].wartosc == "z-poprzedniego-uruchomienia"
    assert zrodlo.logowania == 0
