#!/bin/bash
# Pomiar domkniecia dla www.dawro.pl — punkty b, c, e, g checklisty
# z SPEC.md §4 dla piatego zrodla (RECON.md §4.5).
#
# CO MIERZYMY. Jak dlugo po `koniec` widac cene i strone aukcji, czy
# historia ofert przezywa zamkniecie, czy w koncowce cena idzie AJAX-em
# (`POST /WebService/PasekInformacyjny/`), i ktory sygnal strukturalny
# (`div.pasek-informacyjny-licytacji`, `a.przycisk-przystap`) znika po
# zakonczeniu. Punkt (a) — dogrywka — jest juz rozstrzygniety z regulaminu:
# dawro domyka aukcje TWARDO o wyznaczonej godzinie, bez przedluzania
# (RECON.md §4.5, "Regulamin — twarde zamkniecie"). Dlatego drabinka
# domkniecia dla dawro nie potrzebuje szerokiego okna dogrywki, ale okno
# widocznosci ceny po koncu trzeba zmierzyc tak samo jak dla pozostalych.
#
# DLACZEGO WRAPPER, A NIE SAM PYTHON W LAUNCHD. Trzy rzeczy wokol pomiaru:
# nie pozwolic Macowi zasnac w trakcie (`caffeinate`), zapisac log w miejscu,
# w ktorym da sie go znalezc, i sprawdzic, czy w ogole zdazylismy — pomiar
# odpalony po fakcie zapisuje stara/zamrozona strone i wyglada jak wynik.
#
# Uruchomienie reczne (gdyby launchd nie zadzialal):
#   tools/pomiar-dawro.sh
#   POMIAR_URL="https://www.dawro.pl/aukcja/16770,fiat-ducato-l2h2" tools/pomiar-dawro.sh
set -uo pipefail

KATALOG_REPO="/Users/Patryk/apka-poleasingowe"
PYTHON="${KATALOG_REPO}/.venv/bin/python"

# Domyslnie Hummer H2 (id 16761) — najwczesniej konczaca sie aukcja z serii
# 2026-09-14 (koniec 10:00 Europe/Warsaw). Gdyby ten pomiar przepadl, ta sama
# seria domyka kolejne pozycje co 2 minuty (16762 10:02, 16763 10:04, ...),
# a nastepna seria konczy sie 2026-09-15 od 12:00 — wtedy wystarczy podac
# inny adres przez POMIAR_URL zamiast edytowac skrypt.
URL="${POMIAR_URL:-https://www.dawro.pl/aukcja/16761,hummer-h2}"

LOG="${KATALOG_REPO}/fixtures/dawro/pomiar-domkniecie.log"
STEMPEL="${KATALOG_REPO}/fixtures/dawro/.pomiar-domkniecie-wykonany"

cd "${KATALOG_REPO}" || exit 1
mkdir -p "$(dirname "${LOG}")"

if [ -f "${STEMPEL}" ] && [ -z "${POMIAR_URL:-}" ]; then
  echo "# $(date '+%Y-%m-%d %H:%M') pominiete: pomiar wykonano $(cat "${STEMPEL}")" \
    >>"${LOG}"
  exit 0
fi

{
  echo "=============================================================="
  echo "# start pomiaru: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "# aukcja: ${URL}"
} >>"${LOG}"

# Czy jest jeszcze co mierzyc. `--dry-run --url` to jedno zadanie: parsuje
# strone szczegolow i wypisuje JSON. Sprawdzamy, ze `end_ts` istnieje i nie
# jest przeszloscia sprzed wiecej niz godziny — inaczej jestesmy tak spozieni,
# ze pomiar zlapie tylko martwa strone.
PROBA="$("${PYTHON}" tools/pomiar_dawro.py --dry-run --url "${URL}" 2>&1)"
echo "${PROBA}" >>"${LOG}"

if ! echo "${PROBA}" | "${PYTHON}" -c '
import sys, json, datetime as dt
try:
    d = json.loads(sys.stdin.read())
except Exception as e:
    print(f"# STOP: dry-run nie zwrocil JSON-a ({e})"); sys.exit(1)
ts = d.get("end_ts")
if not ts:
    print("# STOP: brak end_ts — to nie wyglada na strone trwajacej aukcji"); sys.exit(1)
koniec = dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
spoznienie = (dt.datetime.now() - koniec).total_seconds()
if spoznienie > 3600:
    print(f"# STOP: aukcja skonczyla sie {koniec} — spoznienie {spoznienie/3600:.1f} h"); sys.exit(1)
print(f"# OK: koniec {koniec}, do konca {-spoznienie/60:.0f} min")
' >>"${LOG}" 2>&1; then
  {
    echo "# Nastepne serie dawro: 2026-09-14 (co 2 min od 10:00) i 2026-09-15"
    echo "# (co 2 min od 12:00). Podaj inny adres przez POMIAR_URL albo przestaw"
    echo "# termin w ~/Library/LaunchAgents/pl.poleasingowe.pomiar-dawro.plist."
  } >>"${LOG}"
  exit 1
fi

# `caffeinate -i` blokuje usypianie z bezczynnosci na czas pomiaru. Nie obudzi
# Maca, ktory juz spi — od tego jest termin zadania z zapasem. pomiar_dawro.py
# sam redaguje fixtures (VIN/tablice/login) przed zapisem do fixtures/dawro/.
caffeinate -i "${PYTHON}" tools/pomiar_dawro.py --url "${URL}" >>"${LOG}" 2>&1
WYNIK=$?

{
  echo "# koniec pomiaru: $(date '+%Y-%m-%d %H:%M:%S %Z'), kod wyjscia ${WYNIK}"
  echo "# zrzuty (domkniecie-*.html), pomiar-domkniecie-*.json i raport: fixtures/dawro/"
} >>"${LOG}"

# Pomiar jednorazowy. Wyzwalacz launchd jest kalendarzowy, wiec zostawiamy
# stempel — kolejne odpalenie zobaczy go i wyjdzie od razu, nie ruszajac
# serwisu. Stempel tylko po UDANYM przebiegu; nieudany chcemy moc powtorzyc.
# Agenta usuwa sie recznie:
#   launchctl bootout gui/$(id -u)/pl.poleasingowe.pomiar-dawro
if [ "${WYNIK}" -eq 0 ]; then
  date '+%Y-%m-%d %H:%M:%S %Z' >"${STEMPEL}"
  echo "# stempel: ${STEMPEL} — kolejne odpalenia nic nie zrobia" >>"${LOG}"
fi
exit "${WYNIK}"
