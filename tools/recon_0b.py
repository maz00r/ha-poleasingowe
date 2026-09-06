#!/usr/bin/env python3
"""ETAP 0b — pomiar domkniecia aukcji (SPEC.md §4 pkt b, c, e).

Odpowiada na pytania, ktorych nie da sie ustalic ze statycznego zrzutu:
  - jak dlugo cena jest widoczna po wygasnieciu,
  - czy i o ile serwis przedluza aukcje (dogrywka w praktyce, nie z regulaminu),
  - czy panel/lista ofert przezywa zamkniecie.

Nie licytuje, nie loguje sie, nie wysyla niczego poza GET-ami.

Uzycie:
    python3 tools/recon_0b.py --url <URL> --source poleasingowe|efl [--dry-run]

Bez --dry-run skrypt czeka do konca aukcji, probkuje ja gesto w koncowce,
a po wygasnieciu odpytuje wg drabinki z §11.5 i zapisuje kazda odpowiedz.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.request
import zoneinfo

TZ = zoneinfo.ZoneInfo("Europe/Warsaw")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# gesta faza przed koncem: co ile sekund probkowac, zaczynajac T-180 s
RUNUP_FROM_S = 180
RUNUP_STEP_S = 10
# drabinka po wygasnieciu, sekundy od zaobserwowanego konca (§4 pkt b)
AFTER_END_S = [2, 5, 15, 60, 300]


def fetch(url: str) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl-PL,pl;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
        return r.status, {k.lower(): v for k, v in r.headers.items()}, body


def parse_poleasingowe(html: str) -> dict[str, object]:
    """Wyciaga obiekt `auction: {...}` renderowany serwerowo w bloku Alpine."""
    out: dict[str, object] = {}
    for key, pat in [
        ("current_price", r"current_price:\s*'([^']*)'"),
        ("offers_count", r"offers_count:\s*(\d+)"),
        ("bidders_count", r"bidders_count:\s*(\d+)"),
        ("min_offer_price", r"min_offer_price:\s*([\d.]+)"),
        ("instep_price", r"instep_price:\s*([\d.]+)"),
        ("min_price_exceed", r"min_price_exceed:\s*(true|false)"),
        ("auction_pending", r"auction_pending:\s*(true|false)"),
        ("till_the_end", r"tillTheEnd:\s*'([^']*)'"),
        ("end_date", r"endDate:\s*moment\('([^']+)'\)"),
    ]:
        m = re.search(pat, html)
        out[key] = m.group(1).strip() if m else None
    m = re.search(r"lastOffers:\s*(\[.*?\])", html, re.S)
    out["last_offers_raw"] = m.group(1)[:2000] if m else None
    return out


def parse_efl(html: str) -> dict[str, object]:
    out: dict[str, object] = {}
    m = re.search(r"Do zakończenia:.{0,400}?(\d{2}\.\d{2}\.\d{4})\s*godzina\s*(\d{2}:\d{2}:\d{2})",
                  html, re.S)
    out["end_date"] = f"{m.group(1)} {m.group(2)}" if m else None
    m = re.search(r"Ofert:\s*(?:<[^>]*>\s*)*(\d+)", html)
    out["offers_count"] = m.group(1) if m else None
    m = re.search(r"Aktualna cena[^<]*(?:<[^>]*>\s*)*([\d\s &#;]+,\d{2})\s*zł", html)
    out["current_price"] = re.sub(r"&#160;| ", " ", m.group(1)).strip() if m else None
    # panel ofert: liczba wierszy tabeli
    m = re.search(r'<div class="hidden" data-tabs="bidders">(.*?)</div>', html, re.S)
    panel = m.group(1) if m else ""
    out["offers_rows"] = len(re.findall(r"<tr>", panel)) - (1 if "<thead" in panel else 0)
    out["offers_panel_empty"] = "Brak ofert" in panel
    # Swiadomie NIE zgadujemy markera zakonczenia: nie mamy jeszcze zrzutu
    # aukcji zakonczonej (SPEC.md §4 pkt g), a naiwne szukanie "zakoncz" lapie
    # etykiete "Do zakonczenia:" na aukcji aktywnej. Zapisujemy surowe sygnaly
    # i to wlasnie ten pomiar ma pokazac, ktory z nich sie zmienia.
    out["has_countdown_label"] = "Do zakończenia" in html
    out["has_bid_form"] = "NewPriceProposal" in html
    out["has_buy_now"] = "Kup teraz" in html
    return out


PARSERS = {"poleasingowe": parse_poleasingowe, "efl": parse_efl}


def sample(url: str, source: str, outdir: pathlib.Path, tag: str) -> dict[str, object]:
    now = dt.datetime.now(TZ)
    try:
        status, headers, body = fetch(url)
    except Exception as exc:                      # noqa: BLE001 - raport, nie crash
        rec = {"tag": tag, "ts": now.isoformat(), "error": repr(exc)}
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        return rec
    (outdir / f"domkniecie-{tag}.html").write_text(body, encoding="utf-8")
    rec: dict[str, object] = {
        "tag": tag,
        "ts": now.isoformat(),
        "http": status,
        "bytes": len(body),
        "etag": headers.get("etag"),
        "last_modified": headers.get("last-modified"),
        "x_ratelimit_limit": headers.get("x-ratelimit-limit"),
        "x_ratelimit_remaining": headers.get("x-ratelimit-remaining"),
    }
    rec.update(PARSERS[source](body))
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    return rec


def parse_end(source: str, rec: dict[str, object]) -> dt.datetime | None:
    raw = rec.get("end_date")
    if not isinstance(raw, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(raw, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--source", required=True, choices=sorted(PARSERS))
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="jedna probka i wyjscie — do sprawdzenia parsera")
    args = ap.parse_args()

    outdir = pathlib.Path(args.outdir or f"fixtures/{args.source}")
    outdir.mkdir(parents=True, exist_ok=True)
    log = outdir / "recon-0b.jsonl"

    def write(rec: dict[str, object]) -> None:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    first = sample(args.url, args.source, outdir, "t0")
    write(first)
    if args.dry_run:
        return 0

    end = parse_end(args.source, first)
    if end is None:
        print("STOP: nie udalo sie odczytac daty konca — parser albo strona sie zmienily",
              file=sys.stderr)
        return 1
    print(f"# koniec aukcji wg strony: {end.isoformat()}", flush=True)

    # faza 1: gesty run-up, z ciaglym odczytem ends_at (dogrywka moze go przesunac)
    while True:
        now = dt.datetime.now(TZ)
        left = (end - now).total_seconds()
        if left <= 0:
            break
        if left > RUNUP_FROM_S:
            time.sleep(min(left - RUNUP_FROM_S, 60))
            continue
        time.sleep(min(RUNUP_STEP_S, max(left, 1)))
        rec = sample(args.url, args.source, outdir,
                     f"pre-{int(max((end - dt.datetime.now(TZ)).total_seconds(), 0))}s")
        write(rec)
        new_end = parse_end(args.source, rec)
        if new_end and new_end > end:
            print(f"# DOGRYWKA: ends_at {end.isoformat()} -> {new_end.isoformat()} "
                  f"(+{(new_end - end).total_seconds():.0f}s)", flush=True)
            rec["dogrywka_shift_s"] = (new_end - end).total_seconds()
            end = new_end

    # faza 2: drabinka po wygasnieciu — sedno punktu (b)
    zero = end
    for offset in AFTER_END_S:
        target = zero + dt.timedelta(seconds=offset)
        delay = (target - dt.datetime.now(TZ)).total_seconds()
        if delay > 0:
            time.sleep(delay)
        rec = sample(args.url, args.source, outdir, f"post+{offset}s")
        write(rec)
        new_end = parse_end(args.source, rec)
        if new_end and new_end > zero:
            print(f"# DOGRYWKA po wygasnieciu: -> {new_end.isoformat()}", flush=True)

    print(f"# gotowe, log: {log}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
