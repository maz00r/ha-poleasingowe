"""Rekonesans dawro.pl — parser i przepływ skanu (tools/pomiar_dawro.py).

Skrypt nie jest częścią add-onu, ale jego parser jest podstawą przyszłego
adaptera `dawro` i musi wychwytywać zmianę szablonu serwisu tak samo jak
`test_efl_parser.py`. HTML w testach jest minimalny — to spis pól, na które
liczy parser, nie wierny zrzut.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

import pomiar_dawro as pd

# --------------------------------------------------------------------------
# Minimalne szablony
# --------------------------------------------------------------------------

BOX = """
<div class="fl aukcja-box">
  <a href="/aukcja/{eid},{slug}">
    <h2 class="nazwa" title="{model}">{model}, {plate} &nbsp;</h2>
    <div class="koniec">Koniec aukcji: <span class="tekst-czerwony"><b>{koniec}</b></span></div>
    <div class="zdjecie"><img class="lazy" data-original="/cache/zdjecia/a/b/c/d/x.jpg"></div>
    <div class="przycisk zielony strzalka">LICYTUJ</div>
    <div class="parametry">
      <div class="najwyzsza-oferta">{najwyzsza}</div>
      <div class="k">Cena wywoławcza:</div><div class="w">{cena}</div>
      <div class="k">Rok produkcji:</div><div class="w">{rok} r.</div>
      <div class="k">Przebieg:</div><div class="w">{przebieg} km</div>
      <div class="k">Forma sprzedaży:</div><div class="w">{forma}</div>
      <div class="k">Sprzedający:</div><div class="w">{sprzedawca}</div>
    </div>
  </a>
</div>
"""


def strona_listy(*boksy: str, numer: int = 1) -> str:
    srodek = "".join(boksy)
    return (
        "<html><body><div id='wyniki'>"
        f"<!-- strona {numer} -->{srodek}"
        "<div class='clear'></div></div></div></div></body></html>"
    )


def box(
    eid: str,
    *,
    slug: str = "auto-x",
    model: str = "FIAT Ducato",
    plate: str = "NO310CJ",
    koniec: str = "2026-09-15 12:00",
    cena: str = "36 600,00 zł",
    najwyzsza: str = "&nbsp;",
    rok: str = "2023",
    przebieg: str = "12345",
    forma: str = "faktura VAT",
    sprzedawca: str = "STELLANTIS",
) -> str:
    return BOX.format(**locals())


DETAL = """
<html><head><meta charset="utf-8"><title>{model} • Dawro</title></head><body>
<div class="pasek-informacyjny-licytacji">
  <div class="fl koniec-aukcji-tekst">Czas trwania:</div>
  <div class="fl koniec-aukcji">od 2026-09-09 do {koniec_txt}</div>
</div>
<div class="fl-wrapper"><div class="fl lewo"><div class="parametry">
  <div><div class="fl k">Sprzedawca:</div><div class="fl v">{sprzedawca}</div></div>
  <div><div class="fl k">Opis modelu:</div><div class="fl v">{model}</div></div>
  {rejestracja}
  <div><div class="fl k">Rok produkcji:</div><div class="fl v">{rok} r.</div></div>
  {przebieg}
  {vin}
  <div><div class="fl k">Forma sprzedaży:</div><div class="fl v">{forma}</div></div>
</div></div>
<div class="opis">Dom Aukcyjny Mariola Nosko, Wodzisławska 8, 52-017 Wrocław, tel. +48 71 340 08 55</div>
<div class="parking-informacje"><div class="adres">Dawro Warszawa<br>ul. Mrówcza 79<br>04-768 Warszawa</div></div>
<div class="fr prawo"><div id="kwoty">
  <div class="fl">Cena<br />{etykieta_ceny}:</div>
  <div class="fl">{cena}</div>
  <div class="fl">Najwyższa<br />oferta:</div>
  <div class="fl" id="najwyzsza-oferta">{najwyzsza}</div>
  <div class="clear"></div>
</div>
<a class="przycisk-przystap" href="#" onclick="Dialog.logowanie();">PRZYSTĄP DO AUKCJI</a>
<div id="zdjecie-male">{galeria}</div>
</div>
<h1 class="fl nazwa-przedmiotu">
  {model}{h1_plate}			</h1>
<div id="regulamin"><h1>I. Słownik pojęć</h1><p>Postąpienie oznacza...</p></div>
<script>Zegar.odliczanie({ts}); Licytacja.pasekInformacyjny({eid});
  var html = 'Aukcja zakończona!';</script>
<div class="polecane"><h1>POLECANE AUKCJE</h1>
  <div class="fl k">Cena wywoławcza:</div><div class="fl v">99 999,00 zł</div>
</div>
</body></html>
"""

GAL_ZDJ = (
    '<span class="slide-product"><a class="jackbox" data-thumbnail="/cache/zdjecia/1/t{n}.jpg" '
    'href="/cache/zdjecia/1/f{n}.jpg"><img src="/cache/zdjecia/1/p{n}.jpg" alt="{n}"></a></span>'
)


def detal(
    *,
    eid: str = "16798",
    model: str = "PEUGEOT 308 III",
    koniec_txt: str = "2026-09-15 12:14",
    ts: int = 1789467240,
    cena: str = "58 100,00 zł",
    etykieta_ceny: str = "wywoławcza",
    najwyzsza: str = "pobieranie danych...",
    rok: str = "2024",
    forma: str = "faktura VAT",
    sprzedawca: str = "STELLANTIS",
    vin: str | None = "VR3FPHNSLRY516862",
    plate: str | None = "PY93574",
    przebieg: str | None = "58765",
    zdjec: int = 4,
) -> str:
    return DETAL.format(
        eid=eid,
        model=model,
        koniec_txt=koniec_txt,
        ts=ts,
        cena=cena,
        etykieta_ceny=etykieta_ceny,
        najwyzsza=najwyzsza,
        rok=rok,
        forma=forma,
        sprzedawca=sprzedawca,
        h1_plate=f", {plate}" if plate else "",
        rejestracja=(
            f'<div><div class="fl k">Nr rejestracyjny:</div>'
            f'<div class="fl v">{plate}</div></div>'
            if plate
            else ""
        ),
        vin=(
            f'<div><div class="fl k">VIN:</div><div class="fl v">{vin}</div></div>'
            if vin
            else ""
        ),
        przebieg=(
            f'<div><div class="fl k">Przebieg:</div>'
            f'<div class="fl v">{przebieg} km</div></div>'
            if przebieg
            else ""
        ),
        galeria="".join(GAL_ZDJ.format(n=i) for i in range(zdjec)),
    )


# --------------------------------------------------------------------------
# Parser listy
# --------------------------------------------------------------------------


def test_lista_zwraca_wszystkie_pozycje_z_roznymi_sprzedawcami() -> None:
    html = strona_listy(
        box("16761", model="Hummer H2", sprzedawca="MultiDealer"),
        box("16762", model="AUDI Q5", sprzedawca="STELLANTIS", forma="umowa k/s"),
        box("16763", model="KIA Ceed", sprzedawca="Santander"),
    )
    poz = pd.parsuj_liste(html)
    assert [p["external_id"] for p in poz] == ["16761", "16762", "16763"]
    assert {p["sprzedawca"] for p in poz} == {"MultiDealer", "STELLANTIS", "Santander"}


def test_url_i_id_ze_sciezki() -> None:
    poz = pd.parsuj_liste(strona_listy(box("16798", slug="peugeot-308-iii-active")))
    assert poz[0]["external_id"] == "16798"
    assert poz[0]["url"] == "https://www.dawro.pl/aukcja/16798,peugeot-308-iii-active"
    assert poz[0]["slug"] == "peugeot-308-iii-active"


def test_numer_rejestracyjny_zdjety_z_nazwy() -> None:
    """dawro dokleja tablicę do nazwy: „Fiat Ducato, NO310CJ"."""
    poz = pd.parsuj_liste(strona_listy(box("1", model="FIAT Ducato", plate="NO310CJ")))
    assert poz[0]["nazwa"] == "FIAT Ducato"
    assert poz[0]["nazwa_pelna"] == "FIAT Ducato, NO310CJ"


def test_brak_najwyzszej_oferty_to_none() -> None:
    poz = pd.parsuj_liste(strona_listy(box("1", najwyzsza="&nbsp;")))
    assert poz[0]["najwyzsza_oferta"] is None
    poz2 = pd.parsuj_liste(strona_listy(box("2", najwyzsza="41 000,00 zł")))
    assert poz2[0]["najwyzsza_oferta"] == "41 000,00"


def test_lista_bez_kontenera_to_blad() -> None:
    with pytest.raises(pd.BladRekonesansu):
        pd.parsuj_liste("<html><body>nic tu nie ma</body></html>")


# --------------------------------------------------------------------------
# Parser szczegółów
# --------------------------------------------------------------------------


def test_szczegoly_wyciagaja_pola_pojazdu() -> None:
    d = pd.parsuj_szczegoly(detal(), "16798")
    pola = d["pola"]
    assert isinstance(pola, dict)
    assert pola["vin"] == "VR3FPHNSLRY516862"
    assert pola["nr_rejestracyjny"] == "PY93574"
    assert pola["opis_modelu"] == "PEUGEOT 308 III"
    assert pola["rok_produkcji"] == "2024 r."
    assert pola["sprzedawca"] == "STELLANTIS"
    assert d["parking"] == "Dawro Warszawa ul. Mrówcza 79 04-768 Warszawa"
    assert d["liczba_zdjec"] == 4
    # nazwa z <h1 class="nazwa-przedmiotu">, nie z nagłówka regulaminu
    # wstawionego inline ("I. Słownik pojęć"); tablica ucięta z końca.
    assert d["nazwa"] == "PEUGEOT 308 III"


def test_nazwa_pomija_naglowek_regulaminu_i_ucina_tablice() -> None:
    d = pd.parsuj_szczegoly(detal(model="Hummer H2", plate="DW929KM"), "16761")
    assert d["nazwa"] == "Hummer H2"


def test_end_ts_z_odliczania_ma_pierwszenstwo() -> None:
    """`Zegar.odliczanie(<unix>)` daje termin co do sekundy — lepszy niż
    tekst „do 2026-09-15 12:14" z minutową rozdzielczością."""
    d = pd.parsuj_szczegoly(detal(ts=1789467240), "1")
    assert d["end_ts"] == "2026-09-15 12:14:00"


def test_polecane_aukcje_nie_zanieczyszczaja_pol() -> None:
    """Sekcja „POLECANE AUKCJE" ma ten sam układ etykiet — cięcie po niej
    jest konieczne, inaczej cena wycieka z rekomendacji."""
    d = pd.parsuj_szczegoly(detal(cena="58 100,00 zł"), "1")
    assert d["cena_wywolawcza"] == "58 100,00"
    assert d["cena_wywolawcza"] != "99 999,00"


@pytest.mark.parametrize(
    "etykieta,oczekiwana",
    [
        ("wywoławcza netto", "NETTO"),
        ("wywoławcza brutto", "BRUTTO"),
        ("wywoławcza", "UNKNOWN"),
        ("wywoławcza netto/brutto", "UNKNOWN"),
    ],
)
def test_podstawa_ceny_tylko_z_jawnej_etykiety(etykieta: str, oczekiwana: str) -> None:
    d = pd.parsuj_szczegoly(detal(etykieta_ceny=etykieta), "1")
    assert d["podstawa_ceny"] == oczekiwana
    assert pd.podstawa_ceny(None) == "UNKNOWN"


def test_brak_ceny_nie_wywraca_parsera() -> None:
    surowy = detal().replace(
        '<div class="fl">58 100,00 zł</div>', "<div class='fl'></div>"
    )
    d = pd.parsuj_szczegoly(surowy, "1")
    assert d["cena_wywolawcza"] is None
    assert d["podstawa_ceny"] == "UNKNOWN"


def test_pola_opcjonalne_gdy_brak_w_tabeli() -> None:
    d = pd.parsuj_szczegoly(detal(vin=None, przebieg=None, plate=None), "1")
    pola = d["pola"]
    assert isinstance(pola, dict)
    assert pola["vin"] is None
    assert pola["przebieg"] is None
    assert pola["nr_rejestracyjny"] is None
    # paliwo/skrzynia/nadwozie dawro w ogóle nie podaje w tej tabeli
    assert pola["paliwo"] is None


def test_galeria_zwraca_pelne_zdjecia() -> None:
    d = pd.parsuj_szczegoly(detal(zdjec=3), "1")
    zdj = d["zdjecia"]
    assert isinstance(zdj, list)
    assert zdj == [
        "https://www.dawro.pl/cache/zdjecia/1/f0.jpg",
        "https://www.dawro.pl/cache/zdjecia/1/f1.jpg",
        "https://www.dawro.pl/cache/zdjecia/1/f2.jpg",
    ]


def test_endpoint_najwyzszej_oferty_wykryty_nie_wolany() -> None:
    d = pd.parsuj_szczegoly(detal(), "16798")
    assert d["endpoint_najwyzszej_oferty"] == "/WebService/PasekInformacyjny/"


def test_szczegoly_bez_paska_licytacji_to_blad() -> None:
    with pytest.raises(pd.BladRekonesansu):
        pd.parsuj_szczegoly("<html><body>404</body></html>", "1")


# --------------------------------------------------------------------------
# Przepływ skanu
# --------------------------------------------------------------------------


class _Serwer:
    """Zamiast sieci: mapuje URL → (status, bajty)."""

    def __init__(self, trasy: dict[str, tuple[int, str]]) -> None:
        self.trasy = trasy
        self.wywolania: list[str] = []

    def __call__(self, url: str, *, limit_s: int = 30) -> pd.Odpowiedz:
        self.wywolania.append(url)
        sciezka = url.replace(pd.BAZA, "")
        status, tresc = self.trasy.get(sciezka, (404, "<html>404</html>"))
        return pd.Odpowiedz(status, {}, tresc.encode("utf-8"), 0, url)


def _sesja(monkeypatch: pytest.MonkeyPatch, serwer: _Serwer) -> pd.Sesja:
    monkeypatch.setattr(pd, "pobierz", serwer)
    s = pd.Sesja(limit_na_minute=100000)
    return s


def _zapis() -> tuple[dict[str, bytes], Callable[[str, pd.Odpowiedz], None]]:
    zapisane: dict[str, bytes] = {}

    def zapisz(nazwa: str, o: pd.Odpowiedz) -> None:
        zapisane[nazwa] = o.bajty

    return zapisane, zapisz


def test_skan_przechodzi_pelna_paginacje(monkeypatch: pytest.MonkeyPatch) -> None:
    s1 = pd.WZORZEC_LISTY.format(strona=1)
    s2 = pd.WZORZEC_LISTY.format(strona=2)
    s3 = pd.WZORZEC_LISTY.format(strona=3)
    serwer = _Serwer(
        {
            s1: (200, strona_listy(box("1"), box("2"))),
            s2: (200, strona_listy(box("3"), box("4"))),
            s3: (200, strona_listy(box("3"), box("4"))),  # brak nowych → koniec
        }
    )
    sesja = _sesja(monkeypatch, serwer)
    _, zapisz = _zapis()
    poz = pd.skan(sesja, zapisz)
    assert sorted(str(p["external_id"]) for p in poz) == ["1", "2", "3", "4"]


def test_skan_wykrywa_petle_po_powrocie_do_wczesniejszej_strony(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strona wraca do zbioru sprzed poprzedniej — cykl, nie koniec listy.

    (Koniec listy = brak NOWYCH id, obsłużony osobno; tu każda strona ma
    świeży kafelek, więc `skan` nie przerwałby sam.)
    """
    trasy = {
        pd.WZORZEC_LISTY.format(strona=1): (200, strona_listy(box("11"), box("12"))),
        pd.WZORZEC_LISTY.format(strona=2): (200, strona_listy(box("21"), box("22"))),
        # strona 3 = zbiór strony 1 → brak nowych id
        pd.WZORZEC_LISTY.format(strona=3): (200, strona_listy(box("11"), box("12"))),
    }
    sesja = _sesja(monkeypatch, _Serwer(trasy))
    _, zapisz = _zapis()
    poz = pd.skan(sesja, zapisz)
    # {11,12} nie ma nowych id po stronie 2 → koniec listy, bez błędu.
    assert sorted(str(p["external_id"]) for p in poz) == ["11", "12", "21", "22"]


def test_skan_przerywa_nieskonczona_paginacje(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Każda strona zwraca same nowe aukcje — bez limitu skan biegłby wiecznie."""

    class _Nieskonczony(_Serwer):
        def __call__(self, url: str, *, limit_s: int = 30) -> pd.Odpowiedz:
            n = int(url.split("strona,")[1].split(",")[0])
            body = strona_listy(box(str(n * 100 + 1)), box(str(n * 100 + 2)))
            return pd.Odpowiedz(200, {}, body.encode(), 0, url)

    sesja = _sesja(monkeypatch, _Nieskonczony({}))
    _, zapisz = _zapis()
    with pytest.raises(pd.BladRekonesansu, match="paginacj"):
        pd.skan(sesja, zapisz)


def test_skan_przerywa_na_wafie(monkeypatch: pytest.MonkeyPatch) -> None:
    serwer = _Serwer(
        {
            pd.WZORZEC_LISTY.format(strona=1): (
                200,
                "<html><body>Just a moment... __cf_chl</body></html>",
            )
        }
    )
    sesja = _sesja(monkeypatch, serwer)
    _, zapisz = _zapis()
    with pytest.raises(pd.BladRekonesansu, match="WAF"):
        pd.skan(sesja, zapisz)


def test_skan_przerywa_na_niepoprawnym_html(monkeypatch: pytest.MonkeyPatch) -> None:
    serwer = _Serwer({pd.WZORZEC_LISTY.format(strona=1): (200, "{}")})
    sesja = _sesja(monkeypatch, serwer)
    _, zapisz = _zapis()
    with pytest.raises(pd.BladRekonesansu):
        pd.skan(sesja, zapisz)


def test_skan_przerywa_na_przekierowaniu_na_glowna(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Redirect(_Serwer):
        def __call__(self, url: str, *, limit_s: int = 30) -> pd.Odpowiedz:
            # Treść wygląda jak lista (kontener obecny, > 4 kB), ale URL
            # końcowy to strona główna — serwis przekierował skan.
            body = strona_listy(*[box(str(i)) for i in range(20)])
            return pd.Odpowiedz(200, {}, body.encode(), 1, pd.BAZA + "/")

    sesja = _sesja(monkeypatch, _Redirect({}))
    _, zapisz = _zapis()
    with pytest.raises(pd.BladRekonesansu, match="główną"):
        pd.skan(sesja, zapisz)


def test_probka_domkniecia_notuje_zmiane_terminu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Przesunięcie `Zegar.odliczanie` to potencjalna dogrywka."""
    d1 = pd.parsuj_szczegoly(detal(ts=1789467240), "1")
    d2 = pd.parsuj_szczegoly(detal(ts=1789467900), "1")  # +660 s
    assert d1["end_ts"] != d2["end_ts"]
    k1 = pd._parsuj_koniec(str(d1["end_ts"]))
    k2 = pd._parsuj_koniec(str(d2["end_ts"]))
    assert k1 is not None and k2 is not None
    assert (k2 - k1).total_seconds() > pd.DOGRYWKA_TOLERANCJA_S


def test_raport_powstaje_nawet_bez_pomiaru_domkniecia(tmp_path: object) -> None:
    import pathlib

    cel = pathlib.Path(str(tmp_path))
    poz = pd.parsuj_liste(strona_listy(box("1", sprzedawca="STELLANTIS")))
    detale = [pd.parsuj_szczegoly(detal(), "1")]
    pd.zapisz_raport(
        cel,
        poz,
        detale,
        pd.pokrycie_pol(detale),
        {"1": {"liczba": 4, "wszystkie_ok": True}},
        None,
        "pomiar domknięcia do wykonania później",
        7,
    )
    tresc = (cel / "raport.md").read_text(encoding="utf-8")
    assert "pomiar domknięcia do wykonania później" in tresc
    assert "PasekInformacyjny" in tresc
    assert "Pokrycie pól" in tresc


# --------------------------------------------------------------------------
# Redakcja
# --------------------------------------------------------------------------


def test_redakcja_usuwa_vin_tablice_i_login_z_fixtures(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Żaden VIN, numer rejestracyjny ani login nie może zostać w plikach
    przeniesionych do repo (SPEC.md §10.2)."""
    import pathlib

    import redakcja_fixtures as rf

    tmp = pathlib.Path(str(tmp_path)) / "tmp"
    cel = pathlib.Path(str(tmp_path)) / "repo"
    tmp.mkdir()
    # Rejestr zamienników jest plikiem w repo — test go nie tyka.
    monkeypatch.setattr(rf, "REJESTR", pathlib.Path(str(tmp_path)) / "rejestr.json")

    vin = "WBA1J51050VT12345"
    plate = "ZS12345"  # 7 znaków, jak tablice dawro
    (tmp / "szczegoly-16798.html").write_text(detal(vin=vin, plate=plate), "utf-8")
    (tmp / "lista-01.html").write_text(
        strona_listy(box("16798", model="Peugeot 308", plate=plate)), "utf-8"
    )
    (tmp / "pomiar-domkniecie-16798.json").write_text(
        json.dumps({"winner": "licytant_x", "bd_name": "u...k"}), "utf-8"
    )

    pd.zredaguj_do_repo(tmp, cel)

    for plik in cel.iterdir():
        tresc = plik.read_text(encoding="utf-8", errors="surrogateescape")
        assert vin not in tresc, f"VIN został w {plik.name}"
        assert plate not in tresc, f"tablica została w {plik.name}"
