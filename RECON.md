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
| Lista bez logowania | **tak, z ceną i liczbą ofert** | **tak, z ceną** | tak | **tak, z ceną i VIN** |
| Szczegóły bez logowania | **tak, pełne** (blok Alpine) | **tak, pełne** | tak | **tak, ale bez ofert** |
| Cena aktualna | **tak** (netto, brutto, EUR) | **tak** | **tak, obok wywoławczej** | **tak** |
| Liczba ofert | **tak** (`offers_count`, `bidders_count`) | **tak (`Ofert: N`)** | nie znaleziono | **NIE — dopiero po zalogowaniu** |
| Historia ofert | **pełna w HTML, bez logowania** | **inline w HTML, jawna** | **tabela inline, bez logowania** | **SignalR `getAuctionOffers`** |
| Min. postąpienie | **`instep_price` wprost** | z regulaminu (10/100/200 zł) | nie znaleziono | **2% ostatniej oferty** |
| Render | dane serwerowe, JS tylko odświeża | **statyczny** | statyczny | dane serwerowe + push |
| API JSON | **`POST /pl/auctions/bid-details/<id>`** | brak | brak (jQuery ajax, nieustalone) | **SignalR (WebSocket)** |
| `ETag`/`Last-Modified` | **brak** | **brak** | **brak** | **brak** |
| `If-Modified-Since` → 304 | **nie, 200** | **nie, 200** | **nie, 200** | **nie, 200** |
| Limit tempa w nagłówkach | **`x-ratelimit-limit` 60–120** | brak | brak | brak |
| Paginacja | serwerowa | serwerowa `?page=N` | `strona-N`, **robots blokuje > 1** | serwerowa `?page=N` |
| **Dogrywka** | **+30 s, okno 30 s, max +30 min** | **BRAK — twardy koniec** | **+2 min, okno 2 min** | **+120 s, okno 2 min, bez sufitu** |
| Czas do końca | **absolutny `endDate` w HTML** + strefa jawnie | **absolutny timestamp w HTML** | **odliczanie D:HH:MM, rozdzielczość 1 min** | **ukryty input `auctionEndDate`** |
| Czas serwera | **`sdt.date` w API, bez logowania** | brak | brak | brak |
| VIN publiczny | **tak** | **tak** | **tak** | **tak, już na liście** |
| Werdykt | **`httpx`, bez logowania do odczytu** | **`httpx`, bez przeglądarki** | `httpx`, ale mało danych | **`httpx`; oferty wymagają sesji lub SignalR** |
| Stan po zakończeniu | nie zmierzone | nie zmierzone | nie zmierzone | **302 na `/` — aukcja znika** |

### 2.1 Wolumeny i koszt przemiatu listy (punkt h)

Liczebności wyprowadzone z zasięgu paginacji, nie z licznika — żaden serwis
nie podaje sumy wyników w tekście.

| Serwis | Pozycji | Stron × na stronę | Koszt pełnego przemiatu |
|---|---|---|---|
| poleasingowe.pl (`vehicles`) | **~721** | 73 × 10 (ostatnia: 1) | **73 żądania** |
| aukcje.efl.com.pl (Carefleet/Osobowe) | **~280** | 35 × 8 | **5 żądań** przy `perPage=64` |
| aukcje.leasygroup.pl (pojazdy) | **88, w tym 35 licytacji** | 8 × 12 (ostatnia: 4) | 8 żądań |
| autoprzetarg.pl (`Pojazdy1`) | **244** (licznik na stronie) | ~21 × 12 | ~21 żądań |

Metoda: dla poleasingowe wyszukiwanie binarne ostatniej niepustej strony
(`?page=N`) — 73 zwraca 1 pozycję, 74 zwraca 0. Paginator jest okienkowy
(bieżąca ±5) i **nie zdradza sumy**, więc liczbę stron trzeba wykryć sondą.
Dla EFL najwyższy numer strony (`page=34`) jest w paginatorze wprost.
Dla leasygroup policzone po decyzji z §3.3, przez przejście wszystkich
ośmiu stron widoku listy: **88 pozycji, z czego 35 to licytacje**, reszta
to sprzedaż w cenie stałej. Rozkład jest nierówny — na stronie 1 była
jedna licytacja, na stronie 7 osiem.

Wniosek dla §11.2: przemiat poleasingowe kosztuje 73 żądania, ale przy jego
limicie 120/min mieści się w minucie i przy interwale „raz na kilka godzin"
jest pomijalny. **EFL jest tańszy o rząd wielkości** dzięki `perPage=64` —
pięć żądań na całą kategorię.

**VIN (druga część punktu h): publikowany przez wszystkie trzy serwisy**
na stronie szczegółów, bez logowania — EFL `WAUZZZGY2PA052888`,
poleasingowe `TMBJH7NP0P7055920`, leasygroup `W1NFF3DE0RB112081`.
Deduplikacja międzyserwisowa z §8.4 ma więc na czym się oprzeć.

---

## 3. Rozbieżności ze SPEC.md — do decyzji

### 3.1 Krok 1 „taniego odpytu" (§11.3) jest martwy

Żadna dynamiczna strona w **żadnym z czterech** serwisów nie zwraca `ETag`
ani `Last-Modified`. `If-Modified-Since` z datą z przeszłości dostaje
**HTTP 200**, nie 304, we wszystkich czterech. ETagi widoczne w nagłówkach
dotyczą wyłącznie plików statycznych (`robots.txt`). Wynik jest jednomyślny,
więc to nie przypadek doboru próbki.

Konsekwencja: cała oszczędność z §11.3 spada na krok 2 — `content_hash`.
Krok 1 należy zostawić w kodzie jako tani no-op (koszt zerowy, gdyby serwis
kiedyś zaczął go honorować), ale **nie wolno na nim opierać budżetu**.

Dowód: `fixtures/*/meta.json`, pola `headers`.

### 3.2 §11.5 i §11.2 zakładają dogrywkę ~60 s. Żaden serwis jej nie ma.

Cztery serwisy, cztery różne reguły — i **żadna nie jest 60-sekundowa**:

| Serwis | Okno wyzwalające | Przedłużenie | Limit |
|---|---|---|---|
| poleasingowe.pl | **30 s** | **+30 s** | **max +30 min** |
| aukcje.leasygroup.pl | **2 min** | **+2 min** | brak limitu w regulaminie |
| **autoprzetarg.pl** | **2 min** | **+120 s** | **brak sufitu w regulaminie** |
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

### 3.3 `robots.txt` — decyzja: `Disallow` nie ogranicza nas

**Rozstrzygnięte 2026-09-06.** Właściciel repo zdecydował: **ignorujemy
wszystkie reguły `Disallow`.** Decyzja jest trwała i dotyczy wszystkich
czterech źródeł. Podniosłem tę kwestię dwa razy i dwa razy została
rozstrzygnięta w ten sam sposób.

Treść `robots.txt` pozostaje **opisana** w sekcjach per serwis, bo §4 pkt 2
SPEC.md wymaga jej jako faktu o serwisie — ale nie jest już ograniczeniem
projektowym. Odblokowuje to:

| Serwis | Co było zablokowane | Co daje |
|---|---|---|
| poleasingowe.pl | `/pl/bidder-panel/*`, w tym `.../all_offers` | pełna historia ofert, formularz logowania |
| aukcje.leasygroup.pl | `widok-lista/*`, strony poza `strona-1` | jawna etykieta `Typ aukcji:`, procent prowizji, wszystkie ~96 pozycji zamiast 12 |
| autoprzetarg.pl | `Disallow: /` dla ClaudeBot, GPTBot i in. | — (reguła dla `*` i tak dopuszczała) |

**Co się przez to NIE zmienia.** Ograniczenia, które zostają w mocy, bo nie
wynikają z `robots.txt`, tylko z uprzejmości wobec serwisu i z §10 SPEC:

- limity tempa z nagłówków (`x-ratelimit-limit`, §11.2) — nadal obowiązują;
- `concurrency = 1` na serwis (§13) — nadal;
- twardy limit 3 nieudanych logowań i `AUTH_LOCKED` (§10.2) — nadal, bo
  chroni konto, nie serwis;
- redakcja danych osobowych osób trzecich w logach i zrzutach (§10.2) —
  nadal, i po ustaleniach z §4.2 i §4.4 obejmuje więcej pól, niż zakładał
  spec: `winner` w poleasingowe (pełny login) i „niepełne Loginy
  Uczestników" w autoprzetarg;
- aplikacja pozostaje **wyłącznie do odczytu** — nie licytuje, nie składa
  ofert, nie wywołuje metod mutujących SignalR.

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

**Sortowanie serwerowe** (`select#sort`) — bezpośrednio użyteczne
dla dispatchera i dla rekonesansu: `Title-asc|desc`,
`CurrentPrice-asc|desc`, **`EndDate-asc|desc`**, **`BidsCount-asc|desc`**.
Rozmiar strony (`perPage`): 4 / 8 / 16 / 32 / **64** — jeden przemiat listy
przy 64 pozycjach na stronę to kilkanaście żądań na całą kategorię.

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

**Historia ofert (punkt c) — POTWIERDZONA.** Panel
`<div class="hidden" data-tabs="bidders">` jest **inline w HTML**, przełączany
CSS-em, bez AJAX-a, i **nie wymaga logowania**. Przy `Ofert: 0` zawiera
„Brak ofert kupna."; przy aukcji z ofertami — pełną tabelę
(`fixtures/efl/szczegoly-435508.html`, `Ofert: 1`):

```html
<table class="data-table">
  <thead><tr><th>Kod oferty</th><th>Oferta</th><th>Data</th></tr></thead>
  <tbody><tr>
    <td>106125</td>
    <td class="Currency">48 600,00 zł</td>
    <td class="Date">2026.09.04 13:00:52.2194</td>
  </tr></tbody>
</table>
```

Znacznik czasu ma rozdzielczość poniżej milisekundy, kwota jest sformatowana,
a licytujący zanonimizowany do „kodu oferty" — **brak danych osobowych**.
Zgodne z regulaminem §4 ust. 4: „Wszystkie oferowane przez Uczestników ceny
są jawne oraz zostają uwidocznione w Serwisie Aukcyjnym EFL w trakcie
trwania Aukcji."

**Konsekwencja dla §11.8: dla EFL problem kompletności historii ofert
nie istnieje.** Każdy odpyt strony szczegółów zwraca pełną listę ofert
z dokładnymi czasami, więc `bid_gap` jest tu zawsze 0 z definicji i nie ma
potrzeby zgadywania z różnic snapshotów. Pozostaje otwarte, czy panel
przeżywa zamknięcie aukcji — punkt (c) drugiej części, do 0b.

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

**Sitemap — sprawdzony i odrzucony.** `sitemap.xml` ma **64 MB**
i **435 841 wpisów `<loc>`** (435 837 z `lastmod`): to pełna historia serwisu
od 2015 r., nie lista aktywnych aukcji. Rozkład lat jest równomierny
(ok. 40–60 tys. wpisów rocznie), a najnowsze `lastmod` są bieżące, więc plik
jest żywy — tylko bezużyteczny dla nas.

Pobranie 64 MB i sparsowanie prawie pół miliona wpisów, żeby znaleźć kilkaset
aktywnych aukcji, jest **o cztery rzędy wielkości droższe** niż pięć żądań
z `perPage=64` (§2.1) i wprost łamie budżet z §1.1 na maszynie z 6 GB RAM
dzielonej z TeslaMate. **Nie używać.** Zapisane tutaj, żeby nie wracać do tego
pomysłu jako „optymalizacji".

Uboczna obserwacja: identyfikatory aukcji są globalne i w przybliżeniu
sekwencyjne w całym serwisie (najwyższe w sitemapie: 435846, przy bieżących
aukcjach w okolicy 435500). Sitemap pozostaje jedynym znanym źródłem URL-i
aukcji **zakończonych** — gdyby kiedyś powstał pomysł backfillu historii,
to jest to miejsce, ale jako jednorazowa operacja offline, nie element
przebiegu.

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
**absolutną datę końca z jawną strefą `Europe/Warsaw`** oraz `lastOffers`.
VIN jest w HTML (`TMBJH7NP0P7055920`).

**`lastOffers` jest wypełniona bez logowania — potwierdzone.** Na aukcji
`9ooxn4x9` (`offers_count: 2`, `fixtures/poleasingowe/szczegoly-9ooxn4x9.html`):

```json
[{"id":896162,"bd_name":"d...6","op":"32 100 PLN",
  "dt":"niedziela 6 wrzesień 2026 11:56:26"},
 {"id":896147,"bd_name":"i...k","op":"32 000 PLN",
  "dt":"sobota 5 wrzesień 2026 14:38:25"}]
```

Liczba wpisów zgadza się z `offers_count`, każdy ma identyfikator, kwotę
i znacznik czasu co do sekundy. **Dla §11.8 oznacza to, że historia ofert
jest dostępna anonimowo** — dokładnie tak jak w EFL, tylko w innym opakowaniu.

Otwarte zostaje jedno: **czy `lastOffers` ucina się przy większej liczbie
ofert** (nazwa sugeruje „ostatnie N"). W dniu rekonesansu żadna aukcja
w kategorii nie miała więcej niż 2 ofert — cała partia kończy się dopiero
2026-09-07 o 12:00, a licytacja skupia się w końcówce. **Pomiar 0b odpowie
na to sam**: w końcówce wystarczy porównać długość `lastOffers`
z `offers_count`.

**Uwaga na dane osobowe (§10.2).** Obiekt zawiera `winner: 'dreamcars26'`
— **pełny, niezamaskowany login zwycięzcy** — obok zamaskowanego
`winner_formated: 'd...6'`. W `lastOffers` loginy są maskowane (`d...6`),
ale `winner` nie. To login osoby trzeciej i **nie może trafiać do `raw_json`
ani do zrzutów debug**; filtr redakcyjny z §10.2 musi go obejmować.

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

### 4.3 aukcje.leasygroup.pl

**Druga korekta tej sekcji.** Pierwsza wersja twierdziła, że pozycje nie mają
daty zakończenia; druga — że nie umiem odróżnić licytacji od ceny stałej.
**Oba twierdzenia były błędne.** Serwis oznacza typ oferty wyraźnie, tylko
szukałem nie tych fraz, a wyświetlając wyłącznie pierwsze trafienie grepa
przeoczyłem właściwe. Poniżej stan ustalony na fixtures i na DOM-ie strony.

**Dwa rodzaje pozycji, rozróżnialne w surowym HTML.** Kafelek listy to
`<div class="single_offer" data-id="<numer>">`. Licytacja ma w środku:

```html
<div class="time_label f_14 center">Do końca
  <br><span class="semibold to_end">3 : 15 : 48</span></div>
```

Oferta „Kup teraz" tego elementu **nie ma** (na stronie szczegółów ma pusty
`time_label_x` z `&nbsp;`). To jest marker typu i jest **renderowany
serwerowo** — `httpx` wystarczy. Drugi, niezależny sygnał: licytacje podają
cenę **brutto**, oferty „Kup teraz" **netto**.

**Jawna etykieta typu istnieje, ale w widoku listy.** Właściciel repo wskazał
element `Typ aukcji: <span class="bl_text_color">Licytacja</span>` — jest on
w **`widok-lista`**, w wierszu `a.single_row_offer` (obok niego stoi też
„Prowizja za udział w aukcji: 7%", więc **prowizja jest zmienna per aukcja**,
nie stała 3%). Widok listy jest objęty `Disallow` w `robots.txt`, więc
w zbieranych fixtures go nie ma.

Po decyzji z §3.3 **używamy widoku listy** — jawna etykieta jest
odporniejsza na zmiany szablonu niż wnioskowanie z obecności `time_label`,
i dokłada procent prowizji. Marker z widoku siatki zostaje jako zapasowy,
gdyby szablon listy się zmienił.

**Fixtures z widoku listy nie ma** — zbierałem je, zanim ta decyzja zapadła.
Do uzupełnienia przy budowie adaptera.

**W całej kategorii jest 88 pozycji, z czego 35 to licytacje** (policzone
po §3.3, przejściem ośmiu stron widoku listy). Rozkład jest jednak bardzo
nierówny: na **stronie 1 licytacja była jedna na dwanaście**, na stronie 7 —
osiem na dwanaście.

To wyjaśnia obie moje wcześniejsze pomyłki. Pobrałem wyłącznie stronę 1
siatki, bo tylko ona była dozwolona, i trafiłem w najgorszą możliwą próbkę:
dwie pozycje z większości „Kup teraz". Gdybym trzymał się `robots.txt`,
**widziałbym 1 z 35 licytacji tego serwisu** i wyciągnął wniosek, że źródło
jest bezwartościowe.

**Dwa identyfikatory — rozwiązane.** `data-id` na kafelku listy (`326196`)
to ten sam numer, który strona szczegółów pokazuje jako **„Numer aukcji"**,
i ten sam, którego używa link obserwowania `/aukcje/obserwuj/326196/`.
Identyfikator w URL-u aukcji (`28163`) jest inny. Do zbudowania adresu
potrzebny jest ten z URL-a; `data-id` warto zapisać jako drugi klucz, bo to
on identyfikuje aukcję w funkcjach serwisu.

**Strona licytacji — pełna** (`fixtures/leasygroup/szczegoly-28163-licytacja.html`,
Honda NSX). Bez logowania widać:

- **„Cena aktualna: 878 900 PLN brutto"** oraz osobno
  **„Cena wywoławcza: 878 900 PLN brutto"** — jedyne źródło podające obie
  wprost;
- **„Najwyższa oferta"**;
- **„Historia licytacji"** — tabela `table.offers-history` z kolumnami
  **Użytkownik / Cena / Data**, inline w HTML, **bez logowania**. W próbce
  pusta, bo licytacja jeszcze się nie zaczęła (cena aktualna = wywoławcza,
  najwyższa oferta „-"). Struktura jest ta sama co w EFL;
- interfejs licytacji („Twoja oferta", „Licytuj") — samo złożenie oferty
  wymaga konta, ale pola są w HTML;
- „Prowizja za udział w aukcji 3%", VIN, nr rejestracyjny, rocznik, paliwo,
  skrzynia, wyposażenie, opinia rzeczoznawcy.

**To unieważnia moje wcześniejsze twierdzenie, że §11.8 nie działa dla tego
źródła bez sesji.** Tabela historii licytacji jest dostępna anonimowo.
Pozostaje potwierdzić jej kształt na aukcji z realnymi ofertami — pomiar
0b na tej aukcji jest w toku.

**Czas do końca (punkt d) — najgorszy przypadek z czterech.** Strona
licytacji **nie podaje absolutnego czasu zakończenia w żadnej postaci**:
ani tekstem, ani w `data-*`, ani w JSON. Jest wyłącznie odliczanie względne
`span.to_end`, renderowane serwerowo. `ends_at` trzeba więc **wyliczać** jako
`teraz + odliczanie`, co jest dokładnie tym przypadkiem, przed którym
ostrzega §11.7: przy dryfie zegara VM-ki błąd wchodzi wprost do wyznaczonego
terminu końca.

**Format odliczania to `DNI : GODZINY : MINUTY`, nie `H:MM:SS`.** Serwis nie
podaje jednostek nigdzie w HTML, więc pierwsza wersja tej sekcji czytała
`3 : 15 : 48` jako trzy godziny. Ustalone pomiarem: między 2026-09-06 20:13
a 2026-09-07 02:03 upłynęło **5 h 50 min**, a licznik spadł z `3 : 15 : 46`
na `3 : 09 : 56`, czyli dokładnie o 5 h 50 min. Odczyt jako `H:MM:SS` dawałby
spadek o 5 min 50 s — sto razy za mało.

Praktyczna konsekwencja: rozdzielczość odliczania to **jedna minuta**. Przy
dwuminutowym oknie dogrywki tego serwisu oznacza to, że z samego licznika
**nie da się rozstrzygnąć, czy do końca zostało 0 czy 59 sekund**. Dla
poprawnego domknięcia (§11.5) trzeba albo próbkować gęściej niż wynika
z odczytu, albo oprzeć się na tym, że aukcje kończą się o pełnych godzinach —
w próbce koniec wypadł na 2026-09-10 11:59:42, czyli praktycznie równo
w południe, tak samo jak partie poleasingowe.

Paradoksalnie oferty „Kup teraz" mają absolutne „Koniec aukcji:
2026-09-07 12:00" — czyli serwis podaje absolutny czas tam, gdzie jest
najmniej potrzebny.

**Pola na liście**: tytuł (**ucięty wielokropkiem**), rocznik, paliwo,
przebieg, skrzynia, kolor, cena, oraz `time_label` przy licytacjach.
Daty zakończenia na liście nie ma w żadnej postaci.

**Dogrywka (punkt a).** Regulamin § 2 ust. 3:

> „W przypadku aukcji prowadzonych w trybie licytacji, złożenie przez
> oferenta oferty na nie więcej niż 2 minuty przed planowanym terminem
> zakończenia aukcji, automatycznie przedłuża czas jej trwania o kolejne
> 2 minuty."

**Filtr typu oferty.** Lista ma `auctionType[]` (`1` = Licytacja,
`2` = Kup teraz), ale żądanie `…/strona-1?auctionType%5B%5D=1` zwraca te same
pozycje — filtr działa po stronie JS. **Nie szkodzi**, bo typ da się
rozpoznać z obecności `time_label` w każdym kafelku.

**Logowanie.** `/zaloguj-sie/` (przekierowanie z `/logowanie/`). Pole `mail`,
checkbox `rules` i **ukryte pole o losowej nazwie**
(`Tzd5UWsvaU9kQlMzbWhuNzIwMk9TQT09` = `Kzh0VWpqS0ltaU9hVlVxR0prSlpCUT09`,
obie w base64) — nazwa rotuje, więc adapter musi wyciągać **parę
nazwa+wartość**. Brak markerów captchy.

**Ochrona przed botami.** Ciasteczko `TS014acf5b` = F5 BIG-IP ASM. Decyzja:
akceptujemy z ostrożnością — floor wyższy niż z reguły §11.2 i szybszy
circuit breaker niż przy pozostałych źródłach.

**robots.txt — pozostaje głównym ograniczeniem.** `Allow` wyłącznie dla
`widok-siatka/strona-1`; `widok-lista/*` i dalsze strony siatki objęte
`Disallow`. Przy ścisłym przestrzeganiu widzimy 12 z ~96 pozycji kategorii,
a skoro licytacje to mniejszość, realny zasięg jest jeszcze mniejszy.

**Werdykt: `httpx` wystarcza, bez logowania, z pełną historią licytacji.**
Źródło jest wyraźnie lepsze, niż wynikało z dwóch poprzednich wersji tej
sekcji: 35 realnych licytacji samochodowych, jawny typ oferty, obie ceny,
tabela historii licytacji i procent prowizji per aukcja.

Zostaje **jeden** realny minus: brak absolutnego czasu zakończenia —
`ends_at` trzeba wyliczać z odliczania, z ryzykiem dryfu (§11.7). Minus
w postaci odciętej paginacji zniknął wraz z decyzją z §3.3.

### 4.4 autoprzetarg.pl

Zbadany normalnie, tak jak pozostałe (decyzja z 2026-09-06). Uwaga
o `robots.txt` — patrz koniec sekcji.

**URL-e.** Lista: `/kategoria/Pojazdy1`, paginacja `?page=N` (od 1),
12 pozycji na stronę. Szczegóły: `/aukcja/<TYTUL>,<ID>,<Kategoria>` —
**kategoria jest zaszyta w URL-u** (`Samochody-osobowe`,
`Samochody-dostawcze`, `Motocykle`, `Naczepy-i-przyczepy`), więc zawężenie
do aut osobowych nie kosztuje ani jednego dodatkowego żądania.

**`external_id`** — token alfanumeryczny w środkowym segmencie URL-a
(`bFhGo2gH3wg`, `T8MPEc0I3wg`, `AGAJpQkI3wg`). Nie liczba, nie sekwencyjny.

**Pola na liście** (`fixtures/autoprzetarg/lista-01.html`) — najbogatsza
lista z całej czwórki: rok produkcji, pojemność i moc, nr rejestracyjny,
**VIN**, przebieg, paliwo, sprzedający (np. „Poczta Polska"), lokalizacja,
„Aktualna cena aukcji", licznik „Do końca aukcji" oraz licznik wyników
(„Wyszukano 244 ofert"). **To jedyny serwis podający VIN już na liście** —
deduplikacja z §8.4 może działać bez wchodzenia w szczegóły.

**Czas do końca (punkt d) — najczystszy przypadek z czterech.** Widoczny
`<div>` jest pusty i wypełnia go licznik JS, ale obok stoi ukryte pole
z absolutnym znacznikiem, renderowane serwerowo, **per pozycja listy**:

```html
<a href="/aukcja/<slug>,<id>,<kategoria>" class="section-list-auctions-block-item">
  <input id="auctionEndDate" name="auctionEndDate" type="hidden"
         value="2026-09-07 10:10:00" />
```

Do parsowania wystarczy odczyt tego pola. Strefa nie jest podana — zakładam
Europe/Warsaw, **do potwierdzenia**.

**Uwaga na kolejność przy parowaniu.** Ukryte pole stoi **po** linku swojego
kafelka, a nie przed nim. Parowanie „pole → następny link" przypisuje datę
sąsiedniej aukcji i przesuwa cały wynik o jeden. Złapałem się na tym raz:
lista dawała 10:05, a strona szczegółów tej samej aukcji 10:10. Poprawny
wzorzec to `href="/aukcja/…"` **potem** `input#auctionEndDate`. Strona
szczegółów jest tu źródłem rozstrzygającym.

**Aukcje kończą się gęsto, co kilka minut** — w próbce z 2026-09-07 na jednej
stronie listy wypadały o 10:00, 10:00, 10:05, 10:10, 10:20, 10:20, 10:30.
To odwrotność poleasingowe i leasygroup, gdzie cała partia kończy się o tej
samej sekundzie. Dla dispatchera oznacza to strumień pojedynczych terminów
zamiast jednego szczytu — łatwiejsze do obsłużenia jednym bucketem, ale
wymaga, żeby kolejka była stale aktywna.

**Szczegóły** (`fixtures/autoprzetarg/szczegoly-bFhGo2gH3wg.html`): „Data
zakończenia: 2026-09-07 08:10:00", „Aktualna cena aukcji: 11579,31 zł", VIN,
nr rejestracyjny, lokalizacja, stawka VAT, opis, status
„OFERTA WYMAGA AKCEPTACJI" oraz **gotowy marker zalogowania** (§10.2):
„Zaloguj się, aby złożyć ofertę".

**Historia i liczba ofert — nie ma ich w HTML bez logowania.** To wyróżnia
ten serwis in minus: strona szczegółów **nie podaje nawet liczby ofert**.
Regulamin § o przebiegu aukcji mówi co innego:

> „Wszystkie ceny oferowane przez Uczestnika są jawne oraz zostają
> uwidocznione w Serwisie Auto Przetarg w czasie trwania Aukcji. Podczas
> trwania Aukcji widoczne są również niepełne Loginy Uczestników, którzy
> złożyli Ofertę zakupu podczas trwania danej Aukcji."

Czyli oferty są jawne **po zalogowaniu**. Konsekwencja dla §11.8 jest ostra:
bez sesji nie da się policzyć nawet `bid_gap`, bo nie ma `bid_count`,
z którego liczy się przyrost. **Dla tego źródła §11.8 jest bez logowania
niewykonalne.**

Uwaga na §10.2: widoczne „niepełne Loginy Uczestników" to dane
pseudonimizujące osoby trzecie. Nie powinny trafiać do `raw_json`
ani do zrzutów debug.

**SignalR — punkt (e), znalezisko z zakładki Network.** Strona aukcji ładuje
`jquery.signalR-2.4.2.min.js` i pobiera `/signalr/hubs`. Serwis **wypycha**
aktualizacje kanałem WebSocket zamiast być odpytywanym. Proxy hubów
(`fixtures/autoprzetarg/xhr-signalr-hubs.js`) deklaruje dwa huby:

| Hub | Metody |
|---|---|
| `auctionHub` | `auctionNewOffer`, `getAuctionOffers`, `getNextOffer`, `getCommisionForAuction`, `refreshUsersPage`, `revertLastOffer`, `revertLastOfferFromAdmin` |
| `chatHub` | (proxy bez metod w tym pliku) |

`getAuctionOffers` to programistyczny dostęp do historii ofert, a
`auctionNewOffer` to zdarzenie push przy każdej nowej ofercie. **Nie
sprawdzałem, czy działają bez zalogowania — i nie wywoływałem żadnej
metody**, bo w tym samym hubie siedzą `revertLastOffer`
i `revertLastOfferFromAdmin`, czyli operacje mutujące. Aplikacja jest
wyłącznie do odczytu (SPEC.md, nagłówek), więc do tego huba podchodzimy
ostrożnie albo wcale — patrz pytanie 6 w §7.

**Dogrywka (punkt a).** Regulamin, § o przebiegu Aukcji, ust. 8:

> „Aukcja kończy się z upływem terminu na jaki została ogłoszona, chyba że
> w ostatnich dwóch minutach trwania Aukcji zostanie złożona kolejna Oferta
> zakupu. Wówczas Aukcja zostaje wydłuża równo na 120 sekund do czasu kiedy
> żaden z Uczestników nie zdecyduje się na złożenie kolejnej, wyższej Oferty
> zakupu przez ostatnie 120 sekund trwania Aukcji."

Okno 2 min, przedłużenie równo 120 s, powtarzalne, **bez sufitu**
(w odróżnieniu od poleasingowe, gdzie sufit to +30 min).

**Postąpienie — jedyny serwis z regułą procentową:** „Kwota postąpienia […]
wynosi **2% wartości ostatniej złożonej Oferty zakupu**". EFL ma progi
kwotowe (10/100/200 zł), poleasingowe podaje `instep_price` wprost. Model
domenowy musi umieć oba kształty — stały krok i procent.

Jest też „automat", czyli licytacja proxy z własną ofertą maksymalną,
analogicznie do EFL.

**Logowanie.** `/uzytkownik/logowanie`, pola `Login`, `Password`,
`RememberMe` oraz ukryte `__RequestVerificationToken` (CSRF, zmienny).
**Brak markerów captchy i 2FA** — sprawdzone pod kątem reCAPTCHA, hCaptcha,
Turnstile i kodów jednorazowych; jedyne trafienie na „2fa" okazało się
fragmentem hasha, nie polem formularza. Automatyczne logowanie wygląda więc
na wykonalne, ale to potwierdzi dopiero próba.

**Nagłówki.** Brak `ETag` i `Last-Modified`, `If-Modified-Since` → 200,
`cache-control: private`, `cf-cache-status: DYNAMIC`. Żadnych nagłówków
limitu tempa. Cloudflare przed aplikacją.

**robots.txt.** `User-agent: *` → `Allow: /` z `Content-Signal:
search=yes,ai-train=no,use=reference`. Osobne `Disallow: /` dla ClaudeBot,
GPTBot, CCBot, Google-Extended, Applebot-Extended, Amazonbot, Bytespider
i meta-externalagent. Reguła dla `*` nie blokuje więc add-onu działającego
pod własnym UA, natomiast wpisy botowe są adresowane do crawlerów AI.
Zebranie tych fixtures odbyło się na wyraźne polecenie właściciela repo.

**Werdykt: `httpx` wystarczy do ceny, daty końca, VIN-u i całej reszty pól
opisowych.** Liczba i historia ofert są poza zasięgiem `httpx` — wymagają
sesji, a docelowo kanału SignalR. Adapter da się więc napisać w dwóch
poziomach: podstawowy bez sesji, wzbogacony z sesją.

**Punkt (g) — stan po zakończeniu: ZMIERZONE 2026-09-07.** Aukcja, która
się skończyła, **przestaje istnieć pod swoim adresem**:

```
GET /aukcja/<slug>,<id>,<kategoria>   ->  HTTP 302, location: /
```

Aukcja trwająca zwraca `200`. To najtańszy marker stanu końcowego z całej
czwórki — widać go po samym nagłówku, bez pobierania i parsowania treści.
Dowód: `fixtures/autoprzetarg/naglowki-zakonczona-bFhGo2gH3wg.txt` (aukcja
kończyła się 2026-09-07 08:10, sprawdzona o 09:55).

**Konsekwencja dla §11.5 jest ostra.** Po przekierowaniu **cena końcowa jest
nie do odzyskania** — strony po prostu nie ma. Dla tego źródła `CONFIRMED`
jest osiągalne **wyłącznie** wtedy, gdy trafimy w okno między `ends_at`
a pojawieniem się przekierowania. Poza tym oknem zostaje `LAST_SEEN`, czyli
dokładnie punkt 4 drabinki: „oferta zakończona bez ceny albo zniknęła".

**Punkt (b) — ZMIERZONE 2026-09-07 na aukcji kończącej się 10:10:**

| Po `ends_at` | Cena | Rozmiar strony |
|---|---|---|
| +2 s | `5975,15` | 48 500 B (aukcja) |
| +5 s | `5975,15` | 48 500 B |
| +15 s | `5975,15` | 48 500 B |
| +60 s | **brak** | 25 216 B (przekierowanie na `/`) |
| +300 s | brak | 25 217 B |

Okno jest **znacznie szersze, niż zakładała pierwsza wersja drabinki
z §11.5**. Trzy pierwsze stopnie (2 s, 5 s, 15 s) łapią cenę z zapasem, więc
**`CONFIRMED` jest dla tego źródła osiągalne bez wyścigu o sekundy** —
wbrew temu, co sugerowała obserwacja o przekierowaniu.

Dowód: `fixtures/autoprzetarg/recon-0b.jsonl` i zrzuty `domkniecie-*.html`.

**Czego ten pomiar NIE rozstrzygnął.** W obserwowanej aukcji nie padła ani
jedna oferta: cena stała na `5975,15` przez wszystkie 17 próbek, a `ends_at`
nie drgnął. Regulaminowa reguła dogrywki — „+120 s przy ofercie w ostatnich
dwóch minutach" — **pozostaje niezweryfikowana w praktyce**. Trafiłem na
aukcję bez licytacji w końcówce.

**Nie zmierzone:** punkt (f) — czas życia sesji (wymaga zalogowania).

## 5. Które serwisy obsłuży sam `httpx` (§4 pkt 3)

**Wszystkie cztery. Playwright nie jest potrzebny.**

- **EFL** — statyczny HTML, komplet danych. Bez zastrzeżeń.
- **poleasingowe.pl** — mimo Alpine.js komplet danych jest serwerowo
  w HTML (obiekt `auction: {...}`), więc wystarczy `httpx` + parser.
  Endpoint `POST /pl/auctions/bid-details/<id>` (ciasteczko `XSRF-TOKEN`
  przepisane do nagłówka `X-XSRF-Token`) jest opcją na odświeżanie na żywo
  i `topoffers`, nie warunkiem odczytu.
- **leasygroup** — statyczny HTML.
- **autoprzetarg.pl** — statyczny HTML wystarcza do ceny, daty końca i VIN-u.
  Zastrzeżenie: historia i liczba ofert idą kanałem SignalR (WebSocket), a nie
  HTTP-em — `httpx` ich nie dosięgnie. Patrz §4.4 i pytanie 6 w §7.

Warunek z §5 SPEC („jeśli okaże się wymagany przez wszystkie serwisy —
zatrzymaj się i zgłoś") **nie zachodzi**.

### 5.1 Kolejność budowy adapterów (§14 pkt 5 i 10)

Właściciel repo chce obsługi **wszystkich czterech** serwisów, z priorytetem
dla **autoprzetarg.pl i poleasingowe.pl**. Mimo to na pierwszy adapter
rekomenduję **EFL** — nie wbrew priorytetom, tylko dlatego, że §14 pkt 5 mówi
„najprostszy wg rekonesansu": rolą pierwszego adaptera jest przepchnięcie
całej pionowej ścieżki (adapter → mapper → repozytorium → dispatcher) przez
najmniej oporny materiał, a nie zebranie najcenniejszych danych.

| # | Serwis | Dlaczego tu |
|---|---|---|
| 1 | **EFL** | statyczny HTML, komplet bez logowania, jawna tabela ofert inline, absolutny czas końca, **brak dogrywki** — najmniej ruchomych części |
| 2 | **poleasingowe.pl** | priorytet; dane serwerowo, historia ofert bez logowania, czas serwera w API, udokumentowany limit tempa. Trudność: najkrótsze okno dogrywki (30 s) |
| 3 | **autoprzetarg.pl** | priorytet, ale jako jedyny wymaga klienta SignalR do liczby i historii ofert — nowa zależność spoza §5, lepiej dokładana na działającej ścieżce |
| 4 | **leasygroup** | wymaga wyliczania `ends_at` z odliczania i decyzji o zasięgu (§7 pkt 2) |

Gdyby priorytet miał przeważyć nad prostotą, zamiana miejsc 1 i 2 jest
bezpieczna — poleasingowe jest tylko nieznacznie trudniejsze od EFL.
Zamiana z pozycją 3 nie jest, bo wciąga SignalR do pierwszego adaptera.

---

## 6. Rekomendacja floora per źródło (§11.2)

| Serwis | Dowód na limit tempa | Rekomendowany floor |
|---|---|---|
| poleasingowe.pl | `x-ratelimit-limit: 60–120` per trasa | patrz niżej |
| aukcje.efl.com.pl | brak nagłówków | 60 s, ale **endgame zbędny** (brak dogrywki) |
| aukcje.leasygroup.pl | brak nagłówków | 60 s — okno dogrywki 2 min, dwie próbki mieszczą się z zapasem |
| autoprzetarg.pl | brak nagłówków | 60 s — okno 2 min, jak wyżej; **albo 0 s, jeśli użyjemy SignalR** |

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

1. **Ucinanie `lastOffers` w poleasingowe.** Wątek `Disallow` odpadł
   (§3.3), więc zostaje samo pytanie techniczne: **czy `lastOffers` ucina się
   przy dużej liczbie ofert.** Jeśli nie — sprawa zamknięta, wszystko mamy
   anonimowo. Jeśli tak — sięgamy po `.../all_offers`, co jest już decyzją
   podjętą. Rozstrzyga pomiar 0b.
2. ~~**Paginacja i widok listy w leasygroup.**~~ **Rozstrzygnięte** (§3.3).
   Używamy widoku listy — daje jawne `Typ aukcji: Licytacja` i procent
   prowizji — oraz pełnej paginacji, czyli ~96 pozycji zamiast 12. Źródło
   zostaje w §3; pytanie „czy wypada" upadło już wcześniej, po korekcie
   z §4.3.
3. **Tryb 30 s w §11.2** — patrz §6. Wymaga albo zmiany progu, albo
   przyjęcia gorszej jakości pomiaru.
4. **Długość drabinki w §11.5** — 79 s nie pokrywa 30-minutowego sufitu
   przedłużeń poleasingowe.pl. Proponuję parametryzację per źródło (§3.2).
5. **WAF F5 na leasygroup** — czy ryzyko blokady jest akceptowalne.
6. **SignalR w autoprzetarg.pl.** Kanał push dawałby historię ofert
   i natychmiastowe zdarzenia bez odpytywania — najtańsza możliwa końcówka.
   Ale: (a) SPEC.md §5 przewiduje wyłącznie `httpx`, więc doszłaby zależność
   od klienta SignalR; (b) trwałe połączenie WebSocket kłóci się z §11.1
   („dispatcher śpi do najbliższego terminu, CPU w spoczynku ~0%");
   (c) ten sam hub wystawia metody mutujące (`revertLastOffer`), więc
   podłączenie się do niego wymaga dyscypliny, żeby aplikacja pozostała
   wyłącznie do odczytu. Wchodzimy w to, czy zostajemy przy odpytywaniu
   HTML i akceptujemy brak liczby ofert bez sesji?
7. **Postąpienie procentowe.** autoprzetarg liczy 2% ostatniej oferty, EFL
   ma progi kwotowe, poleasingowe podaje `instep_price` liczbowo. Model
   domenowy musi obsłużyć oba kształty — to drobna, ale realna zmiana
   w stosunku do §8, który o postąpieniu nie wspomina wcale.

---

## 8. Czego brakuje — ETAP 0b

Zaplanowane, niewykonane, wymaga aukcji kończącej się w trakcie obserwacji:

- **(b) okno widoczności ceny po wygaśnięciu** — pomiar po 2 s, 5 s, 15 s,
  60 s i 5 min od `ends_at`. Nie zmierzone dla żadnego serwisu. To jest
  wejście do drabinki z §11.5 i bez tego drabinka pozostaje zgadywaniem.
- **(g) zrzut aukcji zakończonej** — brak dla wszystkich serwisów. Bez tego
  nie da się napisać wykrywania stanu końcowego.
- **(c) domknięcie** — panel ofert EFL potwierdzony w stanie pustym
  **i niepustym** (pełna tabela z czasami). Zostaje sprawdzić, czy panel
  przeżywa zamknięcie aukcji.
- ~~**(e) AJAX w końcówce**~~ — **zrobione dla trzech z czterech**:
  poleasingowe ma `bid-details` odpytywany co 1000 ms, autoprzetarg ma push
  przez SignalR, EFL nie ma nic (strona statyczna). Zostaje leasygroup.
- **(f) czas życia sesji** — wymaga zalogowania, czyli Twojej obecności.
- ~~**(h) liczba aktywnych ofert i VIN**~~ — **zrobione**, patrz §2.1.
  Zostaje tylko potwierdzenie liczebności leasygroup, zablokowane przez
  `robots.txt`.
- ~~**leasygroup**: znaleźć realną aukcję w trybie licytacji~~ — **zrobione**
  (28163, Honda NSX). Pomiar 0b uruchomiony 6.09 wieczorem, koniec aukcji
  23:29. Zostaje potwierdzić kształt tabeli „Historia licytacji" na aukcji
  z realnymi ofertami — w próbce była pusta.
- **autoprzetarg.pl**: punkty (b), (f), (g). Serwis nie ma widocznego
  archiwum ani filtra statusu, więc zrzut aukcji zakończonej trzeba złapać
  w locie — aukcja z próbki kończy się 2026-09-07 08:10:00, czyli **przed**
  celami zaplanowanego pomiaru 0b (EFL 10:47, poleasingowe 12:00).
  Jeśli ma być objęta, pomiar trzeba przesunąć wcześniej.
