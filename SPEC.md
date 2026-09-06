# Aukcje poleasingowe — add-on do Home Assistant

Add-on dla Home Assistant OS, który cyklicznie zbiera oferty z polskich
serwisów aukcji samochodów poleasingowych, trzyma pełną historię cen
i udostępnia interfejs operacyjny przez Ingress.

**Home Assistant jest wyłącznie nośnikiem.** Daje kontener, panel w bocznym
menu i uwierzytelnienie przez Ingress. Add-on **nie wystawia żadnych encji**,
nie używa MQTT i nie zapisuje się do rejestru urządzeń HA.

**Aplikacja jest wyłącznie do odczytu** — nigdy nie licytuje, nie składa
ofert i nie zakłada kont.

Podział odpowiedzialności:

| Warstwa | Kto |
|---|---|
| Zbieranie danych, sesje, harmonogram | ten add-on |
| Przechowywanie | istniejący Postgres (współdzielony z TeslaMate) |
| Operacje: przeglądanie, filtry, watchlist, diagnostyka | UI add-onu (Ingress) |
| Analityka: historia cen, mediany, statystyki rynkowe | istniejąca Grafana |

---

## 0. Stan zastany — fakty potwierdzone, nie do zgadywania

Poniższe zostało sprawdzone na docelowej instancji. **Nie weryfikuj tego
ponownie, nie zakładaj innych wartości i nie proponuj zmian w tej sekcji.**

| Co | Wartość |
|---|---|
| Serwer bazy | PostgreSQL **17.5** (Debian 17.5-1.pgdg120+1), x86_64 |
| Dodatek | `postgres-latest` (repozytorium alexbelgium) |
| Hostname z innych dodatków | `db21ed7f-postgres-latest`, port `5432` |
| Superużytkownik | **`maz00r`** — rola `postgres` **nie istnieje** |
| Bazy na serwerze | `teslamate` (TeslaMate), `poleasingowe` (nasza) |
| Locale bazy | `en_US.utf8`, encoding UTF8 |
| Rola aplikacji | `poleasingowe_app` — właściciel bazy i schematów |
| Rola Grafany | `grafana_ro` — ma wyłącznie `CONNECT` |
| Schematy | `app` i `reporting` — **już utworzone**, właściciel `poleasingowe_app` |
| Uprawnienia | `PUBLIC` odebrane na bazie i na schemacie `public` |
| Grafana | działa jako osobny dodatek, ma już źródło danych do bazy `teslamate` |

Konsekwencje dla kodu:

- Migracje **nie tworzą schematów** `app` ani `reporting` — one istnieją.
  Migracja `001` zaczyna od tabel.
- Add-on łączy się **wyłącznie** jako `poleasingowe_app`. Nigdy jako
  `maz00r`. Hasła superużytkownika nie ma w opcjach add-onu i nie ma go
  w repozytorium.
- `poleasingowe_app` **nie jest superużytkownikiem**. Jeżeli jakakolwiek
  migracja miałaby wymagać `CREATE EXTENSION`, `ALTER SYSTEM` albo innej
  operacji wymagającej uprawnień nadrzędnych — **zatrzymaj się i zgłoś**.
  Taką operację wykonuję ręcznie poza add-onem, a spec i tak zakłada brak
  rozszerzeń (§8.3).
- Locale bazy to `en_US.utf8`. Sortowanie polskich znaków (Škoda, Citroën)
  pójdzie regułami angielskimi. Tam, gdzie kolejność ma być czytelna dla
  człowieka — lista marek i modeli w UI — użyj
  `ORDER BY ... COLLATE "pl-PL-x-icu"`. Nie zmieniaj locale bazy: jest
  ustalone przy tworzeniu i nie podlega zmianie.
- Zasoby VM zostały zweryfikowane jako wystarczające. Budżet z §1.1
  obowiązuje mimo to i ma być mierzony po wdrożeniu, nie deklarowany.

---

## 1. Środowisko docelowe

HAOS jako VM na Proxmoksie. Dell Wyse 5070, Pentium Silver J5005 (4 słabe
rdzenie), 6144 MB RAM przy 7,6 GiB fizycznych na hoście. Dysk 44 GB na
thin-LVM. amd64. Serwer stoi w innej lokalizacji — dostęp wyłącznie przez
panel HA wystawiony przez cloudflared.

Na tej samej maszynie działają już: Home Assistant (~1,8 GB), PostgreSQL,
Grafana i TeslaMate. **To jest maszyna ograniczona zasobowo i jest to twarde
ograniczenie projektowe, nie preferencja.**

### 1.1 Budżet zasobów

| Pozycja | Limit |
|---|---|
| Obraz kontenera (bez przeglądarki) | < 200 MB |
| RSS w spoczynku | < 170 MB |
| RSS w szczycie (20 obserwowanych w dogrywce) | < 250 MB |
| CPU w spoczynku | ~0% — dispatcher śpi do najbliższego terminu |
| Połączenia do Postgresa | pula 1–3, `CONNECTION LIMIT 5` na roli |
| Przyrost bazy po roku | < 300 MB |
| Katalog debug w `/data` | < 200 MB z rotacją |

Każda decyzja podnosząca którąkolwiek pozycję wymaga uzasadnienia w opisie
commita. Przy przekroczeniu budżetu zgłoś to zamiast podnosić limit.

---

## 2. Współdzielony PostgreSQL — zasady bezwzględne

Instancja obsługuje **TeslaMate**. Baza `teslamate` zawiera dane przejazdów
i ładowań, których **nie da się odtworzyć z żadnego innego źródła**. Ich
utrata jest nieodwracalna.

Zasady bez wyjątków:

1. Dodatek to opakowanie oficjalnego obrazu `postgres`. Serwer hostuje wiele
   baz; `POSTGRES_DB` w jego opcjach dotyczy wyłącznie pierwszej
   inicjalizacji i nie ma dla nas znaczenia. Baza `poleasingowe` jest już
   utworzona ręcznie. **Add-on nigdy nie tworzy ani nie usuwa baz.**
2. Add-on łączy się **wyłącznie** do bazy `poleasingowe`. Nigdy do
   `teslamate`, nigdy do `postgres`, nigdy do bazy recordera HA.
3. Add-on **nigdy** nie wykonuje `ALTER SYSTEM`, nie modyfikuje
   `postgresql.conf` ani `pg_hba.conf`, nie zmienia `shared_buffers`,
   `max_connections` ani żadnego parametru globalnego, nie wywołuje
   `pg_terminate_backend`, nie robi `VACUUM FULL` i nie dotyka autovacuum.
4. DDL wyłącznie w schematach `app` i `reporting` bazy `poleasingowe`.
5. Limity roli są już ustawione i mają pozostać:
   ```sql
   statement_timeout = '30s'
   lock_timeout = '5s'
   idle_in_transaction_session_timeout = '60s'
   CONNECTION LIMIT 5
   ```
   Jeżeli jakieś zapytanie nie mieści się w 30 s — popraw zapytanie,
   nie limit.
6. Migracje pod `pg_advisory_lock` z **własną, stałą, udokumentowaną
   wartością klucza**, nie kolidującą z blokadami migracji Ecto używanymi
   przez TeslaMate. Zapisz wybraną wartość w `DOCS.md`.
7. Wolumen zapisów ma pozostać znikomy wobec TeslaMate. Służy temu reguła
   zapisu snapshotów wyłącznie przy zmianie (§8.4). Nie łam jej „dla wygody
   wykresu".
8. Brak dostępu do bazy przy starcie = retry z backoffem do 5 minut
   i czytelny komunikat w panelu diagnostycznym. **Nigdy crash-loop
   kontenera** — restartująca się usługa dobijająca się do współdzielonego
   Postgresa jest gorsza niż usługa wyłączona.

---

## 3. Źródła

- poleasingowe.pl
- aukcje.efl.com.pl
- autoprzetarg.pl
- aukcje.leasygroup.pl (zweryfikuj dokładny adres)

Większość wymaga zalogowania, żeby widzieć pełne dane licytacji.

---

## 4. ETAP 0 — REKONESANS (najpierw, potem STOP)

Zanim napiszesz jakikolwiek parser, Dockerfile czy migrację:

1. Pobierz i zapisz w `fixtures/<serwis>/` realny HTML listy aukcji oraz
   strony pojedynczej aukcji — w wersji niezalogowanej, a gdy będę już miał
   konta, także zalogowanej.
2. Dla każdego serwisu ustal i opisz:
   - czy lista i szczegóły wymagają logowania i co dokładnie widać bez niego;
   - **czy formularz logowania ma 2FA albo captchę** — to przesądza
     o wykonalności automatycznego logowania;
   - czy strona jest statyczna, czy renderowana JS-em;
   - **czy istnieje wewnętrzne API JSON/XHR** (zakładka Network). Jeśli tak,
     używamy go zamiast parsowania HTML — najtańsza ścieżka, ma priorytet;
   - czy serwer zwraca `ETag` / `Last-Modified` i honoruje żądania warunkowe;
   - czy widać nagłówki limitów tempa (`X-RateLimit-*`, `Retry-After`);
   - paginację i filtry po stronie serwera;
   - jakie pola są na liście, a jakie dopiero w szczegółach;
   - czy widać cenę aktualną i liczbę ofert, czy tylko wywoławczą;
   - **czy serwis stosuje dogrywkę** (przedłużenie przy ofercie w ostatnich
     minutach) i jaka jest jej reguła;
   - co mówi robots.txt i regulamin o automatycznym pobieraniu.

   Oraz ustalenia krytyczne dla domknięcia aukcji (§11.4, §11.5, §11.8):

   a) **Ile dokładnie trwa dogrywka** i w jakim oknie przed końcem oferta ją
      wyzwala. Źródło: regulamin. Cytat do `RECON.md`.
   b) **Jak długo cena jest widoczna po wygaśnięciu** — pomiar, nie domysł:
      odpyt stronę aukcji po 2 s, 5 s, 15 s, 60 s i 5 min od `ends_at`
      i zapisz, kiedy cena znika. Wymaga aukcji kończącej się w trakcie
      rekonesansu — zaplanuj to.
   c) **Czy strona aukcji pokazuje historię/listę ofert** (kto, kiedy, ile)
      i czy przeżywa zamknięcie. To bezpośrednio zasila §11.8.
   d) W jakiej formie podany jest czas do końca: atrybut `data-*`, timestamp
      w JSON, czy licznik dorysowywany JS-em. §11.7 zależy od odpowiedzi.
   e) Czy w końcówce strona odświeża cenę AJAX-em bez przeładowania — jeśli
      tak, ten endpoint jest tańszy i dokładniejszy niż cała strona.
   f) `Set-Cookie` po zalogowaniu: `Max-Age` / `Expires`. Sesja wygasająca
      w środku dogrywki to realny scenariusz.
   g) **Zrzut aukcji ZAKOŃCZONEJ** per serwis. Bez tego nie da się napisać
      wykrywania stanu końcowego.
   h) Ile jest aktywnych ofert samochodowych per serwis i czy publikowany
      jest VIN (deduplikacja z §8.4).
3. Wskaż, które serwisy da się obsłużyć samym `httpx`, bez przeglądarki.
4. Zapisz jako `RECON.md` — łącznie z przepisaną tabelą ze §0, żeby fakty
   o środowisku były w jednym miejscu — i **czekaj na moją akceptację**.

Selektory, nazwy pól i kształty odpowiedzi mają pochodzić wyłącznie z plików
w `fixtures/`. Nic nie zmyślaj i nie zgaduj.

---

## 5. Stack

| Warstwa | Wybór | Powód |
|---|---|---|
| Język | Python 3.12, `uv` | zgodność z bazowym obrazem HA |
| HTTP | `httpx` (async, HTTP/2, keep-alive) | jeden klient per źródło |
| Parsowanie | `selectolax` | parser w C, znacznie tańszy od BeautifulSoup |
| Baza | `psycopg 3` (async) + `psycopg_pool` | PG 17.5, bez ORM |
| Migracje | numerowane `.sql` + tabela `schema_migration`, `pg_advisory_lock` | bez Alembica |
| Web | FastAPI (Starlette + `Depends`) | routing i DI |
| Szablony | Jinja2 + HTMX z pliku lokalnego | bez build stepu, bez SPA, bez CDN |
| CSS | jeden ręcznie pisany plik | brak Node w obrazie, brak etapu build |
| Walidacja | pydantic **tylko** dla `options.json` | jedyne miejsce, gdzie się opłaca |
| Scheduler | własny dispatcher na asyncio | patrz §11 |
| Testy | pytest, pytest-asyncio, lokalny PG 17 na Macu | |
| Jakość | ruff, `mypy --strict`, import-linter | dev-time, zero kosztu runtime |

**Bez ORM. Bez SQLAlchemy.** SQL pisany ręcznie, wyłącznie wewnątrz
repozytoriów. Zapytania jako stałe modułowe, nigdy sklejane f-stringami;
parametry wyłącznie przez placeholdery.

**Pydantic nie służy do reprezentacji aukcji.** Obiekty domenowe to
`@dataclass(slots=True, frozen=True)`.

**Playwright tylko jeśli rekonesans wykaże konieczność.** Wtedy: osobny
wariant obrazu, przeglądarka jako **subprocess uruchamiany na żądanie
i zamykany natychmiast po pobraniu**. Nigdy trwale działająca instancja.
Jeśli okaże się wymagany przez wszystkie serwisy — zatrzymaj się i zgłoś.

**Alpine vs Debian.** Bazowy obraz HA jest alpine'owy. Jeśli którakolwiek
zależność nie ma koła `musllinux`, przełącz się na wariant Debian zamiast
kompilować w obrazie — kompilacja na J5005 trwa nieakceptowalnie długo.

---

## 6. Architektura — porty i adaptery

Cztery warstwy, zależności wyłącznie do wewnątrz:

```
interfaces/       routery FastAPI, szablony, CLI
                  → wywołuje tylko application; zero SQL, zero httpx
application/      use case'y, porty (Protocol), Unit of Work
domain/           encje, value objects, reguły. ZERO zależności
                  zewnętrznych — wyłącznie stdlib
infrastructure/   adaptery: sources/, persistence/, supervisor/, scheduler/
```

`domain/` nie wie, że istnieje HTTP, SQL, Grafana ani Home Assistant.
`application/` operuje na Protokołach; implementacje wstrzykiwane
w `interfaces/deps.py` przez `Depends`. Żadnych globalnych singletonów,
żadnego modułu `utils.py`.

### 6.1 Egzekwowanie granic

`import-linter` z kontraktem `layers` w `pyproject.toml`, uruchamiany
w pre-commit i CI. Naruszenie warstwy wywala build. To jedyny mechanizm,
który realnie powstrzymuje rozjazd architektury — reszta to dobre chęci.

### 6.2 Wzorce i ich konkretne miejsca

- **Strategy + Protocol** — `AuctionSource` w `application/ports.py`,
  jedna implementacja na serwis w `infrastructure/sources/`.
- **Registry** — mapa `key -> klasa adaptera`, wypełniana deklaratywnie.
  Włączanie i wyłączanie źródeł z opcji add-onu bez zmiany kodu.
- **Anti-corruption layer** — `infrastructure/sources/<serwis>/mapper.py`
  tłumaczy surowy kształt serwisu na model domenowy. Dziwactwa serwisu nie
  wyciekają poza ten plik.
- **Repository** — `AuctionRepository`, `SnapshotRepository`,
  `WatchlistRepository`, `RunLogRepository`. SQL wyłącznie w
  `infrastructure/persistence/`.
- **Unit of Work** — context manager na transakcji; commit na wyjściu,
  rollback na wyjątku. Jedna transakcja per use case, nie per zapytanie.
- **Value objects** — `Money(Decimal, Currency)`, `Mileage`, `Vin`
  z walidacją w konstruktorze. **Nigdy `float` do ceny.**
- **Domain events** — `PriceChanged`, `AuctionEnded`, `NewMatchingListing`,
  `SourceAuthLocked`. Emitent nie wie, kto słucha. Zdarzenia zostają wewnątrz
  aplikacji — nie ma po drugiej stronie encji HA.
- **Clock jako port** — `Clock.now()` wstrzykiwany. Żadnego `datetime.now()`
  poza implementacją portu; inaczej harmonogram jest nietestowalny.
- **Wyjątki domenowe** — `SourceUnavailable`, `ParseFailed`,
  `AuthenticationFailed`, `SessionExpired`, `DatabaseUnavailable`. Łapane na
  granicy use case'u i logowane do `run_log`. Nigdy gołe `except:`.

### 6.3 Antywzorce — czego nie chcę

Logiki biznesowej w handlerach tras. Plików `models.py` po 800 linii.
Importów cyklicznych „rozwiązywanych" importem w środku funkcji. Mieszania
sync i async w jednej ścieżce wywołań. `from x import *`. Magicznych stringów
zamiast `enum`. Klas z jedną metodą tam, gdzie wystarczy funkcja. Warstw
abstrakcji „na wypadek gdyby kiedyś" — jeśli nie ma dziś drugiej
implementacji, nie ma interfejsu.

---

## 7. Struktura repozytorium add-onów

```
repository.yaml
poleasingowe/
  config.yaml            # slug, wersja, arch [amd64], opcje + schema, ingress
  build.yaml             # ghcr.io/home-assistant/amd64-base-python:3.12-alpine
  Dockerfile
  apparmor.txt
  rootfs/etc/s6-overlay/s6-rc.d/...
  DOCS.md  CHANGELOG.md  icon.png  logo.png
  translations/{en,pl}.yaml
  app/
    domain/  application/  infrastructure/  interfaces/
    migrations/          # 001_init.sql, 002_....sql
    static/app.css       # jeden plik
    templates/
  grafana/dashboards/    # JSON, wersjonowane
  tests/
  fixtures/
```

### 7.1 Wymagania add-onu

- `ingress: true`, `ingress_port`, `panel_icon: mdi:car-search`,
  `panel_title`. FastAPI obsługuje dynamiczny prefiks z nagłówka
  `X-Ingress-Path` (`root_path` w middleware); w szablonach wyłącznie
  `url_for`, nigdy ścieżki na sztywno.
- **Ingress zapewnia uwierzytelnienie HA — nie dokładaj własnego logowania.**
  `ports` puste, nic nie publikujemy na zewnątrz.
- Opcje w `config.yaml` z typowanym `schema`. Odczyt z `/data/options.json`,
  walidacja pydantikiem przy starcie. Błędna konfiguracja = jasny komunikat
  i zatrzymanie, nie działanie z domyślnymi.
  Minimalny zestaw, z wartościami domyślnymi wg §0:
  `db_host` (`db21ed7f-postgres-latest`), `db_port` (`5432`),
  `db_name` (`poleasingowe`), `db_user` (`poleasingowe_app`),
  `db_password` (`password`), `grafana_base_url`, `credentials` (lista),
  `sources` (lista z per-źródłowym floorem i limitem tempa), `log_level`,
  `debug_dumps` (bool, domyślnie `false`), `notify_on_critical` (bool,
  domyślnie `false`).
- W `/data` mieszkają wyłącznie: sesje (`/data/sessions/`, 0600), zrzuty
  debug (`/data/debug/`, rotacja) i cache. **Dane trwałe są w Postgresie.**
- Eksporty do `/share/poleasingowe/`, strumieniowo.
- **Własny backup, niezależny od snapshotu HA.** Snapshot dodatku Postgres
  obejmuje obie bazy naraz, więc odtworzenie samej bazy `poleasingowe`
  cofnęłoby też TeslaMate. Add-on wykonuje `pg_dump -Fc` **własnej bazy**
  raz na dobę do `/share/poleasingowe/backup/`, trzyma 7 kopii i loguje
  wynik do `run_log`. Nigdy `pg_dumpall`.
- `homeassistant_api` **opcjonalnie, domyślnie wyłączone** — jedyne
  zastosowanie to `persistent_notification` przy stanie krytycznym
  (`AUTH_LOCKED`, źródło w circuit breakerze, brak bazy > 10 min). To nie
  jest encja, to alarm operacyjny.
- s6-overlay, `init: false`, poprawne zamknięcie na SIGTERM: dispatcher kończy
  bieżący odpyt, sesje httpx i pula połączeń domykają się czysto.
- Profil AppArmor. Bez `privileged`, `host_network`, `full_access`.
- Dockerfile wieloetapowy, bez kompilatorów w warstwie runtime.
- **Jeden proces, jeden event loop.** Bez uvicorn workers, bez
  multiprocessingu, bez Redisa, bez Celery.
- W `DOCS.md` napisz wprost: dane leżą w wolumenie add-onu PostgreSQL, nie
  w `/data` tego add-onu; odtworzenie wymaga obu.

---

## 8. Model danych

Baza `poleasingowe`. Tabele w schemacie `app`, widoki w `reporting`.
**Oba schematy już istnieją — migracje ich nie tworzą.**

### 8.1 Tabele

- **`source`** — key, nazwa, włączone, interwał przemiatu listy, limit tempa,
  floor interwału, stan uwierzytelnienia, `consecutive_auth_failures`.
- **`auction`** — `source_id`, `external_id`, url, marka, model, wersja,
  rocznik, przebieg, paliwo, skrzynia, pojemność, moc, VIN, nadwozie, kolor,
  lokalizacja, sprzedający, `price_start`, `price_current`, `currency`,
  `bid_count`, `ends_at`, `status`, `first_seen_at`, `last_seen_at`,
  `content_hash`, `raw_json`, `next_poll_at`, `poll_tier`,
  `consecutive_failures`, `final_price_state`, `duplicate_of`,
  `last_price_lead_seconds` — sekundy między ostatnią obserwacją z ceną
  a faktycznym końcem; miara jakości pomiaru.
- **`price_snapshot`** — `auction_id`, `ts`, `price`, `bid_count`, `ends_at`,
  `bid_gap` — ile ofert przegapiono przed tym snapshotem (0 = komplet).
- **`watchlist`** — `auction_id`, notatka, cena docelowa, `added_at`.
- **`saved_filter`** — nazwa, kryteria `jsonb`, `created_at`.
- **`run_log`** — źródło, start, koniec, nowe/zmienione, błędy, RSS procesu,
  rozmiar bazy.

### 8.2 Typy

- kwoty: `numeric(12,2)` + kolumna waluty
- czas: `timestamptz`, wszystko zapisywane w UTC; konwersja do strefy lokalnej
  wyłącznie w warstwie widoku
- `raw_json`: `jsonb` — bez ręcznej kompresji, Postgres robi TOAST sam
- statusy: `text` + `CHECK` — **nie** typy enum Postgresa, bo ich migracje bolą
- `last_price_lead_seconds` i `bid_gap`: `integer`, `NULL` dopuszczalny
  i **znaczący** — `last_price_lead_seconds` jest `NULL` dla `CONFIRMED`,
  `bid_gap` jest `NULL` dla pierwszego snapshotu aukcji (§11.8)

### 8.3 Ograniczenia i indeksy

```sql
UNIQUE (source_id, external_id)

CREATE INDEX ON app.auction (next_poll_at) WHERE status = 'ACTIVE';
CREATE INDEX ON app.auction (status, ends_at);
CREATE INDEX ON app.auction (make, model, year);
CREATE INDEX ON app.auction (vin) WHERE vin IS NOT NULL;
CREATE INDEX ON app.price_snapshot (auction_id, ts DESC);
CREATE UNIQUE INDEX ON app.watchlist (auction_id);
```

Indeks częściowy na `next_poll_at` jest kluczowy — to zapytanie wykonuje się
najczęściej i musi trafiać wyłącznie w aktywne aukcje.

**Bez rozszerzeń.** Żadnego `pg_trgm`, FTS ani niczego wymagającego
`CREATE EXTENSION` — rola aplikacji nie ma na to uprawnień, a instalacja
w instancji dzielonej z TeslaMate to zmiana, której chcę uniknąć. Przy kilku
tysiącach wierszy `ILIKE` na indeksowanych kolumnach wystarczy.

Sortowanie prezentacyjne (marki, modele) z `COLLATE "pl-PL-x-icu"` — locale
bazy to `en_US.utf8` i bez tego polskie znaki ustawią się nienaturalnie.

### 8.4 Reguły rozmiaru i obciążenia

- **`price_snapshot` zapisywany wyłącznie gdy zmieniła się cena, liczba ofert
  albo `ends_at`.** Odpyt bez zmiany aktualizuje tylko `last_seen_at`.
  Bez tej reguły dogrywka generuje setki identycznych wierszy na aukcję
  i niepotrzebnie obciąża instancję dzieloną z TeslaMate.
- `raw_json` przechowywany tylko dla aukcji obserwowanych i zakończonych.
  Dla reszty `NULL`.
- **Zakończonych aukcji z ceną końcową nigdy nie kasuj** — to najcenniejsza
  część bazy i jedyne źródło realnych cen rynkowych. Kasowaniu podlegają
  wyłącznie snapshoty aukcji nieobserwowanych starsze niż rok, z zachowaniem
  pierwszego i ostatniego.
- Deduplikacja między serwisami po VIN: oznacz przez `duplicate_of`,
  nie usuwaj.
- Zapisy zbiorcze jedną transakcją i `executemany`, nie wiersz po wierszu.

---

## 9. Kontrakt dla Grafany

Grafana działa i ma źródło danych do bazy `teslamate`. Dokładamy **drugie
źródło danych** na roli `grafana_ro`.

Rola i uprawnienia na poziomie bazy są **już utworzone** (§0). Migracja
tworząca widoki musi jedynie nadać dostęp do nich:

```sql
GRANT USAGE ON SCHEMA reporting TO grafana_ro;   -- idempotentnie
GRANT SELECT ON <nowy_widok> TO grafana_ro;
```

`ALTER DEFAULT PRIVILEGES` jest już ustawione dla roli `poleasingowe_app`
w schemacie `reporting`, więc widoki tworzone przez add-on dostają `SELECT`
automatycznie. Mimo to nadawaj `GRANT SELECT` jawnie w migracji — jest
idempotentny, a nie chcę zależeć od stanu ustawionego ręcznie miesiące
wcześniej.

**Grafana nigdy nie odpytuje tabel bazowych — wyłącznie widoki.** Gdyby
dashboardy podpięły się pod `app.auction`, schemat byłby zamrożony i każda
migracja po cichu psułaby wykresy.

Widoki w `reporting`:

- **`v_price_history`** — `auction_id`, `ts`, `price`, `bid_count`, `ends_at`
- **`v_auction_current`** — bieżący stan aukcji + flaga obserwowana
- **`v_market_stats`** — `make`, `model`, `year` oraz statystyki liczone
  z **`last_observed_price`** (nie `final_price` — cena zaobserwowana jako
  ostatnia nie zawsze jest ceną końcową):
  - `median_confirmed` — mediana po aukcjach z
    `final_price_state = 'CONFIRMED'`
    (`percentile_cont(0.5) WITHIN GROUP (ORDER BY ...)`);
  - `median_last_seen` — ta sama mediana po aukcjach `LAST_SEEN`;
  - `n_confirmed`, `n_last_seen` — liczebności obu grup;
  - `median_lead_seconds` — mediana `last_price_lead_seconds`, czyli jak
    daleko przed faktycznym końcem urwał się pomiar;
  - stosunek `last_observed_price` do ceny wywoławczej.

  **Obie mediany osobno, nigdy zmieszane.** `LAST_SEEN` to dolne oszacowanie
  (§11.5); wrzucone do jednej mediany z `CONFIRMED` zaniżałoby obraz rynku,
  a `median_lead_seconds` mówi, o ile ten pomiar jest gorszy.
- **`v_source_health`** — ostatnie przebiegi, błędy, stan uwierzytelnienia

Widoki są kontraktem publicznym: zmiana ich kształtu wymaga aktualizacji
dashboardów w tym samym commicie. Tabele bazowe można refaktorować dowolnie,
dopóki widoki zwracają to samo.

`v_market_stats` jako zwykły widok na start. Jeśli okaże się wolny na J5005 —
`MATERIALIZED VIEW` odświeżany raz na dobę w oknie bezczynności, **nie
częściej**, bo `REFRESH` konkuruje o I/O z zapisami TeslaMate.

Dashboardy jako pliki JSON w `grafana/dashboards/`, wersjonowane w repo.
Nie klikane w UI i zapominane.

---

## 10. Uwierzytelnianie w serwisach

Ruch jest imienny — powiązany z moim kontem, nie z anonimowym IP. Uprzejmość
wobec serwisu przestaje być opcją, a błąd w pętli retry może skończyć się
blokadą konta, nie odrzuconym żądaniem.

### 10.1 Port

`AuthenticatedSource(AuctionSource)` dokłada `ensure_authenticated(session)`.
Adaptery serwisów publicznych implementują samo `AuctionSource` — nie zmuszaj
ich do pustego `login()`.

### 10.2 Wymagania

- Jedna sesja per źródło na cały czas życia procesu. **Nigdy logowanie per
  żądanie.**
- Cookie jar serializowany do `/data/sessions/<source>.json`, uprawnienia 0600,
  wczytywany przy starcie. Restart add-onu nie powoduje ponownego logowania.
- Wykrywanie wygaśnięcia po **treści odpowiedzi**, nie po samym kodzie HTTP:
  przekierowanie na formularz logowania, brak markera zalogowania w HTML,
  401/403. Marker definiuje adapter, nie warstwa wspólna.
- Ponowne logowanie leniwe — dopiero po wykryciu wygaśnięcia, z jednym retry.
  Nigdy prewencyjnie.
- **Twardy limit nieudanych logowań: 3 na źródło.** Po przekroczeniu adapter
  wchodzi w `AUTH_LOCKED`, przestaje wykonywać jakiekolwiek żądania i wymaga
  ręcznego resetu z UI. Część serwisów blokuje konto po kilku próbach.
  **Licznik jest trwały w bazie** — inaczej pętla restartów kontenera obejdzie
  limit. Wejście w `AUTH_LOCKED` wyzwala powiadomienie, jeśli włączone.
- Circuit breaker per źródło: po serii błędów sieciowych źródło pauzuje
  z backoffem wykładniczym; pozostałe pracują dalej.
- Tokeny CSRF i ukryte pola formularza wyciągane ze strony logowania przy
  każdej próbie. Nic zaszytego na sztywno.
- Poświadczenia wyłącznie z opcji add-onu. Nigdy w kodzie, nigdy w obrazie,
  nigdy w logach, nigdy w `raw_json`.
- **Redakcja w logach i zrzutach**: hasła, ciasteczka, `Authorization`,
  `Set-Cookie`, tokeny — zastępowane `***` przez filtr loggera. Zrzuty HTML
  przechodzą przez ten sam filtr. Strona po zalogowaniu zawiera moje dane
  osobowe.
- Zrzuty debug domyślnie wyłączone; włączone piszą wyłącznie przy błędzie
  parsowania, z rotacją i limitem rozmiaru.
- Jeśli rekonesans wykaże 2FA albo captchę: automatyczne logowanie odpada.
  Zamiast tego ręczny import ciasteczek sesji wklejanych w UI, z jawnym
  komunikatem o terminie wygaśnięcia.

---

## 11. Harmonogram odpytywania

### 11.1 Dispatcher

Jedna pętla asyncio. Pobiera aukcje z `next_poll_at <= now()` posortowane po
`ends_at`, przepuszcza przez token bucket per źródło, po każdym odpycie
przelicza `next_poll_at`.

**Pętla śpi do najbliższego terminu**, nie budzi się na stałym ticku:
`await asyncio.sleep(min(next_due - now, 60))`. W spoczynku koszt CPU jest
zerowy, a baza nie dostaje zapytań bez potrzeby.

Zbiór identyfikatorów w locie trzymany w pamięci — proces jest jeden, więc to
wystarczy. Nie buduj blokad w bazie „na przyszłość".

### 11.2 Polityka jako Strategy

`PollingPolicy` w `domain/` — **czysta funkcja** `(auction, now) -> datetime`.
Bez sieci, bez bazy, bez `datetime.now()` w środku. Cała logika progów jest
przez to testowalna tabelą przypadków i to jedyne miejsce, gdzie wolno ją
zmieniać.

`TieredEndgamePolicy` dla aukcji **obserwowanych**:

| Czas do końca | Interwał |
|---|---|
| > 7 dni | 24 h |
| 1–7 dni | 6 h |
| 6–24 h | 1 h |
| 1–6 h | 15 min |
| 15–60 min | 3 min |
| < 15 min | 60 s |

Progi i interwały konfigurowalne, z **domyślną dolną granicą 60 s**,
możliwością podniesienia floora per źródło i jednym wyjątkiem w dół — trybem
30 s opisanym niżej, dopuszczalnym wyłącznie dla źródła z udowodnionym
limitem tempa. Przy pięciu aukcjach kończących się równocześnie na jednym
serwisie 60 s to już kilkanaście żądań na minutę z zalogowanej sesji. Niżej
schodzimy tylko wtedy, gdy rekonesans wykaże, że serwis to toleruje.

Endgame ma dwa tryby:

- `< 15 min do ends_at` → 60 s (floor domyślny)
- `< 3 min do ends_at` → 30 s, ale **wyłącznie** dla źródła, dla którego
  rekonesans wykazał nagłówki limitu tempa dopuszczające to z zapasem
  (np. poleasingowe.pl: `x-ratelimit-limit: 60`). Bez takiego dowodu
  zostaje 60 s.

Uzasadnienie zejścia do 30 s: okno dogrywki to ~60 s, więc dwie próbki
w oknie gwarantują wykrycie przedłużenia, zanim aukcja się domknie. Jedna
próbka na okno tego nie gwarantuje.

Tryb 30 s podwaja szczytowe tempo żądań wobec scenariusza z §1.1 (20
obserwowanych w dogrywce). Budżet z §1.1 dotyczy RAM, nie liczby żądań, więc
formalnie nie pęka — ale ma to być zmierzone przy wdrożeniu, nie założone.

W endgame `ends_at` po każdym odpycie jest odczytywany na nowo — wartość
z bazy jest tylko wskazówką, nie prawdą.

**Aukcje nieobserwowane nie są odpytywane pojedynczo w ogóle.** Wystarcza im
zbiorczy przemiat listy raz na kilka godzin. To główna oszczędność całego
systemu — koszt rośnie z liczbą obserwowanych, nie z liczbą ofert w serwisie.

### 11.3 Tani odpyt

W tej kolejności, przerywając na pierwszym trafieniu:

1. Żądanie warunkowe `If-None-Match` / `If-Modified-Since` → `304` kończy
   sprawę bez pobierania treści.
2. Hash surowych bajtów odpowiedzi porównany z `content_hash` → identyczny
   oznacza pominięcie parsowania. **Parsowanie jest najdroższą operacją CPU
   w całej aplikacji i nie wolno go wykonywać na niezmienionej treści.**
3. Dopiero teraz parsowanie i mapowanie.

Zawsze `Accept-Encoding: gzip, br`, HTTP/2, keep-alive. Jeden
`httpx.AsyncClient` per źródło, tworzony raz.

### 11.4 Dogrywka

Jeśli po odpycie `ends_at` przesunął się w przyszłość: zapisz zmianę
w `price_snapshot`, zostaw aukcję w trybie endgame, przelicz interwał od
nowego czasu. **Nie traktuj przesunięcia jako błędu parsowania.**

### 11.5 Domknięcie aukcji

Serwisy stosują dogrywkę (§11.4): oferta w ostatnich chwilach przedłuża
`ends_at`, zwykle o 60 s. Aukcja kończy się dopiero po pełnym oknie
dogrywki bez nowej oferty. Cena końcowa jest widoczna kilka sekund po
wygaśnięciu, potem oferta znika.

Dlatego po `ends_at` nie stosuje się stałego interwału, tylko celowanie
w moment:

1. `ends_at + 2 s` — pierwszy odpyt.
2. Jeśli oferta wciąż aktywna i `ends_at` się przesunął → dogrywka,
   wracamy do endgame.
3. Jeśli oferta zakończona z widoczną ceną → `CONFIRMED`, koniec.
4. Jeśli oferta zakończona bez ceny albo zniknęła → `LAST_SEEN`
   z ostatnim snapshotem, koniec.
5. Jeśli nierozstrzygnięte → ponów po 2 s, 5 s, 10 s, 20 s, 40 s.
   Maksymalnie 6 prób, potem `LAST_SEEN`.

Te ponowienia są zwolnione z token bucketa źródła — to najwyżej sześć
żądań na aukcję, raz w jej życiu. Odnotuj je w `run_log` osobno.

`CONFIRMED` oznacza cenę odczytaną ze strony po zakończeniu.
`LAST_SEEN` oznacza ostatnią obserwację przed zamknięciem i jest
dolnym oszacowaniem — zapisz wtedy `last_price_lead_seconds`.

### 11.6 Kolizje

Kilka obserwowanych aukcji kończących się w tej samej minucie na jednym
serwisie konkuruje o ten sam bucket. Priorytet ma bliższy `ends_at`. Jeśli
opóźnienie w kolejce przekroczy interwał tieru, zapisz to w `run_log` — to
sygnał, że mam za dużo obserwowanych na jednym serwisie albo za niski limit
tempa.

### 11.7 Czas

Preferuj czas pozostały raportowany przez serwis nad różnicą
`ends_at - now()`. Zegar VM-ki potrafi dryfować, a przy interwale 60 s
30 sekund dryfu to różnica między złapaniem ceny końcowej a jej przegapieniem.
Sprawdzaj dryf przy starcie i ostrzegaj w logu.

### 11.8 Kompletność historii ofert

Celem w końcówce jest zalogowanie każdej oferty, nie tylko ostatniej.
`bid_count` jest miarą kompletności: jeśli między dwoma snapshotami
wzrósł o więcej niż 1, przegapiliśmy oferty pośrednie.

Zapisuj w `price_snapshot` kolumnę `bid_gap` = przyrost `bid_count`
ponad 1. Suma `bid_gap` per aukcja to liczba ofert, których nie widzieliśmy.

`bid_gap` liczony jest przy zapisie snapshotu względem `bid_count`
z **poprzedniego snapshotu tej aukcji**, nie względem stanu w `auction` —
bo §8.4 zapisuje snapshot wyłącznie przy zmianie. Pierwszy snapshot aukcji
ma `bid_gap = NULL`, nie `0`: w chwili pierwszej obserwacji aukcja mogła już
mieć oferty, a zero znaczyłoby „komplet".

Jeśli rekonesans wykaże, że serwis udostępnia listę lub historię ofert
na stronie aukcji — parsuj ją zamiast polegać na różnicach snapshotów.
To ma pierwszeństwo przed częstszym odpytywaniem i jest istotniejsze
niż jakikolwiek parametr harmonogramu.

---

## 12. Interfejs (Ingress)

Add-on obsługuje **operacje**. Analityka jest w Grafanie.

- Lista z filtrami: marka/model, zakres ceny, rocznik, przebieg, paliwo,
  skrzynia, lokalizacja, źródło, data zakończenia. Sortowanie, szukanie
  tekstowe. **Paginacja keyset po stronie serwera, nie `OFFSET`** — 50 wierszy
  na stronę.
- Szczegóły aukcji: wszystkie pola, link do oferty, **link do dashboardu
  Grafany** z `?var-auction_id=<id>`. Bez własnego wykresu.
- Watchlist: dodaj/usuń, notatka, cena docelowa, wyróżnienie po przekroczeniu
  progu.
- Widoki „kończą się w 24 h" i „nowe od ostatniej wizyty".
- Zapisane filtry.
- Archiwum zakończonych z ceną końcową i znacznikiem pewności.
- Link do dashboardu rynkowego z `?var-make=&var-model=`.
- **Panel diagnostyczny**: stan każdego źródła, ostatni `run_log`, licznik
  `AUTH_LOCKED` z przyciskiem resetu, stan puli połączeń, rozmiar bazy,
  RSS procesu, dryf zegara, data ostatniego `pg_dump`.
- Eksport CSV/XLSX do `/share/poleasingowe/`, strumieniowo.
- Bez miniatur zdjęć na start. Jeśli kiedyś — proxy z cache na dysku i twardym
  limitem, nie hotlink.
- HTMX z lokalnego pliku statycznego. Żadnych CDN-ów.

---

## 13. Wymagania pozafunkcjonalne

- Izolacja adapterów: awaria jednego źródła nie przerywa przebiegu, ląduje
  w `run_log` i w panelu diagnostycznym.
- Rate limiting z jitterem, backoff wykładniczy, **`concurrency = 1` na
  serwis**. Nigdy nie dobijaj jednego serwisu równolegle.
- Testy parserów na `fixtures/`, offline, bez sieci.
- **Testy kontraktowe**: jeden sparametryzowany zestaw sprawdzający, że każdy
  adapter spełnia `AuctionSource`. Nowy adapter dostaje testy za darmo.
- Testy tabelaryczne `PollingPolicy`: pełna tabela progów plus przypadki
  brzegowe — dogrywka, `ends_at` w przeszłości, brak `ends_at`.
- **Testy repozytoriów wyłącznie na lokalnym PostgreSQL 17 na moim Macu.**
  Nigdy na instancji `db21ed7f-postgres-latest` — tam mieszka TeslaMate.
  Testy tworzą i kasują własną bazę tymczasową.
- `mypy --strict` przechodzi. `ruff` bez wyjątków w kodzie aplikacji.
- Logi strukturalne, poziom z opcji, domyślnie `INFO`, bez zrzutów treści.
- Po każdym przebiegu zapis RSS, rozmiaru bazy i stanu puli do `run_log` —
  budżet z §1.1 ma być mierzalny, nie deklaratywny.

---

## 14. Kolejność prac

Po każdym etapie: commit, krótkie podsumowanie, **czekaj na moją akceptację**.
Nie rób kilku etapów naraz.

Baza, role i schematy są już przygotowane (§0) — ten krok jest za nami.

1. `RECON.md` → akceptacja.
2. Szkielet warstw, kontrakt import-lintera, ruff, mypy, CI. **To stoi przed
   logiką**, nie po niej.
3. `domain/` + `persistence/` + migracje (bez tworzenia schematów) + testy
   repozytoriów na lokalnej bazie.
4. Schemat `reporting`: widoki + `GRANT SELECT` dla `grafana_ro`. Drugie
   źródło danych w Grafanie, weryfikacja że widzi **puste** widoki. Zrób to
   zanim powstaną dane — pusty widok, który działa, jest lepszy niż dashboard
   budowany na danych produkcyjnych.
5. Jeden adapter end-to-end — najprostszy wg rekonesansu, najlepiej publiczny
   — plus testy na fixtures.
6. Add-on: Dockerfile, `config.yaml`, s6, Ingress, AppArmor. Instalacja
   z lokalnego repozytorium, weryfikacja że wstaje na HAOS, łączy się do bazy
   jako `poleasingowe_app` i mieści się w budżecie z §1.1.
7. UI: lista, filtry, szczegóły, watchlist, panel diagnostyczny.
8. Logowanie: port, persystencja sesji, wykrywanie wygaśnięcia, licznik
   `AUTH_LOCKED` z resetem, redakcja w logach. Testy na fixtures z cyklem:
   sesja ważna → wygasła → odnowiona → zablokowana.
9. Dispatcher + `PollingPolicy`. Najpierw testy tabelaryczne, potem
   integracja. Weryfikacja endgame na sztucznej aukcji z `ends_at` za
   20 minut.
10. Pozostałe adaptery.
11. Backup `pg_dump`, dashboardy Grafany jako JSON w repo, statystyki, eksport.

Utrzymuj `README.md` (dev) i `DOCS.md` (użytkownik add-onu).
