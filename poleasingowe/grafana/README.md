# Dashboardy Grafany

Trzy dashboardy jako JSON w repozytorium (SPEC.md §9, §14 pkt 11). Trzymamy
je tutaj, a nie tylko w Grafanie, bo **kształt widoku `reporting.*` jest
kontraktem publicznym**: zmiana kolumny w migracji musi iść w tym samym
commicie co poprawka dashboardu. Dashboard żyjący wyłącznie w bazie Grafany
rozjeżdża się po cichu i widać to dopiero na wykresie, który przestał
rysować.

| Plik | UID | Do czego |
|---|---|---|
| `aukcja.json` | `poleasingowe-aukcja` | przebieg licytacji jednej aukcji |
| `rynek.json` | `poleasingowe-rynek` | mediany cen końcowych modelu |
| `zrodla.json` | `poleasingowe-zrodla` | stan źródeł i historia przebiegów |

UID-y **są częścią umowy**: dodatek linkuje do nich wprost
(`/d/poleasingowe-aukcja?var-auction_id=…` z karty aukcji,
`/d/poleasingowe-rynek?var-make=…&var-model=…`). Zmiana UID-u zrywa te linki.

## Import

1. Grafana → Dashboards → **New → Import**.
2. Wklej zawartość pliku albo wskaż go z dysku.
3. Wybierz źródło danych PostgreSQL wskazujące na bazę `poleasingowe`
   (rola `grafana_ro`, instrukcja w `DOCS.md`).

## Co te dashboardy pokazują — i czego NIE pokazują

**Dwie osobne mediany na wykresie rynku.** `median_confirmed` liczy się
z cen odczytanych **po** zakończeniu aukcji, `median_last_seen` z ostatnich
widzianych **przed** końcem, czyli z dolnych oszacowań. Zlanie ich w jedną
liczbę zaniżyłoby obraz rynku, dlatego widok podaje je osobno razem
z licznościami — a dashboard pokazuje obie kolumny obok siebie (§9).

**Odstęp między punktami na wykresie ceny to nie częstotliwość odpytów.**
Snapshot powstaje wyłącznie przy zmianie ceny, liczby ofert albo terminu
(§8.4), więc płaski odcinek znaczy „nikt nie licytował", a nie „nie
sprawdzaliśmy".

**`bid_gap` mówi, ile ofert przegapiliśmy** przed danym wpisem (§11.8).
`NULL` znaczy „nie wiadomo", nie „komplet" — w szczególności EFL liczy
uczestników licytacji proxy, a nie postąpienia, więc dla tego źródła
przyrost licznika nie jest liczbą ofert.

Rola `grafana_ro` widzi **wyłącznie widoki w schemacie `reporting`** — nigdy
tabel bazowych (§9). Gdyby dashboard potrzebował danych, których w widokach
nie ma, właściwą drogą jest nowy widok w migracji, a nie otwarcie tabeli.
