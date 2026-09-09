#!/bin/bash
# Pomiar 0b dla aukcje.leasygroup.pl — ostatnia niezmierzona pozycja
# checklisty z SPEC.md §4 (punkty b, c, e).
#
# CO MIERZYMY. Jak długo po wygaśnięciu widać cenę końcową i czy serwis
# realnie przedłuża aukcję. Bez tej liczby drabinka domknięcia z §11.5
# dostaje siatkę domyślną `{2,5,10,20,40}`, a ta jest zgadnięta: dla
# autoprzetargu okazała się za długa (cena znika po 15 s), dla EFL
# niepotrzebnie napięta (strona zostaje zamrożona godzinami).
#
# DLACZEGO WRAPPER, A NIE SAM PYTHON W LAUNCHD. Trzy rzeczy trzeba zrobić
# wokół pomiaru: nie pozwolić Macowi zasnąć w trakcie (`caffeinate`),
# zapisać log w miejscu, w którym da się go potem znaleźć, i — najważniejsze
# — sprawdzić, czy w ogóle zdążyliśmy. Pomiar uruchomiony po fakcie zapisuje
# same 404 i wygląda jak wynik.
#
# Uruchomienie ręczne (gdyby launchd nie zadziałał):
#   tools/pomiar-leasygroup.sh
set -uo pipefail

KATALOG_REPO="/Users/Patryk/apka-poleasingowe"
# Domyślnie Honda NSX (id 28163), najbliżej kończąca się licytacja
# w kategorii pojazdów. Gdyby ten pomiar przepadł, kolejne aukcje
# leasygroup kończą się 2026-09-14 i 2026-09-15 — wtedy wystarczy
# podać inny adres, zamiast edytować skrypt:
#   POMIAR_URL="https://aukcje.leasygroup.pl/aukcja/…" tools/pomiar-leasygroup.sh
URL="${POMIAR_URL:-https://aukcje.leasygroup.pl/aukcja/28163/honda-nsx-3-5-hybrid-581-km-4x4-2017/}"
LOG="${KATALOG_REPO}/fixtures/leasygroup/pomiar-0b.log"

# Gęsta siatka po wygaśnięciu. Odliczanie leasygroup ma rozdzielczość MINUTY
# (RECON.md §4.3), więc moment końca znamy z dokładnością do ~60 s — dlatego
# próbek jest więcej i sięgają dalej niż domyślne pięć. Trzynaście żądań
# w dziesięć minut, jednorazowo, przy rekomendowanym floorze 60 s.
PO_KONCU="0,5,10,15,20,30,45,60,90,120,180,300,600"

STEMPEL="${KATALOG_REPO}/fixtures/leasygroup/.pomiar-0b-wykonany"

cd "${KATALOG_REPO}" || exit 1
mkdir -p "$(dirname "${LOG}")"

if [ -f "${STEMPEL}" ] && [ -z "${POMIAR_URL:-}" ]; then
  echo "# $(date '+%Y-%m-%d %H:%M') pominięte: pomiar wykonano $(cat "${STEMPEL}")" \
    >>"${LOG}"
  exit 0
fi

{
  echo "=============================================================="
  echo "# start pomiaru: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "# aukcja: ${URL}"
} >>"${LOG}"

# Czy jest jeszcze co mierzyć. `--dry-run` to jedno żądanie: jeśli aukcja
# już się skończyła albo zniknęła, kończymy z jasnym komunikatem zamiast
# zbierać 404 przez dziesięć minut.
PROBA="$("${KATALOG_REPO}/.venv/bin/python" tools/recon_0b.py \
  --url "${URL}" --source leasygroup --dry-run 2>&1)"
echo "${PROBA}" >>"${LOG}"

if ! grep -q '"is_auction": true' <<<"${PROBA}"; then
  {
    echo "# STOP: to już nie wygląda na trwającą licytację."
    echo "# Następne aukcje leasygroup kończą się 2026-09-14 i 2026-09-15"
    echo "# (wszystkie o 11:59) — trzeba przestawić termin zadania."
  } >>"${LOG}"
  exit 1
fi

# JSONL jest jedynym surowym zapisem pomiaru, a `recon_0b.py` DOPISUJE do
# niego. Wiersze z prób na sucho (także tej powyżej) wymieszałyby się
# z pomiarem — a przy odczycie trudno odróżnić `t0` próbne od właściwego.
# Odkładamy je obok zamiast kasować: to też jest obserwacja serwisu.
JSONL="${KATALOG_REPO}/fixtures/leasygroup/recon-0b.jsonl"
if [ -s "${JSONL}" ]; then
  mv "${JSONL}" "${JSONL%.jsonl}-probne-$(date '+%Y%m%d-%H%M%S').jsonl"
fi

# `caffeinate -i` blokuje usypianie z bezczynności na czas pomiaru. Nie
# obudzi Maca, który już śpi — od tego jest termin zadania z zapasem.
caffeinate -i "${KATALOG_REPO}/.venv/bin/python" tools/recon_0b.py \
  --url "${URL}" \
  --source leasygroup \
  --po-koncu "${PO_KONCU}" >>"${LOG}" 2>&1
WYNIK=$?

{
  echo "# koniec pomiaru: $(date '+%Y-%m-%d %H:%M:%S %Z'), kod wyjścia ${WYNIK}"
  echo "# zrzuty i log JSONL: fixtures/leasygroup/"
} >>"${LOG}"
# Pomiar jest JEDNORAZOWY — mierzymy jedną kończącą się aukcję, nie zbieramy
# szeregu czasowego. Wyzwalacz launchd jest kalendarzowy (10. dnia miesiąca),
# więc zostawiamy stempel: kolejne odpalenie zobaczy go i wyjdzie od razu,
# nie ruszając serwisu. Stempel powstaje tylko po UDANYM przebiegu —
# nieudany chcemy móc powtórzyć.
#
# NIE wyłączamy tu zadania przez `launchctl bootout` na samym sobie: to
# ubiłoby proces w połowie sprzątania. Usunięcie agenta jest ręczne i opisane
# w RECON.md.
if [ "${WYNIK}" -eq 0 ]; then
  date '+%Y-%m-%d %H:%M:%S %Z' >"${STEMPEL}"
  echo "# stempel: ${STEMPEL} — kolejne odpalenia nic nie zrobią" >>"${LOG}"
fi
exit "${WYNIK}"
