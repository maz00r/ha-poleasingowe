# RECON.md — rekonesans źródeł (ETAP 0)

Stan: **ETAP 0a zamknięty** (zbieranie statyczne). ETAP 0b — pomiary czasowe —
otwarty, wymaga aukcji kończącej się w trakcie obserwacji.

Wszystkie twierdzenia w tym dokumencie pochodzą z plików w `fixtures/`
albo z zapisanych nagłówków w `fixtures/<serwis>/meta.json`. Tam, gdzie
czegoś nie zmierzyłem, jest napisane „nie zmierzone" — nie zgadywałem.

Data zbierania: 2026-09-06, UTC. Wszystko bez logowania.

---

## 1. Środowisko docelowe — przepisane z SPEC.md §0

Fakty potwierdzone na docelowej instancji, nie do weryfikowania.

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
| Grafana | osobny dodatek, ma już źródło danych do bazy `teslamate` |

Sprzęt: HAOS na Proxmoksie, Dell Wyse 5070, Pentium Silver J5005, 6144 MB RAM,
dysk 44 GB. Maszyna dzielona z HA (~1,8 GB), PostgreSQL, Grafaną i TeslaMate.

---

## 2. Tabela zbiorcza

| | poleasingowe.pl | aukcje.efl.com.pl | aukcje.leasygroup.pl | autoprzetarg.pl |
|---|---|---|---|---|
| Stack | Laravel + openresty | ASP.NET MVC | PHP + WAF F5 | ASP.NET MVC + Cloudflare |
| Lista bez logowania | **tak, z ceną i liczbą ofert** | **tak, z ceną** | tak | nie sprawdzone |
| Szczegóły bez logowania | **tak, pełne** (blok Alpine) | **tak, pełne** | tak | nie sprawdzone |
| Cena aktualna | **tak** (netto, brutto, EUR) | **tak** | tylko „Cena:" (stała) | nie sprawdzone |
| Liczba ofert | **tak** (`offers_count`, `bidders_count`) | **tak (`Ofert: N`)** | nie znaleziono | nie sprawdzone |
| Historia ofert | `lastOffers` w HTML / `topoffers` w API | **inline w HTML, jawna** | nie znaleziono | nie sprawdzone |
| Min. postąpienie | **`instep_price` wprost** | z regulaminu (10/100/200 zł) | nie znaleziono | nie sprawdzone |
| Render | dane serwerowe, JS tylko odświeża | **statyczny** | statyczny | nie sprawdzone |
| API JSON | **`POST /pl/auctions/bid-details/<id>`** | brak | brak (jQuery ajax, nieustalone) | nie sprawdzone |
| `ETag`/`Last-Modified` | **brak** | **brak** | **brak** | nie sprawdzone |
| `If-Modified-Since` → 304 | **nie, 200** | **nie, 200** | **nie, 200** | nie sprawdzone |
| Limit tempa w nagłówkach | **`x-ratelimit-limit` 60–120** | brak | brak | nie sprawdzone |
| Paginacja | serwerowa | serwerowa `?page=N` | `strona-N`, **robots blokuje > 1** | nie sprawdzone |
| **Dogrywka** | **+30 s, okno 30 s, max +30 min** | **BRAK — twardy koniec** | **+2 min, okno 2 min** | nie sprawdzone |
| Czas do końca | JS countdown + API `end_date` | **absolutny timestamp w HTML** | nie znaleziono | nie sprawdzone |
| Czas serwera | **`sdt.date` w API, bez logowania** | brak | brak | nie sprawdzone |
| VIN publiczny | **tak** | **tak** | **tak** | nie sprawdzone |
| Werdykt | **`httpx`, bez logowania do odczytu** | **`httpx`, bez przeglądarki** | `httpx`, ale mało danych | nie sprawdzone |

---

## 3. Rozbieżności ze SPEC.md — do decyzji

### 3.1 Krok 1 „taniego odpytu" (§11.3) jest martwy

Żadna dynamiczna strona w żadnym z trzech zbadanych serwisów nie zwraca
`ETag` ani `Last-Modified`. `If-Modified-Since` z datą z przeszłości dostaje
**HTTP 200**, nie 304, we wszystkich trzech. ETagi widoczne w nagłówkach
dotyczą wyłącznie plików statycznych (`robots.txt`).

Konsekwencja: cała oszczędność z §11.3 spada na krok 2 — `content_hash`.
Krok 1 należy zostawić w kodzie jako tani no-op (koszt zerowy, gdyby serwis
kiedyś zaczął go honorować), ale **nie wolno na nim opierać budżetu**.

Dowód: `fixtures/*/meta.json`, pola `headers`.

### 3.2 §11.5 i §11.2 zakładają dogrywkę ~60 s. Żaden serwis jej nie ma.

Trzy serwisy, trzy różne reguły — i **żadna nie jest 60-sekundowa**:

| Serwis | Okno wyzwalające | Przedłużenie | Limit |
|---|---|---|---|
| poleasingowe.pl | **30 s** | **+30 s** | **max +30 min** |
| aukcje.leasygroup.pl | **2 min** | **+2 min** | brak limitu w regulaminie |
| aukcje.efl.com.pl | — | **brak dogrywki** | — |

To obala dwa świeżo wprowadzone zapisy:

**(a) Uzasadnienie trybu 30 s w §11.2 nie broni się dla poleasingowe.pl.**
Spec mówi: „okno dogrywki to ~60 s, więc dwie próbki w oknie gwarantują
wykrycie przedłużenia". Przy oknie 30 s odpyt co 30 s daje **jedną** próbkę
na okno, a domyślny floor 60 s daje **jedną na dwa okna**. Przy tym serwisie
możemy przegapić całą końcówkę.

**(b) Drabinka z §11.5 (max ~79 s) jest za krótka dla poleasingowe.pl.**
Aukcja może być przedłużana do **30 minut** po pierwotnym `ends_at`.
Drabinka wyczerpie się po ~79 s i zapisze `LAST_SEEN`, gdy aukcja wciąż
trwa. Paradoksalnie **stary §11.5 — odpyt co 60 s przez 30 minut — trafiał
w tę regułę dokładnie**; usunęliśmy zapis, który dla najważniejszego źródła
był poprawny.

Zalecenie: **dogrywka musi być parametrem źródła, nie stałą globalną.**
Kolumna `source` powinna dostać okno dogrywki, długość przedłużenia i limit
łącznego przedłużenia; `PollingPolicy` czyta je zamiast stałych. Dla EFL
wartości są zerowe i cała logika endgame'u się dla niego nie uruchamia.

Cytaty źródłowe w §4 poniżej.

### 3.3 Zalogowana część poleasingowe.pl jest objęta `Disallow`

`robots.txt` blokuje `/pl/bidder-panel/*` — formularz logowania oraz
`/pl/bidder-panel/auctions/details/*/all_offers`, czyli pełną historię ofert,
której chce §11.8. Nie pobierałem tych stron.

Waga tego zmalała po ustaleniu z §4.2: **odczyt cen, liczby ofert i daty
końca nie wymaga logowania**. Blokada dotyczy wyłącznie pełnej historii
ofert. Czyli §11.8 dla tego źródła opiera się na `lastOffers` z HTML
i na różnicach `offers_count` między snapshotami — chyba że zdecydujesz
inaczej (§7).

---

## 4. Serwisy

### 4.1 aukcje.efl.com.pl — **rekomendowany pierwszy adapter**

Najtańsze i najbardziej przewidywalne źródło. Czysty ASP.NET MVC, zero
frameworka JS, komplet danych bez logowania.

**URL-e.** Lista: `/AuctionList/<kategoria>/<podkategoria>`, np.
`/AuctionList/Carefleet/Osobowe`. Paginacja serwerowa:
`/AuctionList?category=16&sort=Title-asc&fc=10&page=N` — **`page` liczony
od 0**, `fc` = rozmiar strony, w próbce 34 strony. Szczegóły:
`/Auction/<slug>-id<ID>`.

**`external_id`** — liczba na końcu slugu (`...-id435587`), potwierdzona
też jako `<input name="id" value="435383">` na stronie szczegółów.
Uwaga na slug: zawiera przecinki kodowane niespójnie (`,c` obok `%2c`)
w dwóch wariantach tego samego linku — **nie używaj slugu jako klucza**.

**Pola na liście** (`fixtures/efl/lista-01.html`): tytuł, zdjęcie, rok
produkcji, przebieg, typ nadwozia, pojemność, moc, `div.price` z ceną,
`div.to-end` z czasem zgrubnym („4 dni", „22 godziny").

**Pola dopiero w szczegółach** (`fixtures/efl/szczegoly-435587.html`):
VIN (`WAUZZZGY2PA052888`), nr rejestracyjny, kolor + „kolor metallic",
skrzynia, liczba drzwi i miejsc, rodzaj pojazdu, data pierwszej rejestracji,
liczba kluczy, instrukcja, książka przeglądów, masy, lokalizacja,
`Ofert: N`, `Aktualna cena (bez VAT)`, `Cena 'Kup teraz'`, format oferty.

**Czas do końca (punkt d).** „Do zakończenia: 22 godz. **07.09.2026 godzina
14:37:00**" — zgrubny opis **oraz absolutny timestamp** w zwykłym tekście
HTML. Brak licznika JS, brak atrybutu `data-*`. Parsuje się trywialnie.
Strefa nie jest podana jawnie — zakładam Europe/Warsaw, **do potwierdzenia
w 0b**.

**Historia ofert (punkt c).** Panel `<div class="hidden" data-tabs="bidders">`
jest **inline w HTML**, przełączany CSS-em — żadnego AJAX-a. W obu pobranych
aukcjach zawiera „Brak ofert kupna." (obie mają `Ofert: 0`). Regulamin §4 ust. 4:

> „Wszystkie oferowane przez Uczestników ceny są jawne oraz zostają
> uwidocznione w Serwisie Aukcyjnym EFL w trakcie trwania Aukcji."

Czyli przy aukcji z ofertami panel powinien zawierać pełną listę — **to
zaspokaja §11.8 bez ani jednego dodatkowego żądania**. Wymaga potwierdzenia
na aukcji z `Ofert > 0` (brak w próbce).

**Dogrywka (punkt a): BRAK.** Regulamin §4 ust. 3:

> „Po upływie terminu zakończenia Aukcji nie będą przyjmowane żadne kolejne
> oferty, bez względu na przyczynę niezamieszczenia oferty w Serwisie
> Aukcyjnym EFL przed jej zamknięciem."

W całym §4 nie ma klauzuli o przedłużeniu. `ends_at` jest twarde.

**Format licytacji — istotny dla modelu danych.** To licytacja **proxy**
w stylu eBaya, nie zwykłe podbijanie (regulamin §4 ust. 6–8):
uczestnik deklaruje cenę maksymalną, a system sam generuje oferty
o minimalne postąpienie. Minimalne postąpienie zależy od ceny wywoławczej:
**10 zł** do 1 000 zł, **100 zł** do 10 000 zł, **200 zł** powyżej.
Pierwsza oferta = cena wywoławcza. Istnieje ukryta **cena minimalna**
(rezerwowa) — serwis ujawnia sam fakt jej ustanowienia, nie wartość;
w HTML widoczne jako „Cena minimalna nie została osiągnięta".

**Logowanie.** `/Account/LogOn`, pola: `Login`, `Password`, `RememberMe`,
ukryte `__RequestVerificationToken` (CSRF, zmienny), `ReturnUrl`,
`IsSystemUser`. W HTML jedna wzmianka o „recaptcha", ale **nie znalazłem
aktywnego widgetu** — nierozstrzygnięte, do sprawdzenia przy logowaniu.
Logowanie jest potrzebne do składania ofert, nie do odczytu.

**robots.txt**: `User-agent: *` / `Allow: /`, plus `Sitemap`.
Sitemap zawiera wpisy z `lastmod` (od 2015 r.) — potencjalnie tani sposób
na wykrywanie nowych aukcji, **niezweryfikowany**.

**Werdykt: `httpx`, bez przeglądarki.** Rekomendacja na §14 pkt 5.

### 4.2 poleasingowe.pl — najbogatsze dane, dostępne bez logowania

**URL-e.** Rozdzielnik kategorii: `/pl/auctions`. Lista pojazdów:
`/pl/auctions/list/pub/all/vehicles` (dalsze podkategorie przez
`/vehicles/ecr_cartypess?mark=&model=`). Szczegóły:
`/pl/auctions/details/<slug>/<external_id>`.

**`external_id`** — alfanumeryczny, 8 znaków (`9pmz3459`, `1dl8a2ae`,
`e2n38a0e`). **Nie liczba** — kolumna tekstowa.

**Wewnętrzne API — najważniejsze znalezisko.** Strona szczegółów odpytuje:

```
POST https://poleasingowe.pl/pl/auctions/bid-details/<external_id>
Content-Type: application/json
X-XSRF-Token: <z ciasteczka XSRF-TOKEN>
credentials: include
```

Frontend serwisu robi to **co 1000 ms** (`INTERVAL_MS = 1000`, funkcja
`safeFetchWithRetry`). Odpowiedź bez logowania
(`fixtures/poleasingowe/xhr-bid-details.json`):

```json
{"auth":false,"sdt":{"date":"2026-09-06 16:17:14.796782","timezone_type":3,
 "timezone":"Europe\/Warsaw"},"auction":null,"topoffers":null,"isBidded":false}
```

Czyli: **`auction` i `topoffers` wymagają zalogowania**, ale `sdt.date` —
**czas serwera** — przychodzi zawsze. To rozwiązuje §11.7 wprost: mamy
darmowe, autorytatywne źródło czasu do wykrywania dryfu zegara VM-ki,
bez logowania i bez parsowania HTML.

Po zalogowaniu API zwraca (wg kodu strony): `auction.auction_current_price_formated`,
`auction.end_date`, `auction.auction_pending`, `auction.from_bn` oraz
**`topoffers` — listę ofert**. To jest tańsza i dokładniejsza ścieżka niż
parsowanie HTML i wg §4 ma pierwszeństwo.

**Marker wygaśnięcia sesji (§10.2).** Gotowy, wprost w kodzie serwisu:
odpowiedź z przekierowaniem (`response.redirected`) oznacza martwą sesję lub
zły CSRF; strona wywołuje wtedy `showSessionExpiredOnce()`. Adapter powinien
użyć tego samego kryterium plus `auth: false` w JSON-ie.

**Co widać bez logowania — komplet.** Kafelek listy
(`fixtures/poleasingowe/lista-vehicles-01.html`) zawiera: badge „AUKCJA
Z PROWIZJĄ", tytuł, rok, paliwo, przebieg, moc, nr rejestracyjny,
lokalizację, „Aktualna cena 110 600 PLN", „Najniższa cena z 30 dni"
oraz **„Ilość ofert: 0"**. Daty zakończenia na liście **nie ma w żadnej
postaci** — ani tekstem, ani w `data-*`, ani jako licznik.

Strona szczegółów renderuje etykietę „Aktualna cena" pustą (dopełnia ją JS),
ale **komplet danych jest serwerowo w HTML**, w obiekcie inicjalizującym
Alpine.js (`fixtures/poleasingowe/szczegoly-9pmz3459.html`):

```js
auction: { secure_conn: true, current_price: '40 500',
  current_price_euro: '9 457', current_price_brutto: '49 815',
  instep_price: 100.00, bidders_count: 0, offers_count: 0,
  min_offer_price: 40500.00, winner: ' - ', min_price_exceed: false,
  tillTheEnd: '19 godzin ', auction_pending: true, from_bn: false,
  lastOffers: [],
  endDate: moment('2026-09-07 12:00:00').tz("Europe/Warsaw") }
```

Czyli bez logowania mamy: cenę netto/brutto/EUR, **minimalne postąpienie
(`instep_price`)**, **minimalną kolejną ofertę (`min_offer_price`, liczbowo)**,
**liczbę ofert i licytujących**, flagę osiągnięcia ceny minimalnej,
**absolutną datę końca z jawną strefą `Europe/Warsaw`** oraz listę ofert
`lastOffers` (pustą przy zerowej liczbie ofert — kształt niepotwierdzony).
VIN jest w HTML (`TMBJH7NP0P7055920`).

To zmienia werdykt: **logowanie nie jest potrzebne do odczytu.** API
`bid-details` pozostaje przydatne do odświeżania na żywo i do `topoffers`,
ale adapter da się napisać bez sesji.

**Aukcje kończą się partiami.** Obie pobrane pozycje mają identyczne
`endDate` — `2026-09-07 12:00:00`. Przy sortowaniu domyślnym (`dzr`) sugeruje
to, że serwis zamyka wiele aukcji w tej samej sekundzie. Jeśli to reguła,
a nie zbieg okoliczności, **kolizje z §11.6 są strukturalne**, nie
incydentalne, i przy dogrywce 30 s to najgorszy możliwy scenariusz dla
jednego bucketa. **Do potwierdzenia w 0b.**

**Sortowanie i paginacja serwerowe.** `select#alist-sort` z wartościami:
`dzr` / `dzm` (data zakończenia rosnąco/malejąco), `cr` / `cm` (cena
wywoławcza), `or` / `om` (liczba ofert). **Domyślnie `dzr`** — lista bez
parametrów jest już posortowana po najbliższym końcu, co jest wprost
użyteczne dla dispatchera. Paginacja: `?page=N`, liczona od 1.

**Dogrywka (punkt a).** Regulamin, „Zakończenie i rozstrzygnięcie Aukcji":

> „Złożenie kolejnej oferty (podbicie) w przeciągu trzydziestu sekund od
> chwili złożenia ostatniej ważnej najwyższej oferty na Aukcji powoduje
> przedłużenie czasu trwania Aukcji o trzydzieści sekund. Aukcja jest
> przedłużana do czasu, gdy żaden z Oferentów nie dokona podbicia
> w przeciągu trzydziestu sekund od złożenia ostatniej najwyższej oferty.
> Aukcja może zostać przedłużona maksymalnie o 30 minut. Pierwsze
> przedłużenie następuje nie wcześniej niż na 30 sekund przed upływem
> terminu na jaki została ogłoszona Aukcja."

Okno 30 s, przedłużenie 30 s, sufit +30 min. Patrz §3.2 — to unieważnia
uzasadnienie trybu 30 s i długość drabinki z §11.5.

**Limity tempa.** Jedyny serwis, który je publikuje, i **różnicuje per trasę**:

| Trasa | `x-ratelimit-limit` |
|---|---|
| `/pl/auctions` | 60 |
| `/pl/auctions/list/pub/all/vehicles` | 120 |

Nie sprawdzałem limitu na `bid-details` — **do zmierzenia w 0b**.

**robots.txt.** `Allow` domyślnie, ale `Disallow:/pl/bidder-panel/*`
(cały panel zalogowanego, w tym logowanie) oraz
`Disallow:/pl/bidder-panel/auctions/details/*/all_offers` (pełna historia
ofert) i `Disallow:/pl/auctions/calc-commission/*`. Patrz §7.

**Werdykt: `httpx`, bez przeglądarki i bez logowania do odczytu.**
Adapter implementuje samo `AuctionSource`. Sesja zalogowana jest opcjonalnym
rozszerzeniem — daje `topoffers` przez API i pełną historię ofert — ale
nie jest warunkiem działania.

### 4.3 aukcje.leasygroup.pl — najsłabszy kandydat

**URL-e.** Lista: `/aukcje/pojazdy-samochodowe-i-motocykle/widok-siatka/strona-N`
(adres z §3 SPEC potwierdzony, HTTP 200). Szczegóły:
`/aukcja/<id-liczbowy>/<slug>/`. `external_id` — liczba (`28143`).

**Zawartość.** Kategoria „pojazdy samochodowe i motocykle" miesza samochody
osobowe z ciężarówkami i betoniarkami. Pobrana pozycja 28143
(Mercedes GLS 450 d) ma VIN (`W1NFF3DE0RB112081`), rok, nr rejestracyjny,
paliwo, wyposażenie i **„Cena: 323 000 PLN netto"** — ale **nie ma ani
`Ofert`, ani daty zakończenia, ani licznika, ani śladu licytacji**.
Wygląda na ofertę w cenie stałej, nie aukcję. Dodatkowo: „Prowizja za udział
w aukcji 3%", „Aukcja skierowana do podmiotów gospodarczych".

**Nie mam w próbce ani jednej realnej aukcji z tego serwisu**, więc nie
umiem opisać jego pól licytacyjnych. To luka do domknięcia.

**Dogrywka (punkt a).** Regulamin § 2 ust. 3:

> „W przypadku aukcji prowadzonych w trybie licytacji, złożenie przez
> oferenta oferty na nie więcej niż 2 minuty przed planowanym terminem
> zakończenia aukcji, automatycznie przedłuża czas jej trwania o kolejne
> 2 minuty."

Sformułowanie „w przypadku aukcji prowadzonych w trybie licytacji"
potwierdza, że serwis ma **dwa tryby** — licytację i sprzedaż w cenie stałej.
Adapter musi je rozróżniać.

**Logowanie.** `/zaloguj-sie/` (przekierowanie z `/logowanie/`). Formularz ma
pole `mail`, checkbox `rules` i **ukryte pole o losowej nazwie**:
`name="Tzd5UWsvaU9kQlMzbWhuNzIwMk9TQT09"` z wartością
`"Kzh0VWpqS0ltaU9hVlVxR0prSlpCUT09"` — obie w base64. Nazwa pola rotuje,
więc adapter musi wyciągać **parę nazwa+wartość** ze strony, nie samą wartość.
Captcha: nie znaleziono markerów (`recaptcha`/`hcaptcha`/`turnstile`).

**Ochrona przed botami.** Ciasteczko `TS014acf5b` = F5 BIG-IP ASM.
Nie napotkałem blokady przy ~8 żądaniach, ale to WAF i przy regularnym
odpytywaniu może zareagować. Ryzyko do monitorowania.

**robots.txt — problem.** Blokuje `widok-lista/*` i `widok-siatka/*` dla
wszystkich kategorii, z jawnym `Allow` **wyłącznie dla `strona-1`**.
Przy ścisłym przestrzeganiu robots widzimy jedną stronę wyników na kategorię.
Patrz §7.

**Werdykt: `httpx` wystarczy**, ale wartość źródła jest wątpliwa dopóki
nie zobaczymy realnej aukcji i nie rozstrzygniemy kwestii paginacji.

### 4.4 autoprzetarg.pl — nie pobierano

Zgodnie z Twoją decyzją nie pobierałem z tego serwisu **żadnych** stron.
Fixtures dostarczasz sam do `fixtures/autoprzetarg/` (katalog utworzony,
pusty).

Co wiadomo z samego `robots.txt` (pobranego przed decyzją) i z nagłówków
strony głównej:

- ASP.NET MVC + jQuery, `__RequestVerificationToken` w ciasteczku,
  za Cloudflare (`cf-ray`, `cf-cache-status: DYNAMIC`).
- Kategorie: `/kategoria/Pojazdy1`. Logowanie: `/uzytkownik/logowanie`,
  rejestracja `/uzytkownik/rejestracja`. Regulamin: `/regulamin`.
- `robots.txt`: `User-agent: *` → `Allow: /` z `Content-Signal:
  search=yes,ai-train=no,use=reference`. Osobne `Disallow: /` dla
  ClaudeBot, GPTBot, CCBot, Google-Extended, Applebot-Extended, Amazonbot,
  Bytespider, meta-externalagent.

**Nie zmierzone dla tego serwisu**: wszystko z checklisty (a)–(h).
Punktu (b) — okna widoczności ceny po wygaśnięciu — **nie zmierzę moimi
środkami**, bo wymaga odpytywania strony w sekundowych odstępach. Albo
zmierzysz go sam, albo zostanie niewiadomą.

---

## 5. Które serwisy obsłuży sam `httpx` (§4 pkt 3)

**Wszystkie trzy zbadane. Playwright nie jest potrzebny.**

- **EFL** — statyczny HTML, komplet danych. Bez zastrzeżeń.
- **poleasingowe.pl** — mimo Alpine.js komplet danych jest serwerowo
  w HTML (obiekt `auction: {...}`), więc wystarczy `httpx` + parser.
  Endpoint `POST /pl/auctions/bid-details/<id>` (ciasteczko `XSRF-TOKEN`
  przepisane do nagłówka `X-XSRF-Token`) jest opcją na odświeżanie na żywo
  i `topoffers`, nie warunkiem odczytu.
- **leasygroup** — statyczny HTML.

Warunek z §5 SPEC („jeśli okaże się wymagany przez wszystkie serwisy —
zatrzymaj się i zgłoś") **nie zachodzi**.

---

## 6. Rekomendacja floora per źródło (§11.2)

| Serwis | Dowód na limit tempa | Rekomendowany floor |
|---|---|---|
| poleasingowe.pl | `x-ratelimit-limit: 60–120` per trasa | patrz niżej |
| aukcje.efl.com.pl | brak nagłówków | 60 s, ale **endgame zbędny** (brak dogrywki) |
| aukcje.leasygroup.pl | brak nagłówków | 60 s — okno dogrywki 2 min, dwie próbki mieszczą się z zapasem |

**poleasingowe.pl wymaga decyzji, nie rekomendacji.** Jako jedyny ma
udokumentowany limit, więc formalnie spełnia warunek z §11.2 na tryb 30 s.
Ale jego okno dogrywki to **30 s**, więc nawet 30 s daje jedną próbkę na
okno — tryb 30 s **nie osiąga celu, dla którego został wprowadzony**.
Istotna zmiana bilansu: skoro odczyt **nie wymaga logowania** (§4.2), ruch
jest anonimowy, a nie imienny. Ryzyko z §10 — blokada konta za zbyt
agresywną pętlę — **dla samego odpytywania nie występuje**. Zostaje ryzyko
limitu HTTP, który serwis sam deklaruje w nagłówku i który sam przekracza:
jego frontend odpytuje `bid-details` **co 1000 ms**.

Realne opcje:

1. Zejść do ~10–15 s w ostatnich minutach dla aukcji obserwowanych.
   Przy limicie 120/min i `concurrency = 1` (§13) mieści się z dużym
   zapasem nawet przy kilkunastu aukcjach. Łamie literę §11.2, więc wymaga
   zmiany zapisu, ale nie naraża konta.
2. Zaakceptować, że przy tym serwisie łapiemy `LAST_SEEN` zamiast
   `CONFIRMED` przy szybkiej końcówce, i uczciwie raportować to przez
   `last_price_lead_seconds`.

Rekomenduję **opcję 1 ograniczoną do aukcji obserwowanych**.

Zastrzeżenie: jeśli potwierdzi się, że poleasingowe zamyka aukcje partiami
o tej samej sekundzie (§4.2), to przy oknie 30 s i wielu obserwowanych
pozycjach jeden bucket nie obsłuży wszystkich na czas — §11.6 przestaje być
przypadkiem brzegowym i staje się normą. To argument, żeby **liczbę
obserwowanych na tym serwisie ograniczać świadomie**, a nie tylko skracać
interwał.

---

## 7. Otwarte pytania

1. **`Disallow` na panelu zalogowanym poleasingowe.pl.** Pełna historia
   ofert (`.../all_offers`) leży pod `/pl/bidder-panel/*`, objętym
   `Disallow`. Odczyt podstawowy działa bez tego, więc pytanie jest węższe:
   czy sięgamy po pełną historię ofert (lepsza jakość §11.8), czy zostajemy
   na `lastOffers` + różnicach `offers_count`?
2. **Paginacja leasygroup.** `robots.txt` dopuszcza tylko `strona-1`.
   Trzymamy się tego (jedna strona na kategorię), czy uznajemy źródło za
   niewarte zachodu i wypada z §3?
3. **Tryb 30 s w §11.2** — patrz §6. Wymaga albo zmiany progu, albo
   przyjęcia gorszej jakości pomiaru.
4. **Długość drabinki w §11.5** — 79 s nie pokrywa 30-minutowego sufitu
   przedłużeń poleasingowe.pl. Proponuję parametryzację per źródło (§3.2).
5. **WAF F5 na leasygroup** — czy ryzyko blokady jest akceptowalne.

---

## 8. Czego brakuje — ETAP 0b

Zaplanowane, niewykonane, wymaga aukcji kończącej się w trakcie obserwacji:

- **(b) okno widoczności ceny po wygaśnięciu** — pomiar po 2 s, 5 s, 15 s,
  60 s i 5 min od `ends_at`. Nie zmierzone dla żadnego serwisu. To jest
  wejście do drabinki z §11.5 i bez tego drabinka pozostaje zgadywaniem.
- **(g) zrzut aukcji zakończonej** — brak dla wszystkich serwisów. Bez tego
  nie da się napisać wykrywania stanu końcowego.
- **(c) domknięcie** — panel ofert EFL potwierdzony jako inline, ale tylko
  w stanie pustym; potrzebna aukcja z `Ofert > 0` oraz sprawdzenie, czy
  panel przeżywa zamknięcie aukcji.
- **(e) AJAX w końcówce** — dla poleasingowe.pl endpoint znany
  (`bid-details`, 1000 ms); dla EFL i leasygroup nie sprawdzone w końcówce.
- **(f) czas życia sesji** — wymaga zalogowania, czyli Twojej obecności.
- **(h) liczba aktywnych ofert** — policzona zgrubnie tylko dla EFL
  (34 strony × 10 pozycji w kategorii Carefleet/Osobowe). Dla pozostałych
  nie policzone.
- **leasygroup**: znaleźć realną aukcję w trybie licytacji.
- **autoprzetarg.pl**: całość, po dostarczeniu fixtures przez Ciebie.
