"""Historia ofert z poleasingowe.pl (SPEC.md §11.8) — na fixtures, bez sieci.

W przeciwieństwie do tabeli EFL (RECON.md §3.5a) `lastOffers` **jest**
chronologią licytacji i te testy to sprawdzają wprost: kwoty mają rosnąć
razem z czasem. Gdyby przestały, założenie stojące za tą sekcją karty
przestałoby obowiązywać i chcemy się o tym dowiedzieć od testu.
"""

from __future__ import annotations

import datetime as dt
import pathlib

from app.application.ports import SurowaOferta, SurowaOfertaUczestnika
from app.infrastructure.sources.poleasingowe import mapper, parser

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "poleasingowe"
TERAZ = dt.datetime(2026, 9, 7, 12, 0, tzinfo=dt.UTC)


def wczytaj(nazwa: str) -> str:
    return (FIXTURES / nazwa).read_text(encoding="utf-8", errors="replace")


def surowa(nazwa: str, external_id: str = "9mjrl4k9") -> SurowaOferta:
    html = wczytaj(nazwa)
    return SurowaOferta(
        external_id=external_id,
        url="https://poleasingowe.pl/x",
        pola={},
        oferty=tuple(
            SurowaOfertaUczestnika(
                kod=w["licytant"],
                kwota=w["kwota"],
                zlozona=w["data"],
                identyfikator=w["id"],
            )
            for w in parser.sparsuj_oferty(html)
        ),
    )


def test_serwis_podaje_dziesiec_ostatnich_ofert() -> None:
    """Liczba nieprzypadkowa: aukcja ma 59 ofert, w `lastOffers` jest 10.

    Dlatego te wiersze **zapisujemy u siebie** — serwis pokazuje okno, a nie
    historię, i czyści je kilka minut po końcu (RECON.md §3.6).
    """
    assert len(parser.sparsuj_oferty(wczytaj("szczegoly-9mjrl4k9-59ofert.html"))) == 10


def test_kwoty_rosna_razem_z_czasem() -> None:
    """To jest ta własność, której NIE ma tabela EFL (RECON.md §3.5a).

    Dzięki niej wolno pokazać tę listę na karcie jako przebieg licytacji.
    """
    oferty = mapper.na_oferty(
        surowa("szczegoly-9mjrl4k9-59ofert.html"), auction_id=1, teraz=TERAZ
    )
    po_czasie = sorted(oferty, key=lambda o: o.placed_at)
    kwoty = [o.amount.amount for o in po_czasie]
    assert kwoty == sorted(kwoty), "późniejsza oferta ma być wyższa"
    assert len(set(kwoty)) == len(kwoty), "żadne dwie oferty nie są równe"


def test_czas_oferty_idzie_z_polskiej_nazwy_miesiaca_na_utc() -> None:
    """„poniedziałek 7 wrzesień 2026 12:00:41" — mianownik, nie dopełniacz.

    `%B` z `locale` odpada: locale jest stanem globalnym procesu, a w obrazie
    add-onu nie ma gwarancji, że polskie w ogóle istnieje.
    """
    oferty = mapper.na_oferty(
        surowa("szczegoly-9mjrl4k9-59ofert.html"), auction_id=1, teraz=TERAZ
    )
    najnowsza = max(oferty, key=lambda o: o.placed_at)
    # 12:00:41 czasu polskiego we wrześniu to 10:00:41 UTC.
    assert najnowsza.placed_at == dt.datetime(2026, 9, 7, 10, 0, 41, tzinfo=dt.UTC)
    assert str(najnowsza.amount.amount) == "332500.00"


def test_kazda_oferta_niesie_identyfikator_z_serwisu() -> None:
    """Klucz lepszy niż (licytant, czas): serwis redaguje nazwy do „u...k".

    Bez własnego identyfikatora dwie oferty złożone w tej samej sekundzie
    zlałyby się w jedną — a przy postąpieniach co kilka sekund to nie jest
    przypadek teoretyczny.
    """
    oferty = mapper.na_oferty(
        surowa("szczegoly-9mjrl4k9-59ofert.html"), auction_id=1, teraz=TERAZ
    )
    identyfikatory = [o.external_offer_id for o in oferty]
    assert all(identyfikatory), "każda oferta ma identyfikator"
    assert len(set(identyfikatory)) == len(identyfikatory)


def test_aukcja_bez_ofert_daje_pusta_liste_a_nie_blad() -> None:
    assert parser.sparsuj_oferty("<html><body>nic tu nie ma</body></html>") == []


def test_uszkodzona_lista_nie_wywraca_odpytu() -> None:
    """Oferty są dodatkiem do ceny i terminu — te sterują harmonogramem."""
    assert parser.sparsuj_oferty("var x = { lastOffers: [to nie JSON], };") == []
