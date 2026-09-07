# Aukcje poleasingowe — dokumentacja add-onu

Add-on zbiera oferty z polskich serwisów aukcji samochodów poleasingowych,
trzyma pełną historię cen i udostępnia interfejs operacyjny przez Ingress.

**Add-on jest wyłącznie do odczytu.** Nigdy nie licytuje, nie składa ofert
i nie zakłada kont.

Home Assistant jest tu wyłącznie nośnikiem: daje kontener, panel w bocznym
menu i uwierzytelnienie przez Ingress. Add-on **nie wystawia żadnych encji**,
nie używa MQTT i nie zapisuje się do rejestru urządzeń.

## Instalacja

1. W Home Assistant: **Ustawienia → Dodatki → Sklep z dodatkami**, menu
   z trzema kropkami w prawym górnym rogu → **Repozytoria**.
2. Dodaj: `https://github.com/maz00r/ha-poleasingowe`
3. Odśwież stronę; dodatek **Aukcje poleasingowe** pojawi się na liście.
4. Zainstaluj, potem przejdź do zakładki **Konfiguracja**.

### Zanim uruchomisz

Dodatek **nie tworzy bazy ani ról** — muszą istnieć wcześniej. Wymagane:

- baza `poleasingowe` na serwerze PostgreSQL,
- rola `poleasingowe_app` będąca właścicielem tej bazy oraz schematów
  `app` i `reporting`,
- rola `grafana_ro` z prawem `CONNECT` — migracja `002` nadaje jej dostęp
  do widoków i **przerwie się z błędem, jeśli rola nie istnieje**.

Jedyna opcja bez sensownej wartości domyślnej to **hasło** roli aplikacji.
Reszta jest wypełniona wartościami z tej instalacji.

### Pierwsze uruchomienie

Po starcie dodatek sam zastosuje migracje i utworzy tabele oraz widoki.
W logu zobaczysz `zastosowano migracje: 001_init, 002_reporting, 003_domkniecie`.

**Brak bazy nie zatrzymuje dodatku.** Interfejs wstaje mimo to i pokazuje
w panelu diagnostycznym, dlaczego połączenia nie ma, a dodatek ponawia próby
z rosnącym odstępem do pięciu minut. Restartująca się usługa dobijająca się
do współdzielonego serwera byłaby gorsza niż usługa wyłączona.

Jedyne, co zatrzymuje dodatek, to **błędna konfiguracja** — wtedy log mówi
wprost, które pole jest złe. Dodatek nie uruchomi się z wartościami
domyślnymi, których nie ustawiłeś.

## Interfejs

Dodatek otwiera się z paska bocznego Home Assistanta. Uwierzytelnia Ingress —
nie ma osobnego logowania.

### Lista

Domyślnie: aktywne aukcje, najbliżej końca u góry. Filtry idą zwykłym adresem,
więc **każdy zestaw filtrów da się zapisać w zakładkach albo wkleić komuś** —
łącznie z sortowaniem.

Paginacja to przycisk **Wczytaj kolejne**, po 50 wierszy. Nie ma numerów stron
i to jest celowe: dodatek stronicuje kursorem, dzięki czemu aukcja, która
w międzyczasie zmieniła cenę, nie wypada z listy ani nie pokazuje się dwa razy.

Gotowe widoki są w pasku u góry:

| Widok | Co pokazuje |
|---|---|
| Aktywne | wszystko, co trwa |
| Kończą się w 24 h | aukcje z terminem w najbliższej dobie, bez tych po terminie |
| Nowe od ostatniej wizyty | co doszło od poprzedniego wejścia na listę |
| Obserwowane | tylko watchlista, niezależnie od statusu |
| Archiwum | zakończone, ze znacznikiem pewności ceny |

**„Nowe od ostatniej wizyty" pamięta ciasteczko przeglądarki**, nie baza.
W innej przeglądarce albo po wyczyszczeniu danych strony licznik startuje od
nowa. Znacznik przestawia się przy wejściu na listę, ale **nie** przy
doładowaniu kolejnej strony — inaczej widok kasowałby się w trakcie
przeglądania.

W archiwum przy każdej pozycji stoi `CONFIRMED` albo `LAST_SEEN`. `CONFIRMED`
to cena odczytana ze strony po zakończeniu aukcji. `LAST_SEEN` to ostatnia
obserwacja **przed** zamknięciem, czyli **dolne oszacowanie** — realna cena
końcowa mogła być wyższa.

### Szczegóły i watchlist

Karta aukcji pokazuje wszystkie pola, link do oferty w serwisie i linki do
Grafany (jeśli ustawiłeś `grafana_base_url`). Wykresu tu nie ma świadomie —
historia cen jest w Grafanie.

Watchlist: notatka i cena docelowa. Gdy bieżąca cena zejdzie do progu lub
niżej, wiersz na liście podświetla się na zielono. Próg porównuje się tylko
w tej samej walucie — 45 000 EUR to nie 45 000 zł.

Zapisane filtry siedzą pod formularzem: wpisujesz nazwę, a dodatek zapamiętuje
**bieżący** zestaw filtrów razem z sortowaniem.

### Panel diagnostyczny

Stan procesu i bazy, pula połączeń, dryf zegara względem PostgreSQL, RSS
procesu i rozmiar bazy. Niżej tabela źródeł: stan uwierzytelnienia, limit
tempa, floor, drabinka domknięcia i to, co dane źródło liczy w `bid_count`.

**Kreska „—" znaczy „nie ma czym zmierzyć", a nie „zero".** Data ostatniego
`pg_dump` jest pusta, bo backupu jeszcze nie ma — powstaje na dalszym etapie.

Źródło ze stanem `LOCKED` dostaje przycisk **Odblokuj**. Blokada zapala się po
trzech nieudanych logowaniach i istnieje po to, żeby dodatek nie zablokował
Twojego konta w serwisie — dlatego reset jest świadomą decyzją, a nie
automatem. Po odblokowaniu stan to `EXPIRED`, nie `OK`: sesji jeszcze nie ma,
dodatek zaloguje się przy najbliższej okazji.

## Gdzie leżą dane — przeczytaj przed pierwszym backupem

**Dane trwałe nie leżą w `/data` tego add-onu.** Są w bazie PostgreSQL,
czyli w wolumenie **add-onu PostgreSQL**, a nie tego.

W `/data` tego add-onu mieszkają wyłącznie: sesje (`/data/sessions/`),
zrzuty debugowe (`/data/debug/`) i cache. Skasowanie ich nie powoduje utraty
historii cen; skasowanie wolumenu PostgreSQL — powoduje.

**Odtworzenie działającej instalacji wymaga obu.** Snapshot samego tego
add-onu nie wystarczy.

Dodatkowo add-on wykonuje własny `pg_dump -Fc` **wyłącznie swojej bazy**
raz na dobę do `/share/poleasingowe/backup/` i trzyma 7 kopii. Ma to sens,
bo snapshot dodatku PostgreSQL obejmuje wszystkie bazy naraz — odtworzenie
z niego cofnęłoby też inne aplikacje korzystające z tego serwera.

## Współdzielony PostgreSQL

Add-on łączy się **wyłącznie** do swojej bazy i nigdy do żadnej innej na tym
samym serwerze. Nie tworzy ani nie usuwa baz, nie zmienia parametrów
globalnych, nie dotyka autovacuum i nie wykonuje `VACUUM FULL`.

### Klucz blokady migracji

Migracje wykonują się pod blokadą doradczą PostgreSQL, żeby dwa procesy nie
weszły sobie w drogę. Blokady doradcze są **wspólne dla całej instancji**,
a nie dla pojedynczej bazy — dlatego klucz musi być na tyle nietypowy, żeby
nie zderzyć się z blokadami migracji innych aplikacji na tym samym serwerze
(np. migracji Ecto).

Wybrana wartość, stała i nigdy niezmieniana:

```
5211429344620168909        (0x4852b760aa21c6cd)
```

Wyprowadzona raz jako:

```python
int.from_bytes(sha256(b"poleasingowe.migrations.v1").digest()[:8], "big", signed=True)
```

Blokada jest **transakcyjna** (`pg_advisory_xact_lock`), więc zwalnia się sama
przy commicie albo rollbacku. Proces ubity w połowie migracji nie zostawia
zawieszonej blokady na współdzielonym serwerze.

Definicja w kodzie: `app/infrastructure/persistence/migrations.py`,
stała `KLUCZ_BLOKADY_MIGRACJI`.

## Grafana — drugie źródło danych

Analityka jest w Grafanie; add-on obsługuje operacje. Grafana ma już źródło
danych do innej bazy na tym samym serwerze — dokładamy **drugie**, do bazy
`poleasingowe`, na roli tylko do odczytu.

**Grafana nigdy nie odpytuje tabel bazowych — wyłącznie widoki z `reporting`.**
Gdyby dashboardy podpięły się pod `app.auction`, schemat byłby zamrożony
i każda migracja po cichu psułaby wykresy. Rola `grafana_ro` ma uprawnienia
wyłącznie do widoków i nie odczyta tabel, nawet gdyby ktoś spróbował.

### Konfiguracja

W Grafanie: **Connections → Data sources → Add new → PostgreSQL**

| Pole | Wartość |
|---|---|
| Name | `poleasingowe` |
| Host | `db21ed7f-postgres-latest:5432` |
| Database | `poleasingowe` |
| User | `grafana_ro` |
| TLS/SSL Mode | `disable` (ruch nie wychodzi poza sieć dodatków) |
| Version | 17 |

Hasło roli `grafana_ro` jest ustawione poza add-onem — add-on nie zarządza
rolami ani hasłami.

### Weryfikacja przed pierwszymi danymi

Zrób to **zanim pojawią się aukcje**. Pusty widok, który działa, jest lepszy
niż dashboard budowany na danych produkcyjnych.

1. **Save & test** — musi zwrócić „Database Connection OK".
2. W **Explore** wybierz źródło `poleasingowe` i wykonaj:

   ```sql
   SELECT * FROM reporting.v_source_health;
   ```

   Ma zwrócić zero wierszy **bez błędu**. Zero wierszy jest tu poprawnym
   wynikiem — źródeł jeszcze nie ma.

3. Powtórz dla `reporting.v_auction_current`, `reporting.v_price_history`
   i `reporting.v_market_stats`.

4. **Sprawdź, że kontrakt działa** — to zapytanie ma się **nie udać**:

   ```sql
   SELECT * FROM app.auction;
   ```

   Oczekiwany błąd: `permission denied for table auction`. Jeśli przejdzie,
   uprawnienia są za szerokie i trzeba je zawęzić, zanim powstaną dashboardy.

### Widoki

| Widok | Do czego |
|---|---|
| `v_price_history` | historia cen jednej aukcji; `bid_gap` mówi, ile ofert przegapiono (`NULL` = nie da się policzyć) |
| `v_auction_current` | bieżący stan aukcji plus flaga obserwowania |
| `v_market_stats` | mediany cen per marka/model/rocznik |
| `v_source_health` | stan źródeł, parametry dogrywki i domknięcia, ostatni przebieg, RSS, rozmiar bazy |

**Uwaga do `v_market_stats`.** Widok podaje **dwie mediany osobno**:
`median_confirmed` (cena odczytana ze strony po zakończeniu) oraz
`median_last_seen` (ostatnia obserwacja przed zamknięciem — dolne
oszacowanie). Kolumna `median_lead_seconds` mówi, jak daleko przed
faktycznym końcem urwał się pomiar.

**Dashboard musi pokazywać je rozdzielnie.** Zmieszanie ich w jedną liczbę
zaniża obraz rynku, a `n_confirmed` i `n_last_seen` mówią, na ilu
obserwacjach każda z nich stoi.

**Uwaga do `bid_gap`.** `NULL` to **nie** to samo co `0`. Zero znaczy
„komplet historii", `NULL` znaczy „nie da się policzyć" — pierwszy snapshot
aukcji albo źródło, którego licznik zlicza uczestników licytacji proxy,
a nie oferty (EFL). Panel pokazujący `SUM(bid_gap)` bez rozróżnienia
zaraportuje takie źródło jako kompletne, choć o kompletności nie wiemy nic.
Który to przypadek, mówi `bid_count_semantics` w `v_source_health`.
