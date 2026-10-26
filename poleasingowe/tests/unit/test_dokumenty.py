"""Ekspertyzy: parsery sześciu źródeł, pobieranie PDF i archiwum (0.31.0).

Markup w testach odtwarza strukturę stron zmierzoną na żywo 2026-10-26
(bez danych z konkretnych aukcji).
"""

from __future__ import annotations

import datetime as dt
import pathlib

import httpx
import pytest

from app.application.ports import Dokument
from app.domain.errors import ParseFailed
from app.infrastructure.dokumenty import Dokumenty, nazwa_pliku
from app.infrastructure.sources import pliki
from app.infrastructure.sources.autoprzetarg import parser as autoprzetarg
from app.infrastructure.sources.dawro import parser as dawro
from app.infrastructure.sources.efl import parser as efl
from app.infrastructure.sources.leasygroup import parser as leasygroup
from app.infrastructure.sources.mleasing import parser as mleasing
from app.infrastructure.sources.poleasingowe import parser as poleasingowe

TERAZ = dt.datetime(2026, 10, 26, 12, 0, tzinfo=dt.UTC)
PDF = b"%PDF-1.7\n" + b"x" * 64


# --------------------------------------------------------------------------
# parsery
# --------------------------------------------------------------------------


def test_dawro_wycena_tylko_gdy_strona_ma_przycisk() -> None:
    strona = (
        '<a href="#" onclick="Dialog.pobierzWycene(17210);"><img src="pdf.png"></a>'
    )
    (dokument,) = dawro.dokumenty(strona, "17210")
    assert (
        dokument.url == "https://www.dawro.pl/webservice/PrzedmiotWycenaPlik/id=17210"
    )
    assert dawro.dokumenty("<html></html>", "17210") == []
    # Przycisk innej aukcji (np. z rekomendacji) nie jest dokumentem tej.
    assert dawro.dokumenty(strona, "99") == []


def test_mleasing_zalaczniki_bez_obcych_hostow() -> None:
    bajty = (
        b'[{"url":"https://pliki-portalaukcyjny.mleasing.pl/files/a/Ekspertyza_1.pdf",'
        b'"name":"Ekspertyza_1.pdf"},'
        b'{"url":"https://zly.example/x.pdf","name":"x.pdf"}]'
    )
    assert [d.nazwa for d in mleasing.zalaczniki(bajty)] == ["Ekspertyza_1.pdf"]
    with pytest.raises(ParseFailed):
        mleasing.zalaczniki(b'{"url": 1}')


def test_leasygroup_bierze_etykiete_z_sasiedniego_kontenera() -> None:
    strona = """
    <div class="row"><div class="icon"></div>
      <div class="name_container"><p class="f_14 name">OT</p></div>
      <div class="download_container">
        <a href="/pobierz-zalacznik/28163/honda-nsx/zalacznik-67298.pdf"
           class="download_link">x</a>
      </div></div>"""
    (dokument,) = leasygroup.dokumenty(strona)
    assert dokument.url == (
        "https://aukcje.leasygroup.pl/pobierz-zalacznik/28163/honda-nsx/zalacznik-67298.pdf"
    )
    assert dokument.opis_zrodla == "OT"


def test_poleasingowe_czyta_pliki_do_pobrania() -> None:
    strona = """
    <div class="download-files-box">
      <p class="download-files-text">OPINIA RZECZOZNAWCY</p>
      <a href="https://poleasingowe.pl/files/doc/9oo4v489"
         data-ecr-datalayer-download='{"file_name":"wz134kn.pdf","file_type":"opinia_rzeczoznawcy"}'>POBIERZ</a>
    </div>"""
    (dokument,) = poleasingowe.dokumenty(strona)
    assert dokument.nazwa == "OPINIA RZECZOZNAWCY — wz134kn.pdf"
    assert dokument.url == "https://poleasingowe.pl/files/doc/9oo4v489"


def test_autoprzetarg_pomija_instrukcje_serwisu() -> None:
    strona = """
    <a href="/uploads/attachements/1kX/KARTA.INFORMACYJNA.GDA7RN8.pdf"
       class="section-auction-column-left-info-download">KARTA.INFORMACYJNA.GDA7RN8.pdf</a>
    <a href="/aukcje/instrukcja?subjectPrefix=poczta"
       class="section-auction-column-left-info-download">Instrukcja</a>"""
    assert [d.nazwa for d in autoprzetarg.dokumenty(strona)] == [
        "KARTA.INFORMACYJNA.GDA7RN8.pdf"
    ]


def test_efl_pusta_zakladka_dokumentow() -> None:
    assert efl.dokumenty('<div class="hidden" data-tabs="documents"> </div>') == []
    (d,) = efl.dokumenty(
        '<div data-tabs="documents"><a href="/Files/raport.pdf">Raport</a></div>'
    )
    assert d.url == "https://aukcje.efl.com.pl/Files/raport.pdf"


# --------------------------------------------------------------------------
# pobieranie PDF
# --------------------------------------------------------------------------


def _klient(odpowiedz: httpx.Response) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda _: odpowiedz))


async def test_pobierz_pdf_przyjmuje_tylko_pdf_z_hostow_zrodla() -> None:
    async with _klient(httpx.Response(200, content=PDF)) as k:
        assert (
            await pliki.pobierz_pdf(
                k, "https://a.test/x.pdf", hosty={"a.test"}, zrodlo="t"
            )
            == PDF
        )
        with pytest.raises(ParseFailed, match="spoza hostów"):
            await pliki.pobierz_pdf(
                k, "https://b.test/x.pdf", hosty={"a.test"}, zrodlo="t"
            )
        with pytest.raises(ParseFailed, match="spoza hostów"):
            await pliki.pobierz_pdf(
                k, "http://a.test/x.pdf", hosty={"a.test"}, zrodlo="t"
            )
    async with _klient(httpx.Response(200, content=b"<html>WAF</html>")) as k:
        with pytest.raises(ParseFailed, match="nie jest PDF"):
            await pliki.pobierz_pdf(k, "https://a.test/x", hosty={"a.test"}, zrodlo="t")
    async with _klient(httpx.Response(200, content=PDF * 10)) as k:
        with pytest.raises(ParseFailed, match="większy"):
            await pliki.pobierz_pdf(
                k, "https://a.test/x", hosty={"a.test"}, zrodlo="t", limit=100
            )


# --------------------------------------------------------------------------
# usługa: lista, proxy, archiwum
# --------------------------------------------------------------------------


class _Zrodlo:
    key = "test"

    def __init__(self) -> None:
        self.pobrania = 0
        self.listy = 0

    async def dokumenty(
        self, external_id: str, url: str | None = None
    ) -> list[Dokument]:
        self.listy += 1
        return [Dokument("Ekspertyza.pdf", "https://a.test/e.pdf", "Opinia")]

    async def pobierz_dokument(self, dokument: Dokument) -> bytes:
        self.pobrania += 1
        return PDF


async def test_archiwum_przezywa_zrodlo_i_jest_idempotentne(
    tmp_path: pathlib.Path,
) -> None:
    zrodlo = _Zrodlo()
    usluga = Dokumenty({"test": zrodlo}, katalog=tmp_path, min_wolne_bajty=0)

    assert [d.nazwa for d in await usluga.lista("test", "1")] == ["Ekspertyza.pdf"]
    pierwszy = await usluga.archiwizuj(1, "test", "1", None, 0, teraz=TERAZ)
    drugi = await usluga_archiwizuj_ponownie(usluga)
    assert pierwszy is not None and drugi == pierwszy
    assert zrodlo.pobrania == 1  # drugi raz z archiwum, bez źródła
    assert zrodlo.listy == 1  # lista z pamięci

    nowa = Dokumenty({}, katalog=tmp_path, min_wolne_bajty=0)  # źródło wyłączone
    (opis,) = nowa.archiwalne(1)
    assert nowa.plik_archiwalny(1, opis.id) == (opis, PDF)
    assert nowa.plik_archiwalny(1, "../../etc/passwd") is None
    assert nowa.plik_archiwalny(2, opis.id) is None


async def usluga_archiwizuj_ponownie(usluga: Dokumenty) -> object:
    return await usluga.archiwizuj(1, "test", "1", None, 0, teraz=TERAZ)


async def test_indeks_spoza_listy_nie_pobiera_niczego(tmp_path: pathlib.Path) -> None:
    zrodlo = _Zrodlo()
    usluga = Dokumenty({"test": zrodlo}, katalog=tmp_path)
    assert await usluga.pobierz("test", "1", None, 5) is None
    assert await usluga.pobierz("inne", "1", None, 0) is None
    assert zrodlo.pobrania == 0


async def test_pliki_do_analizy_najpierw_z_archiwum(tmp_path: pathlib.Path) -> None:
    zrodlo = _Zrodlo()
    usluga = Dokumenty({"test": zrodlo}, katalog=tmp_path, min_wolne_bajty=0)
    await usluga.archiwizuj(1, "test", "1", None, 0, teraz=TERAZ)
    pobrania = zrodlo.pobrania
    (plik,) = await usluga.pliki_do_analizy(1, "test", "1", None)
    assert plik.dane == PDF and zrodlo.pobrania == pobrania


def test_nazwa_pliku_bez_sciezek() -> None:
    assert nazwa_pliku("../../a/b.pdf") == "a_b.pdf"
    assert nazwa_pliku("Opinia rzeczoznawcy — x") == "Opinia rzeczoznawcy _ x.pdf"
    assert nazwa_pliku("") == "dokument.pdf"
