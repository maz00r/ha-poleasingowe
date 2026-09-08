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

## Włączanie źródeł

**Domyślnie dodatek niczego nie odpytuje.** Lista `sources` w opcjach jest
pusta i dopóki taka zostanie, add-on trzyma schemat, wystawia interfejs
i nie wysyła ani jednego żądania do serwisów.

Żeby włączyć źródło, dopisz je do opcji:

```yaml
sources:
  - key: efl
    enabled: true
    rate_limit_per_minute: 30
    floor_seconds: 60
```

Ustawiasz tylko trzy rzeczy: czy źródło działa, ile żądań na minutę i jaki
jest **floor**, czyli najmniejszy odstęp między odpytami w końcówce aukcji.
Reszta — okno dogrywki, siatka domknięcia, co serwis liczy w `bid_count` —
pochodzi z rekonesansu i siedzi w kodzie, bo to fakty o serwisie, a nie
Twoje preferencje.

**Floor działa jako podłoga, nie jako wartość.** Dodatek liczy go z reguły:
połowa okna dogrywki serwisu, nie mniej niż 10 sekund — tak, żeby w okno
przedłużenia zmieściły się dwie próbki. Dla poleasingowe.pl (okno 30 s) daje
to 15 s. Twoje ustawienie może ten wynik **podnieść**, nigdy obniżyć.

Jak często dodatek odpytuje aktywną aukcję:

| Do końca | Odstęp |
|---|---|
| ponad 7 dni | 24 h |
| 1–7 dni | 6 h |
| 6–24 h | 1 h |
| 1–6 h | 15 min |
| 15–60 min | 3 min |
| poniżej 15 min | floor źródła |

**Nowo odkryta aukcja dostaje jeden odpyt szczegółów**, żeby uzupełnić
godzinę zakończenia i resztę pól — lista poleasingowe.pl podaje samą datę
dzienną. Potem wraca do trybu „wystarcza przemiat listy". Pojedynczo, raz za
razem, odpytywane są **wyłącznie pozycje obserwowane** — dzięki temu koszt
stały rośnie z liczbą obserwowanych, a nie z liczbą ofert w serwisie.

Gwiazdka przy wierszu listy włącza i wyłącza obserwację jednym kliknięciem.
To nie jest tylko etykieta: włączenie obserwacji uruchamia regularny odpyt
tej aukcji, wyłączenie go zatrzymuje.

Gdy serwis przestaje odpowiadać, dodatek odstawia **to jedno źródło** na
rosnącą przerwę i pracuje dalej z pozostałymi. Widać to w panelu
diagnostycznym w kolumnie ostatniego przebiegu.

### Zdjęcia i wycena AI

Lista pokazuje miniaturę pierwszego zdjęcia. Obrazy są pobierane przez add-on
i trafiają do rotowanego cache'u `/data/cache/zdjecia` o maksymalnym rozmiarze
100 MB — przeglądarka nie łączy się bezpośrednio z serwisem aukcyjnym. Po
wejściu w szczegóły widoczna jest pełna galeria udostępniona przez źródło.

Wycena AI jest opcjonalna i **nie jest przywiązana do OpenAI**. Wybierasz
dostawcę opcją `ai_provider`:

| `ai_provider` | Dla kogo | Co ustawić |
|---|---|---|
| `openai` (domyślnie) | OpenAI | `ai_api_key` |
| `anthropic` | Anthropic (Claude) | `ai_api_key` |
| `zgodny_z_openai` | wszystko, co wystawia `/chat/completions`: OpenRouter, Groq, DeepSeek, Mistral, xAI, Together, a także **model lokalny** (Ollama, LM Studio) | `ai_api_key`, `ai_base_url`, `ai_model` |

```yaml
# OpenAI — wystarczy klucz
ai_provider: openai
ai_api_key: "sk-..."

# Anthropic
ai_provider: anthropic
ai_api_key: "sk-ant-..."
ai_model: ""            # puste = domyślny model dostawcy

# Model lokalny — dane pojazdu nie opuszczają sieci domowej
ai_provider: zgodny_z_openai
ai_base_url: "http://ollama:11434/v1"
ai_model: "qwen3:14b"
ai_api_key: "cokolwiek"  # lokalne serwery zwykle nie sprawdzają klucza
```

`ai_model` puste znaczy „model domyślny dostawcy"; przy `zgodny_z_openai`
trzeba go podać, bo tam żadnego domyślnego nie ma. Brak `ai_base_url` przy
tym dostawcy **zatrzymuje dodatek z jasnym komunikatem** zamiast po cichu
wysłać dane do OpenAI.

Starsza opcja `openai_api_key` nadal działa jako `ai_api_key` — aktualizacja
dodatku nie wyłącza wyceny osobom, które miały ją już ustawioną.

Po ponownym uruchomieniu szczegóły pojazdu pokażą szacowaną wartość w PLN,
przedział, pewność i założenia. Wynik jest zapisywany w lokalnym cache'u, więc
ponowne otwarcie tej samej wyceny nie wykonuje kolejnego płatnego żądania.
Zmiana danych auta, lokalnych porównań albo modelu automatycznie unieważnia
cache. Do dostawcy nie są wysyłane VIN, identyfikator aukcji ani dane
sprzedającego. Wycena jest orientacyjna i nie zastępuje oględzin ani opinii
rzeczoznawcy.

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

Karta aukcji pokazuje wszystkie pola, zdjęcia pojazdu, link do oferty
w serwisie i linki do Grafany (jeśli ustawiłeś `grafana_base_url`). Wykresu
tu nie ma świadomie — historia cen jest w Grafanie.

**Zdjęcia nie są trzymane w bazie.** Dodatek pobiera je dopiero, gdy otworzysz
kartę aukcji, i podaje **przez siebie**, a nie odsyłaczem wprost do serwisu.
Raz pobrane leżą w cache'u na dysku (`/data/cache/zdjecia`), który ma twardy
limit 100 MB i kasuje najstarsze pliki. Pierwsze otwarcie karty jest przez to
wolniejsze o czas pobrania zdjęć; kolejne są natychmiastowe.

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
