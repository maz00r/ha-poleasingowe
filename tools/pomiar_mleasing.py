#!/usr/bin/env python3
"""Jednorazowy pomiar domknięcia publicznej aukcji mLeasing.

Skrypt tylko odczytuje publiczne endpointy. Zapisuje czas serwera, termin,
stan, ceny i publiczną historię ofert od T-180 s do T+600 s. Jeśli aukcja
zostanie przedłużona, przesuwa całą siatkę na nowy termin.

Przykład:
    .venv/bin/python tools/pomiar_mleasing.py --offer-id 182076
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
from typing import Any

import httpx

BAZA = "https://portalaukcyjny.mleasing.pl"
PRZED_KONCEM = tuple(range(-180, 1, 10))
PO_KONCU = (2, 5, 10, 20, 40, 60, 120, 300, 600)
SIATKA = PRZED_KONCEM + PO_KONCU
STANY_KONCOWE = {"Expired", "Withdrawn", "Sold"}
POLA_OFERTY = (
    "id",
    "number",
    "name",
    "startingAmount",
    "currentAmount",
    "incrementAmount",
    "isGrossAmount",
    "from",
    "to",
    "isBuyNow",
    "amountBuyNow",
    "isWithdrawn",
    "auctionType",
    "offerState",
)
WRAZLIWE_FRAGMENTY = (
    "login",
    "user",
    "name",
    "email",
    "phone",
    "pesel",
    "nip",
    "vin",
    "registration",
)


class BladPomiaru(RuntimeError):
    """Publiczne API nie zwróciło danych potrzebnych do pomiaru."""


def _czas(wartosc: object) -> dt.datetime:
    if not isinstance(wartosc, str):
        raise BladPomiaru(f"brak czasu w odpowiedzi: {wartosc!r}")
    try:
        wynik = dt.datetime.fromisoformat(wartosc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BladPomiaru(f"nieprawidłowy czas: {wartosc!r}") from exc
    if wynik.tzinfo is None:
        raise BladPomiaru(f"czas bez strefy: {wartosc!r}")
    return wynik


def _zredaguj(wartosc: Any, klucz: str = "") -> Any:
    """Usuwa identyfikatory z nieznanego jeszcze formatu wierszy ofert."""
    if any(fragment in klucz.lower() for fragment in WRAZLIWE_FRAGMENTY):
        return "***"
    if isinstance(wartosc, dict):
        return {str(k): _zredaguj(v, str(k)) for k, v in wartosc.items()}
    if isinstance(wartosc, list):
        return [_zredaguj(v) for v in wartosc]
    if isinstance(wartosc, str | int | float | bool) or wartosc is None:
        return wartosc
    return repr(wartosc)


def _get(klient: httpx.Client, sciezka: str, **parametry: str) -> Any:
    try:
        odpowiedz = klient.get(sciezka, params=parametry or None)
        odpowiedz.raise_for_status()
        return odpowiedz.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise BladPomiaru(f"mLeasing: {sciezka}: {exc}") from exc


def probka(klient: httpx.Client, offer_id: int, tag: str) -> dict[str, Any]:
    czas_serwera = _get(klient, "/api/administration/get-time")
    szczegoly = _get(klient, "/api/offer-read/get", id=str(offer_id))
    oferty = _get(
        klient,
        "/api/offer-read/get-bids-with-hidden-logins",
        offerId=str(offer_id),
    )
    if not isinstance(szczegoly, dict) or not isinstance(szczegoly.get("offer"), dict):
        raise BladPomiaru("mLeasing: szczegóły nie mają obiektu offer")
    if not isinstance(oferty, list):
        raise BladPomiaru("mLeasing: historia ofert nie jest listą")
    oferta = szczegoly["offer"]
    if oferta.get("id") != offer_id:
        raise BladPomiaru("mLeasing: odpowiedź dotyczy innej aukcji")

    return {
        "tag": tag,
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "server_time": _czas(czas_serwera).isoformat(),
        "offer": {pole: oferta.get(pole) for pole in POLA_OFERTY},
        "bids_count": len(oferty),
        "bid_keys": sorted(
            {
                str(klucz)
                for wiersz in oferty
                if isinstance(wiersz, dict)
                for klucz in wiersz
            }
        ),
        "bids": _zredaguj(oferty),
    }


def _termin(rekord: dict[str, Any]) -> dt.datetime:
    oferta = rekord.get("offer")
    if not isinstance(oferta, dict):
        raise BladPomiaru("brak danych aukcji")
    return _czas(oferta.get("to"))


def _zapisz(sciezka: pathlib.Path, rekord: dict[str, Any]) -> None:
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    with sciezka.open("a", encoding="utf-8") as plik:
        plik.write(json.dumps(rekord, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(rekord, ensure_ascii=False), flush=True)


def _czekaj_do(cel: dt.datetime) -> None:
    pozostalo = (cel - dt.datetime.now(cel.tzinfo)).total_seconds()
    while pozostalo > 0:
        time.sleep(min(pozostalo, 30))
        pozostalo = (cel - dt.datetime.now(cel.tzinfo)).total_seconds()


def wykonaj(
    offer_id: int,
    *,
    wyjscie: pathlib.Path,
    dry_run: bool,
    oczekiwany_koniec: dt.datetime | None,
    maks_czas: int,
) -> int:
    start_monotoniczny = time.monotonic()
    with httpx.Client(
        base_url=BAZA,
        timeout=30,
        follow_redirects=True,
        headers={"Accept-Language": "pl-PL,pl;q=0.9"},
    ) as klient:
        pierwsza = probka(klient, offer_id, "dry-run" if dry_run else "start")
        _zapisz(wyjscie, pierwsza)
        koniec = _termin(pierwsza)
        stan = pierwsza["offer"].get("offerState")
        if stan in STANY_KONCOWE:
            raise BladPomiaru(f"aukcja jest już zakończona: {stan}")
        if oczekiwany_koniec is not None:
            roznica = abs((koniec - oczekiwany_koniec).total_seconds())
            if roznica > 15:
                print(
                    f"UWAGA: termin API {koniec.isoformat()} różni się od "
                    f"oczekiwanego {oczekiwany_koniec.isoformat()}",
                    file=sys.stderr,
                    flush=True,
                )
        if dry_run:
            return 0

        wykonane: set[tuple[str, int]] = set()
        while time.monotonic() - start_monotoniczny <= maks_czas:
            teraz = dt.datetime.now(koniec.tzinfo)
            kandydaci = [
                (koniec + dt.timedelta(seconds=offset), offset)
                for offset in SIATKA
                if (koniec.isoformat(), offset) not in wykonane
                and koniec + dt.timedelta(seconds=offset)
                >= teraz - dt.timedelta(seconds=2)
            ]
            if not kandydaci:
                return 0
            cel, offset = min(kandydaci, key=lambda x: x[0])
            _czekaj_do(cel)
            tag = f"t{offset:+d}s"
            try:
                rekord = probka(klient, offer_id, tag)
            except BladPomiaru as exc:
                rekord = {
                    "tag": tag,
                    "captured_at": dt.datetime.now(dt.UTC).isoformat(),
                    "error": str(exc),
                }
                _zapisz(wyjscie, rekord)
                wykonane.add((koniec.isoformat(), offset))
                continue
            _zapisz(wyjscie, rekord)
            wykonane.add((koniec.isoformat(), offset))

            nowy_koniec = _termin(rekord)
            if (nowy_koniec - koniec).total_seconds() > 15:
                _zapisz(
                    wyjscie,
                    {
                        "tag": "overtime",
                        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
                        "previous_end": koniec.isoformat(),
                        "new_end": nowy_koniec.isoformat(),
                    },
                )
                koniec = nowy_koniec

        raise BladPomiaru(f"przekroczono maksymalny czas pomiaru: {maks_czas} s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offer-id", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--expected-end")
    parser.add_argument("--max-runtime", type=int, default=7200)
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("fixtures/mleasing/recon-0b.jsonl"),
    )
    args = parser.parse_args()
    oczekiwany = _czas(args.expected_end) if args.expected_end else None
    try:
        return wykonaj(
            args.offer_id,
            wyjscie=args.output,
            dry_run=args.dry_run,
            oczekiwany_koniec=oczekiwany,
            maks_czas=args.max_runtime,
        )
    except BladPomiaru as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
