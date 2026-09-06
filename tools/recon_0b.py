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
import subprocess
import sys
import time
import zoneinfo

TZ = zoneinfo.ZoneInfo("Europe/Warsaw")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# gesta faza przed koncem: co ile sekund probkowac, zaczynajac T-180 s
RUNUP_FROM_S = 180
RUNUP_STEP_S = 10
# drabinka po wygasnieciu, sekundy od zaobserwowanego konca (§4 pkt b)
AFTER_END_S = [2, 5, 15, 60, 300]
# Prog uznania przesuniecia ends_at za dogrywke, nie za szum pomiaru.
DOGRYWKA_TOLERANCE_S = 15


def fetch(url: str) -> tuple[int, dict[str, str], str]:
    """Pobiera przez curl, nie przez urllib.

    Powod: aukcje.leasygroup.pl serwuje niepelny lancuch certyfikatow i
    domyslny magazyn CA Pythona go nie domyka (SSLCertVerificationError),
    podczas gdy curl tak. Weryfikacja certyfikatu zostaje WLACZONA — zmienia
    sie tylko transport, ten sam, ktorym zebrano fixtures.
    """
    proc = subprocess.run(
        ["curl", "-sS", "-m", "30", "-L", "--compressed", "-D", "-",
         "-A", UA,
         "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
         "-H", "Accept-Language: pl-PL,pl;q=0.9",
         url],
        capture_output=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl zwrocil {proc.returncode}: "
                           f"{proc.stderr.decode('utf-8', 'replace')[:200]}")
    raw = proc.stdout.decode("utf-8", "replace")
    # przy -L naglowki kazdego przeskoku poprzedzaja cialo; bierzemy ostatni blok
    parts = re.split(r"\r\n\r\n", raw)
    headers: dict[str, str] = {}
    body = raw
    for i, part in enumerate(parts):
        if part.startswith("HTTP/"):
            headers = {}
            for line in part.splitlines()[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            body = "\r\n\r\n".join(parts[i + 1:])
    m = re.match(r"HTTP/[\d.]+ (\d{3})", parts[0] if parts else "")
    for part in parts:
        if part.startswith("HTTP/"):
            m = re.match(r"HTTP/[\d.]+ (\d{3})", part)
    status = int(m.group(1)) if m else 0
    return status, headers, body


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


def parse_autoprzetarg(html: str) -> dict[str, object]:
    out: dict[str, object] = {}
    m = re.search(r'id="auctionEndDate"[^>]*value="([^"]*)"', html)
    out["end_date"] = m.group(1).strip() if m else None
    m = re.search(r"Aktualna cena aukcji:(?:\s*<[^>]*>)*\s*([\d\s.,]+)\s*zł", html)
    out["current_price"] = m.group(1).strip() if m else None
    # Liczba i historia ofert NIE sa dostepne bez zalogowania (RECON.md §4.4);
    # notujemy wiec sygnaly posrednie, ktore moga sie zmienic po zakonczeniu.
    out["login_prompt"] = "Zaloguj się, aby złożyć ofertę" in html
    out["needs_acceptance"] = "OFERTA WYMAGA AKCEPTACJI" in html
    out["has_signalr"] = "signalR" in html or "signalr" in html
    return out


def parse_leasygroup(html: str) -> dict[str, object]:
    """leasygroup rozroznia dwa rodzaje pozycji (RECON.md §4.3).

    Licytacja ma <div class="time_label"> z ODLICZANIEM WZGLEDNYM (span.to_end,
    format "H : MM : SS") i NIE podaje absolutnego czasu konca. Oferta "Kup
    teraz" ma zamiast tego "Koniec aukcji: <timestamp>" i pusty time_label_x.
    Dlatego ends_at dla licytacji wyliczamy z odliczania — co jest dokladnie
    tym przypadkiem, przed ktorym ostrzega SPEC.md §11.7 (dryf zegara).
    """
    out: dict[str, object] = {}
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))

    m = re.search(r'class="[^"]*\bto_end\b[^"]*"[^>]*>\s*([\d]+)\s*:\s*([\d]+)\s*:\s*([\d]+)',
                  html)
    if m:
        hh, mm, ss = (int(x) for x in m.groups())
        out["is_auction"] = True
        out["countdown_raw"] = f"{hh}:{mm:02d}:{ss:02d}"
        left = dt.timedelta(hours=hh, minutes=mm, seconds=ss)
        out["end_date"] = (dt.datetime.now(TZ) + left).strftime("%Y-%m-%d %H:%M:%S")
        out["end_date_source"] = "wyliczony z odliczania"
    else:
        out["is_auction"] = False
        m2 = re.search(r"Koniec aukcji:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)", flat)
        out["end_date"] = m2.group(1).strip() if m2 else None
        out["end_date_source"] = "absolutny ze strony" if m2 else None

    m = re.search(r"Numer aukcji:\s*(\d+)", flat)
    out["auction_number"] = m.group(1) if m else None
    m = re.search(r"Cena aktualna:\s*([\d\s]+)\s*PLN", flat)
    out["current_price"] = m.group(1).strip() if m else None
    m = re.search(r"Cena wywoławcza:\s*([\d\s]+)\s*PLN", flat)
    out["start_price"] = m.group(1).strip() if m else None
    if out["current_price"] is None:
        m = re.search(r"Cena:\s*([\d\s]+)\s*PLN", flat)
        out["current_price"] = m.group(1).strip() if m else None

    # "Historia licytacji" — tabela inline, bez logowania; wiersz naglowka odpada
    m = re.search(r'<table class="offers-history">(.*?)</table>', html, re.S)
    if m:
        rows = len(re.findall(r"<tr>", m.group(1)))
        out["offers_rows"] = max(rows - 1, 0)
        out["has_offers_table"] = True
    else:
        out["offers_rows"] = None
        out["has_offers_table"] = False
    m = re.search(r"Najwyższa oferta\s+(-|[\d\s]+PLN|[\d\s]+)(?=\s+Aktualna)", flat)
    out["highest_offer"] = m.group(1).strip() if m else None
    out["has_bid_ui"] = "Licytuj" in flat
    return out


PARSERS = {
    "poleasingowe": parse_poleasingowe,
    "efl": parse_efl,
    "autoprzetarg": parse_autoprzetarg,
    "leasygroup": parse_leasygroup,
}


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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M"):
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
        # Tolerancja: leasygroup nie podaje absolutnego konca, wiec ends_at
        # wyliczamy z odliczania (teraz + pozostalo). Jitter sieciowy o sekunde
        # dalby wtedy falszywa "dogrywke". Realne przedluzenie to co najmniej
        # jedno okno dogrywki, wiec 15 s progu odsiewa szum, nie sygnal.
        if new_end and (new_end - end).total_seconds() > DOGRYWKA_TOLERANCE_S:
            print(f"# DOGRYWKA: ends_at {end.isoformat()} -> {new_end.isoformat()} "
                  f"(+{(new_end - end).total_seconds():.0f}s)", flush=True)
            rec["dogrywka_shift_s"] = (new_end - end).total_seconds()
            end = new_end
        elif new_end and new_end > end:
            end = new_end  # drobna korekta, bez raportowania dogrywki

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
        if new_end and (new_end - zero).total_seconds() > DOGRYWKA_TOLERANCE_S:
            print(f"# DOGRYWKA po wygasnieciu: -> {new_end.isoformat()}", flush=True)

    print(f"# gotowe, log: {log}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
