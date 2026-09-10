# Historia zmian

## 0.27.4 — w przygotowaniu

**Szczegóły aukcji są prostsze i dokładniejsze.** Lokalizacja z EFL jest
odczytywana ze strony szczegółów i pusty odczyt nie kasuje znanej wartości.
Obserwowanie przeniesiono przy cenę; cena docelowa została usunięta, a notatka
jest krótkim rozwijanym polem. „Oferty” i „Przebieg licytacji” tworzą teraz
jedną historię z widocznym rozróżnieniem danych serwisu i odczytów aplikacji.

## 0.27.3 — w przygotowaniu

**Dopracowanie interfejsu według projektu GUI.** Panel filtrów można teraz
zwinąć, a lista wykorzystuje odzyskaną szerokość. Usunięto duży nagłówek
promocyjny i nazwę serwisu z górnego paska. Karty, zdjęcia, pola, przyciski
i etykiety mają zaokrąglenia zgodne z projektem, a uchwyty suwaków zakresu
są wyśrodkowane na torze.

## 0.27.2 — w przygotowaniu

**Nowy interfejs katalogu aukcji.** Lista otrzymała ciemny, kontrastowy układ
z bocznym panelem filtrów, wyraźniejszą hierarchią danych i siatką ofert
inspirowaną przekazanym projektem GUI. Przeprojektowane zostały także pasek
nawigacji, karta aukcji oraz widoki diagnostyczne. Wszystkie dane, filtry,
odsyłacze i doładowywanie HTMX działają jak wcześniej; wersja zmienia adres
arkusza stylów, aby Home Assistant nie zachował poprzedniego motywu w cache.

## 0.27.1 — w przygotowaniu

**Doładowanie listy nie zmienia pozycji widoku.** Skrypt odtwarzający listę
po powrocie z karty usuwa swój nasłuch HTMX po zakończeniu odtwarzania i nie
reaguje na zwykłe kliknięcia „Wczytaj kolejne 50”.

## 0.27.0 — w przygotowaniu

**Zniknięcie aukcji wymaga dwóch pełnych skanów listy.** Strona błędu, WAF,
urwana paginacja albo powtórzona strona zostawia wynik jako częściowy i może
dodać znalezione pozycje, ale nie przenosi pozostałych do archiwum. Stan
skanu i czas ostatniego pełnego przebiegu są widoczne w diagnostyce i Grafanie.

**Końcówki aukcji mają pierwszeństwo.** Dispatcher obsługuje domknięcia i
obserwowane aukcje przed przemiataną listą, wraca do nich między stronami
skanu, a backup bazy działa osobno w tle. Lista, szczegóły i galeria używają
jednego limitu oraz jednej blokady na serwis.

**Cena końcowa jest opisana dokładniej.** Dashboard rynkowy pokazuje teraz
liczbę i udział cen `CONFIRMED` oraz `LAST_SEEN`, a także opóźnienie pomiarów
drugiej grupy. Wyjątkowy odpyt w momencie domknięcia zapisuje dług w limiterze
zamiast udawać, że nie zużył żądania.

Zmiany przeszły pełny zestaw automatycznych testów na lokalnym PostgreSQL 17.
Nie stanowi to jeszcze potwierdzenia pracy na HA: przed wydaniem produkcyjnym
pozostaje siedem dni obserwacji, porównanie domknięć ze źródłami i pomiar RSS.

## 0.26.0 — w przygotowaniu

**Widać, którą zakładkę się przegląda.** Bieżąca jest wypełniona i jaśniejsza
od reszty. Podświetlenie liczy się ze znormalizowanych kryteriów, nie z napisu
w adresie, więc dołożenie marki albo zmiana sortowania go nie gasi — to wciąż
ta sama zakładka, tylko zawężona.

**„Więcej filtrów" jest zawsze zwinięte.** Wcześniej otwierało się zależnie od
zawartości, więc w jednych zakładkach pasek był rozłożony, a w innych nie
i układ strony skakał przy przechodzeniu między nimi. Żeby zwinięty filtr nie
zawężał listy po cichu, przy sekcji stoi teraz licznik działających filtrów.

## 0.25.0 — w przygotowaniu

**Aukcja trafia na listę według terminu, nie według naszej księgowości.**
„Aktywne" pokazywało pozycje godzinę po ich zakończeniu, bo status zmienia
się z opóźnieniem: aukcji nieobserwowanej nie odpytujemy pojedynczo, a zegar
zamyka ją dopiero po karencji na możliwą dogrywkę. Teraz o przynależności do
listy decyduje to, co widać samemu — czy termin już minął. „Aktywne"
i „Archiwum" są rozłączne i pokrywają całość, więc żadna aukcja nie wypada
z obu naraz.

**Poprawka: żywa aukcja nie zostaje w archiwum na zawsze.** Aukcję uznajemy
za zniknioną po dwóch przemiatach bez niej, ale przemiat potrafi urwać się
w połowie — i to dwa razy pod rząd. Trafiała wtedy do archiwum mimo że żyła,
a **nic w całym systemie tego nie cofało**: wracała na listę przy każdym
kolejnym przemiacie i nadal leżała w archiwum. Teraz wraca do aktywnych, o ile
jej termin jeszcze nie minął. Zakończonych to nie dotyczy — poleasingowe
trzyma je na liście długo po końcu i wskrzeszanie ich byłoby gorszym błędem.

Aukcje błędnie zarchiwizowane wcześniej wrócą same, przy najbliższym
przemiacie źródła.

## 0.24.0 — w przygotowaniu

**Wejście na kartę aukcji prosi o świeże dane.** Do tej pory karta pokazywała
to, co zostało z ostatniego przemiatu — dla aukcji nieobserwowanej nawet
sprzed kilku godzin. Teraz otwarcie karty planuje natychmiastowy odpyt, budzi
pętlę zbierania i podmienia kartę w miejscu, gdy świeży odczyt dojdzie.
Bez przeładowania strony, więc galeria i pozycja przewinięcia zostają.

Karta **nie wysyła niczego sama** i nie omija limitów tempa: prosi tylko
o wcześniejszy obrót pętli, a o tym, czy żądanie w ogóle poleci, decyduje jak
zawsze kubełek tokenów. Nie prosi też, gdy aukcja jest zakończona albo gdy
dane są świeższe niż minuta — inaczej wciśnięty F5 byłby furtką dookoła
całego harmonogramu.

Pasek „pobieram świeże dane" kończy się sam: po kilkunastu sekundach albo
z chwilą, gdy dane dojdą. Brak świeżych danych też jest odpowiedzią.

## 0.23.0 — w przygotowaniu

**Poprawka: „nowe od ostatniej wizyty" wreszcie coś pokazuje.** Znacznik
wizyty był nadpisywany przy **każdym** wyświetleniu listy — łącznie z tym,
na którym stał ten właśnie filtr. Cofał się więc o kilka sekund przed samego
siebie i widok był pusty zawsze. Teraz znacznik stoi w miejscu przez całe
przeglądanie i przesuwa się dopiero przy nowej wizycie, czyli po pół godzinie
bez ruchu. „Nowe" znaczy więc „od końca poprzedniej wizyty".

**Przełącznik aktywne / wygasłe / wszystkie** w widokach zawężonych —
obserwowanych i wystawionych ponownie. Podział na trwające i wygasłe jest
tam naturalnym drugim pytaniem, a do tej pory siedział w liście rozwijanej
wśród czternastu innych filtrów. Przełączenie niesie komplet bieżących
filtrów i sortowania, więc nie gubi zawężenia, po którym się tam trafiło.

**Nowa zakładka „Wystawione ponownie".** Niesprzedany samochód wraca na
aukcję, zwykle taniej — zestawienie takich pozycji odpowiada na inne pytanie
niż „co jest na sprzedaż". Zakładka pokazuje **wyłącznie** dopasowania po
VIN-ie: karta aukcji dopuszcza też podobieństwo marki, modelu i przebiegu,
ale oznacza je jako przypuszczenie, a na liście nie ma gdzie postawić tego
zastrzeżenia. Flota kupiona hurtem to te same modele z tym samym rocznikiem.

## 0.22.0 — w przygotowaniu

**Cena wywoławcza wreszcie jest — tam, gdzie da się ją ustalić na pewno.**
Żaden serwis jej nie podaje, ale aukcja bez ani jednej oferty stoi właśnie
na cenie wywoławczej: licytować można wyłącznie w górę. Gdy widzimy aukcję
z zerem ofert, zapisujemy tę cenę i **już jej nie nadpisujemy** — przesłanka
znika wraz z pierwszą ofertą i drugiej okazji nie ma.

Migracja uzupełnia też aukcje już zebrane, które nadal mają zero ofert.

Tam, gdzie pewności nie ma, pole zostaje puste: serwis, który nie podaje
liczby ofert (autoprzetarg bez logowania), nie daje podstawy do niczego —
„nie wiem, ile było" to nie „nie było żadnej". A gdy oferty już padły, cena
bieżąca jest wyższa od wywoławczej o nieznaną wartość i każda wpisana liczba
byłaby zmyślona.

## 0.21.0 — w przygotowaniu

**Historia licytacji dla poleasingowe.pl — kolejne postąpienia z kwotami
i czasami.** Serwis podaje je wprost w kodzie strony (`lastOffers`), tą samą
odpowiedzią, którą i tak pobieramy po cenę. To jest realna chronologia: kwoty
rosną razem z czasem, każda oferta ma własny identyfikator. Karta pokazuje ją
tylko dla źródeł, w których lista faktycznie jest przebiegiem licytacji —
tabela EFL nią nie jest i tam nadal się nie pojawia.

Dwa ograniczenia, oba wypisane pod tabelą na karcie: serwis pokazuje
**dziesięć ostatnich** ofert i czyści listę kilka minut po zakończeniu, więc
wcześniejsze mamy o tyle, o ile zdążyliśmy je zobaczyć — najwięcej dla aukcji
obserwowanych. **Ceny wywoławczej poleasingowe.pl nie podaje w ogóle**,
dlatego to pole zostaje puste zamiast pokazywać zgadywaną liczbę.

**Poprawka: licznik ofert na karcie zgadza się z historią.** Przemiat listy
aktualizował liczbę ofert i cenę w miejscu, nie zostawiając po tej zmianie
żadnego śladu — karta pokazywała wtedy „3 oferty" u góry i „2" w ostatnim
wierszu historii. Obie liczby prawdziwe, tylko z różnych chwil. Przemiat
zapisuje teraz wiersz historii na tych samych zasadach co odpyt szczegółów:
wyłącznie przy zmianie.

To przy okazji jedyne źródło historii ceny dla aukcji, których nie
obserwujesz — takich nie odpytujemy pojedynczo po raz drugi, więc do tej pory
ich przebieg licytacji kończył się na pierwszym odczycie.

## 0.20.1 — w przygotowaniu

**Karta pokazuje historię ceny bieżącej i nic poza tym.** Tabela ofert
z EFL, dodana w 0.20.0, została usunięta — myliła. Wyglądała na chronologię
licytacji, a nią nie jest: cena bieżąca równa się kwocie z górnego wiersza,
a niżej stoją wiersze z późniejszą datą i niższą kwotą, mimo że poniżej
ceny bieżącej zalicytować się nie da. Tłumaczenie tego regułami licytacji
proxy było z mojej strony zgadywaniem.

Wiersze zbieramy dalej (nie kosztują żadnego dodatkowego żądania) i bez
nich nie da się tej sprzeczności rozstrzygnąć — ale na kartę nie wracają,
dopóki nie wiadomo, co dokładnie znaczą. Sprzeczność opisana w `RECON.md`
§3.5a razem z tym, jaka obserwacja ją rozstrzygnie.

**Przebiegiem licytacji jest więc historia ceny bieżącej** — jedynej
liczby, o której wiadomo, co znaczy.

## 0.20.0 — w przygotowaniu

**Karta tłumaczy licytację proxy, zamiast wyglądać na przekłamaną.** Tabela
ofert potrafiła pokazać niższą kwotę z późniejszą datą i nic nie mówiło, że
tak ma być. EFL prowadzi licytację proxy: każdy podaje swoje maksimum,
a kto zaoferuje mniej niż stojące już maksimum lidera, ten przegrywa i cena
nie drga. Najwyższa oferta jest teraz oznaczona, wcześniejszy stan tego
samego licytanta przygaszony, a reguła napisana pod tabelą — ale tylko tam,
gdzie serwis faktycznie licytuje proxy.

**Dwie sekcje przestały nazywać się myląco.** „Przebieg licytacji" to od
teraz oferty ze strony serwisu, a nasze odczyty ceny nazywają się wprost
„Nasze odczyty ceny". Wcześniej obie mówiły o cenach i nie dało się zgadnąć,
czym się różnią.

**Poprawka: aukcje EFL mogą wreszcie dostać potwierdzoną cenę końcową.**
Drabinka domknięcia kończyła się 30 sekund po terminie, a EFL dopisuje
„Zakończona" dopiero po 5-7 minutach — więc potwierdzenia nie dało się
zobaczyć nigdy i **każda** aukcja tego źródła lądowała na „ostatniej
widzianej cenie". Drabinka sięga teraz za ten moment. Na tym rozróżnieniu
stoją mediany cen końcowych, więc do tej pory EFL nie wnosił do nich nic.

**Poprawka: zegar nie ucina już drabinki domknięcia.** Karencja przed
zamknięciem aukcji z zegara nie może być krótsza niż jej ostatni stopień —
inaczej aukcja zamykała się w trakcie fazy, która miała złapać
potwierdzenie.

**Suwak rocznika nie zaczyna się już od 1, gdy pojedyncza aukcja ma błędny
rocznik.** Do wyznaczenia granic bierzemy tylko realne lata 1900–2100.
Prawidłowe starsze auta nadal poszerzają zakres poniżej 1990, ale wartość
`1` z uszkodzonych danych nie czyni suwaka bezużytecznym.

## 0.19.1 — w przygotowaniu

**Suwak rocznika zaczyna się od 1990** (było 1980). Granica jest nadal
stała, a nie brana z danych — inaczej przeskakiwałaby przy każdej nowej
aukcji i nie dałoby się jej zapamiętać. Auto starsze niż 1990 nie znika:
granica poszerza zakres, a nie ucina dane, więc suwak sięgnie tam, gdzie
trzeba.

**Poprawka: zakończone aukcje EFL znikają z „Aktywnych" po dziesięciu
minutach, nie po siedemdziesięciu.** Aukcji nieobserwowanej nie odpytujemy
po raz drugi, więc jej koniec rozpoznaje zegar — a ten czekał na możliwą
dogrywkę nawet tam, gdzie serwis dogrywki w ogóle nie ma. EFL kończy aukcje
twardo, więc czekanie godziny „na wszelki wypadek" trzymało je na samej
górze listy, która jest sortowana po najbliższym terminie. Źródła
z dogrywką (poleasingowe.pl) czekają tyle, ile trzeba — bez zmian.

## 0.19.0 — w przygotowaniu

**Prawdziwe oferty zamiast zgadywania.** Karta aukcji EFL pokazuje teraz
sekcję „Oferty": kwotę i moment złożenia każdej oferty tak, jak podaje je
serwis — zamiast wnioskowania z różnic licznika między naszymi odpytami.
Zakładka „Oferty" jest w HTML-u strony i nie wymaga logowania, więc lista
przyjeżdża **tą samą odpowiedzią**, którą i tak pobieramy po cenę: ani
jednego żądania więcej.

Przy okazji archiwum wie więcej niż serwis. EFL trzyma jeden wiersz na
uczestnika i nadpisuje go w miejscu, więc gdy ktoś podniesie swoją ofertę,
poprzednia znika ze strony bez śladu. U nas zostają obie — przebieg
licytacji odtwarza się w całości.

Licytanci są oznaczeni literami („Licytant A", „Licytant B") w kolejności
pojawienia się. Identyfikatory nadane przez serwis **nie trafiają do bazy**:
zapisujemy skrót związany z konkretną aukcją, więc widać, kto kogo przebijał
w tej licytacji, i nie da się z tego złożyć listy aukcji, w których ktoś
brał udział.

**Poprawka: „przegapione oferty" znikły tam, gdzie nic nie znaczyły.**
Licznik ofert w EFL liczy uczestników licytacji proxy, a nie postąpienia,
więc wyliczana z niego liczba przegapionych ofert była pozbawiona sensu —
i wyglądała wiarygodnie, co jest gorsze niż jej brak. Teraz pokazuje się
wyłącznie dla źródeł, o których wiadomo, że licznik liczy oferty.

## 0.18.0 — w przygotowaniu

**Dashboardy Grafany w repozytorium.** Trzy gotowe do zaimportowania:
przebieg licytacji jednej aukcji, mediany cen końcowych modelu i stan źródeł.
Linki z karty aukcji prowadzą do nich wprost — od tej wersji jest dokąd.
Instrukcja importu w `grafana/README.md`.

Dashboardy leżą w repo, a nie tylko w Grafanie, bo kształt widoków
`reporting.*` jest kontraktem: zmiana kolumny w migracji ma iść w tym samym
commicie co poprawka wykresu. Pilnuje tego test, który **wykonuje zapytania
dashboardów na prawdziwej bazie** — zapytanie z nieistniejącą kolumną nie
przejdzie.

## 0.17.0 — w przygotowaniu

**Własna kopia bazy raz na dobę.** Snapshot Home Assistanta obejmuje dodatek
PostgreSQL w całości — razem z TeslaMate — więc odtworzenie z niego samej
bazy `poleasingowe` cofnęłoby też przebiegi auta, których nie da się odtworzyć
z niczego innego. Dodatek robi więc `pg_dump` **wyłącznie swojej bazy** do
`/share/poleasingowe/backup`, trzyma siedem ostatnich kopii, a datę i rozmiar
ostatniej pokazuje w diagnostyce. Nieudana kopia nie zatrzymuje zbierania —
ląduje w logu i ponawia się następnej doby.

Hasło do bazy idzie do `pg_dump` środowiskiem procesu, nigdy w linii poleceń:
ta jest widoczna w `ps` dla wszystkiego, co biegnie w kontenerze.

## 0.16.0 — w przygotowaniu

**Eksport listy do CSV.** Przycisk „CSV ↓" obok sortowania pobiera dokładnie
to, co widać na ekranie — z tymi samymi filtrami, nie całą bazę. Plik jest
przygotowany pod polskiego Excela: średnik jako separator, przecinek
dziesiętny i BOM, bez którego „Škoda" zamienia się w krzaki. Kolejne strony
lecą strumieniowo, więc eksport nie buduje całego pliku w pamięci dodatku.

## 0.15.1 — w przygotowaniu

**Aukcja zdjęta z serwisu przestaje udawać aktywną.** Oferta wycofana przez
sprzedającego znikała z listy i nikt nam tego nie mówił — zostawała `ACTIVE`
bez końca. Teraz dostaje `DISAPPEARED`, ale dopiero po **dwóch** kolejnych
przemiatach bez niej: jeden potrafi urwać się w połowie i wtedy „brak na
liście" znaczy tylko „nie doszliśmy do tej strony". Aukcji po terminie ta
reguła nie dotyczy — tam rządzi faza domknięcia, która ma szansę złapać cenę
końcową.

## 0.15.0 — w przygotowaniu

**Ceny końcowe są wreszcie łapane (faza domknięcia z §11.5).** To była
największa luka projektu: aukcja po terminie zostawała z ostatnią widzianą
ceną i nikt nigdy nie sprawdzał, za ile faktycznie poszła.

Po terminie obserwowanej aukcji dispatcher chodzi po **drabince** — próbach
liczonych od `ends_at`, nie od siebie nawzajem, z siatką ustawianą per
źródło. Liczy się trafienie w okno, nie długość czujki: u autoprzetargu
strona znika 15–17 sekund po końcu, a EFL oznacza aukcję jako zakończoną
dopiero po 5–7 minutach. Próby domknięcia są zwolnione z czekania na token
tempa — przeczekanie tego okna w kolejce znaczyłoby, że nie ma po co było
wysyłać żądania.

Cena dostaje `CONFIRMED` **wyłącznie wtedy, gdy strona sama mówi, że aukcja
się skończyła**. Gdy drabinka się wyczerpie bez potwierdzenia, zostaje
`LAST_SEEN` razem z informacją, o ile sekund ten odczyt wyprzedził termin —
bez tej liczby cena sprzed dwóch sekund wygląda tak samo jak sprzed doby.

**EFL rozpoznaje koniec aukcji.** Nagłówek `Zakończona` to jedyny marker
stanu końcowego w tym serwisie; bez niego każda aukcja z EFL kończyłaby na
dolnym oszacowaniu.

## 0.14.0 — w przygotowaniu

**Ponowne wystawienia są powiązane.** Niesprzedany samochód wraca na aukcję,
czasem w innym serwisie. Karta pokazuje teraz pozostałe wystawienia tego
samego auta — w obie strony: z archiwum starej aukcji do nowej i z nowej do
starej, razem z ceną i przebiegiem każdego podejścia. Bez tego archiwum
kłamało przez przemilczenie: mówiło „zakończona" i nie wspominało, że ta sama
sztuka poszła miesiąc później o osiem tysięcy taniej.

Dopasowanie po **VIN-ie jest oznaczone jako pewne**, bo VIN identyfikuje
egzemplarz. Bez VIN-u wymagamy zgodności marki, modelu, rocznika, silnika,
koloru **oraz** przebiegu w wąskim oknie i oznaczamy takie trafienie jako
„podobne dane" — flota leasingowa bywa kupiona hurtem i samo „ten sam model
i rocznik" wskazywałoby na siebie nawzajem kilkanaście identycznych aut.

**Przebieg licytacji widać w panelu.** Zmiany ceny i liczby ofert zbierały
się od pierwszego dnia, ale jedyną drogą do nich była Grafana. Karta aukcji
ma teraz tabelę zmian — a że snapshot powstaje wyłącznie przy zmianie, odstęp
między wierszami mówi o licytacji, nie o naszym harmonogramie.

## 0.13.0 — w przygotowaniu

**Wycena AI zapisuje się na stałe.** Do tej pory leżała w cache'u na dysku
kluczowanym danymi wejściowymi — razem z porównaniami z zakończonych aukcji.
Każda kolejna zakończona aukcja tego modelu zmieniała te porównania, więc
klucz przestawał pasować i karta liczyła wycenę **od nowa**: inna kwota przy
każdym wejściu i kolejne płatne żądanie do dostawcy.

Wycena jest teraz wierszem w bazie, jednym na aukcję. Raz policzona zostaje,
karta pokazuje datę i model, którym ją policzono, a zmienia ją wyłącznie
przycisk **Przelicz**. Zapisana wycena wyświetla się także wtedy, gdy klucz
API został usunięty — raz policzona nie przestaje być prawdziwa.

**Przycisk „Wróć do listy".** Add-on siedzi w ramce Home Assistanta, więc
„wstecz" przeglądarki cofa cały panel, a nie zawartość ramki. Powrót wraca
w to samo miejsce: odtwarza doładowane strony listy i pozycję przewinięcia,
a nie tylko pierwsze pięćdziesiąt ofert od góry.

**Suwak rocznika zaczyna się od 1980.** Granica z danych przeskakiwała przy
każdej nowej aukcji, więc nie dało się jej zapamiętać.

**„Benzyna + gaz" to to samo co „benzyna + LPG" i samo „LPG".** Instalacja
gazowa jest zawsze dodatkiem do benzyny, a serwisy zapisują to na kilka
sposobów — filtr robił z tego trzy osobne pozycje, z których każda gubiła
część ofert. Separator (`+`, `/`, „i", „z") nie tworzy już nowego paliwa.
Migracja porządkuje wiersze już zebrane.

## 0.12.0 — w przygotowaniu

**Filtry wielokrotnego wyboru.** Marka, źródło, paliwo, skrzynia, lokalizacja
i rodzaj pojazdu przyjmują teraz kilka wartości naraz — „diesel albo benzyna"
to jedno pytanie, a nie dwa przeglądania listy. Wewnątrz wymiaru wartości
łączy OR, wymiary między sobą AND. Kontrolka to rozwijana lista checkboxów
na czystym HTML-u, bez ani jednej linii JavaScriptu.

**Suwaki rocznika i mocy silnika.** Zakres ustawia się przeciąganiem, a nie
wpisywaniem dwóch liczb. Granice suwaka biorą się z danych, nie z teorii:
suwak rocznika od 1900 do 2100 miałby cały ruch na trzech procentach
długości. Skrajne wartości odcina percentyl 1/99, żeby jedna aukcja z błędną
mocą nie spłaszczyła całej skali. Filtr mocy silnika istnieje w ogóle
pierwszy raz.

Bez JavaScriptu suwaki degradują się do dwóch zwykłych pól liczbowych i filtr
nadal działa.

**Cena docelowa znaczy to, co powinna.** Na aukcji cena tylko rośnie, więc
próg jest **limitem**, a nie ceną, do której coś ma spaść — wcześniejszy opis
w interfejsie mówił nieprawdę. Aukcja, która przebiła limit, jest teraz
oznaczona na czerwono i podpisana kwotą limitu; mieszcząca się w nim zostaje
zielona.

## 0.11.1 — w przygotowaniu

**Naprawiony formularz obserwacji.** Pola nie miały żadnych reguł układu, więc
stały sklejone bez odstępu: obwódka zaznaczonego pola „cena docelowa" wchodziła
na przycisk pod spodem, a pole na kwotę rozciągało się na całą szerokość karty.
Notatka i cena stoją teraz obok siebie, kwota ma sensowną szerokość, a zapisana
wartość wyświetla się jako `60000`, nie `60000.00`.

**Widać, po co jest cena docelowa.** Pod polem stoi zdanie: gdy cena zejdzie do
tej kwoty, aukcja podświetli się na liście.

## 0.11.0 — w przygotowaniu

**Wycena odnosi się do cen na portalach ogłoszeniowych.** Model podaje teraz
dodatkowo orientacyjny poziom cen ofertowych podobnego auta na OtoMoto i OLX
oraz pisze, jak duża jest różnica wobec aukcji poleasingowych — bo to ona
jest właściwą miarą okazji.

Ta liczba stoi na karcie **osobno i jest podpisana jako szacunek**: model
nie ma dostępu do internetu, więc jest to jego wiedza o rynku, a nie odczyt
z portali. Polecenie wprost zakazuje wymyślania konkretnych ogłoszeń, linków
i liczby ofert, a „nie umiem oszacować" jest poprawną odpowiedzią — pole
zostaje wtedy puste zamiast wypełnić się zmyśloną kwotą.

## 0.10.1 — w przygotowaniu

**Widać, dlaczego wycena AI odmówiła.** Dotąd w logu było samo „400 Bad
Request", a na karcie „nie udało się" — tym samym kodem dostawca odpowiada
i na nieznaną nazwę modelu, i na nieobsługiwany format odpowiedzi, więc nie
dało się zgadnąć, co poprawić. Powód od dostawcy (np. `Model Not Exist`)
trafia teraz do logu i na kartę aukcji. Wyjątkiem jest odrzucony klucz:
przy 401/403 pokazujemy własne zdanie, bo część dostawców odsyła w błędzie
fragment klucza.

**Opcje AI mają wreszcie opisy.** W konfiguracji dodatku `ai_provider`,
`ai_api_key` i `ai_base_url` pokazywały się jako surowe klucze, a przy
`ai_model` wisiał opis „Model OpenAI… domyślnie gpt-5.4-mini" — mylący przy
DeepSeeku. Nowy test pilnuje, żeby każda opcja miała nazwę i opis w obu
językach.

## 0.10.0 — w przygotowaniu

**Nowy wygląd interfejsu.** Język wizualny idzie za serwisami, z których
zbieramy dane: granat jako kolor konstrukcyjny, geometryczny grotesk
(Montserrat, hostowany lokalnie — żadnych CDN-ów) i **lista jako siatka
kafelków ze zdjęciem** zamiast tabeli. Na kafelku: zdjęcie w stałym kadrze
3:2, źródło i licznik do końca na zdjęciu, cena jako największy element.

Kolor niesie w tej palecie wyłącznie informację: czerwień znaczy „kończy się
w ciągu kwadransa", zieleń „poniżej Twojej ceny docelowej". Gdyby czerwień
trafiła też na przycisk, przestałaby cokolwiek znaczyć w liście kilkudziesięciu
pozycji.

Karta aukcji dostała cenę obok tytułu, a dane techniczne układają się
w równe kolumny etykieta–wartość. Na telefonie nawigacja jest jednym
przewijanym rzędem zamiast trzech zawiniętych, a placeholder braku zdjęcia
jest cichy i podąża za motywem Home Assistanta.

## 0.9.0 — w przygotowaniu

**Rodzaj pojazdu i filtr.** Źródła sprzedają w jednej kategorii samochody
osobowe, furgony, ciągniki siodłowe i naczepy — lista mieszała je ze sobą.
Każda aukcja ma teraz rozpoznany rodzaj, a w głównym pasku filtrów stoi jego
wybór. **Domyślnie widać osobowe**; „wszystkie" jest jednym kliknięciem obok.
Rozpoznanie bierze się z tego, co dane źródło faktycznie mówi: autoprzetarg
ma kategorię w adresie aukcji, EFL osobne pole, a poleasingowe wyłącznie
nazwę pojazdu. Migracja uzupełnia wiersze już zebrane.

**Motocykle z poleasingowe.pl.** Serwis trzyma je pod osobnym adresem listy,
którego nie przemiataliśmy — filtr „motocykle" był dla tego źródła zawsze
pusty. Teraz przemiat obejmuje obie kategorie.

**Koniec „dziwnych tytułów" z EFL.** Serwis podaje na liście jedno zdanie:
nazwa pojazdu, rocznik, tablica rejestracyjna i adres firmy, w której stoi
auto. Całość szła do rozbioru na markę i model, więc wersja wyposażenia
kończyła się adresem obcej firmy. Tytuł jest teraz rozbierany na części,
a z ogona odzyskujemy **lokalizację**, której lista w ogóle nie podaje jako
pola. Tablicy rejestracyjnej nie zapisujemy.

**Zdjęcia z EFL.** Galeria szła pod adres składany z identyfikatora
(`/Auction/x-id<id>`) — założenie o routingu, którego rekonesans nigdy nie
potwierdził. Teraz adapter idzie pod adres zapamiętany przy zbieraniu, a gdy
pobranie galerii się nie uda, widać to w logu na poziomie WARNING zamiast
niewidocznego INFO.

**Zakończone aukcje przestają udawać aktywne.** Aukcji nieobserwowanej nie
odpytujemy po raz drugi, a marker końca stoi tylko na stronie szczegółów —
więc po terminie zostawała `ACTIVE` na zawsze. Dispatcher zamyka je po
upływie okna dogrywki źródła, oznaczając cenę jako ostatnią widzianą, nigdy
jako potwierdzoną.

**Wycena AI u dowolnego dostawcy.** `ai_provider` wybiera między OpenAI,
Anthropic i dowolnym endpointem zgodnym z OpenAI — DeepSeek, OpenRouter,
Groq, a także model lokalny (Ollama, LM Studio), co pozwala nie wypuszczać
danych pojazdu poza sieć domową. Dostawcy różnie wymuszają strukturę
odpowiedzi, więc przy odrzuceniu `json_schema` dodatek sam ponawia żądanie
w trybie `json_object`. Stara opcja `openai_api_key` nadal działa.

## 0.8.1 — w przygotowaniu

**Prawdziwe miniatury na liście.** Add-on generuje i cache'uje osobny JPEG
240×160 zamiast wysyłać do przeglądarki pełne zdjęcie i zmniejszać je samym
CSS-em. Lista ma większy, stały kadr 3:2 podobny do kart popularnych serwisów
ogłoszeniowych; galeria szczegółów nadal korzysta z pełnych zdjęć.

## 0.8.0 — w przygotowaniu

**Poprawione archiwum.** Pokazuje teraz tylko auta pozostawione na watchliście,
zarówno zakończone normalnie, jak i oznaczone przez źródło jako zniknięte.
Parser Autoprzetarg usuwa z tytułów także techniczne ogony zapisane jako `cm3`
lub pojemność z rodzajem paliwa. Migracja poprawia tytuły już zebranych aukcji.

**Zdjęcia i wycena AI.** Lista pokazuje leniwie ładowaną miniaturę pierwszego
zdjęcia, pobieraną przez bezpieczne proxy add-onu. Brak zdjęcia daje neutralny
placeholder zamiast ikony uszkodzonego obrazu. Karta aukcji pokazuje całą
galerię — bez wcześniejszego limitu 20 zdjęć — a twardy limit 100 MB cache'u
na dysku nadal obowiązuje.

Po ustawieniu `openai_api_key` karta automatycznie generuje orientacyjną
wycenę: wartość, realistyczny przedział, poziom pewności, uzasadnienie i
założenia. Model dostaje parametry techniczne auta oraz zagregowane ceny
zakończonych aukcji tego samego modelu i zbliżonych roczników. VIN, zewnętrzny
identyfikator i dane sprzedającego nie opuszczają add-onu. Wynik jest
cache'owany według danych pojazdu, porównań, modelu i wersji promptu.

## 0.7.3 — w przygotowaniu

**Naprawa: CSS i HTMX pod Ingressem bez zależności od nagłówka proxy.**
Objawy były połączone: HTML listy dochodził, ale bez arkusza stylów wyglądał
jak surowy dokument, a bez HTMX przycisk obserwowania nic nie robił.

Poprzednia poprawka nadal składała publiczne adresy z `X-Ingress-Path`.
Teraz dokument wyznacza korzeń add-onu względnym `<base href>` (np. `../`
z karty aukcji), a wszystkie zasoby, linki i akcje formularzy są względem
tego korzenia. Prefiks i token Ingressu zostają w adresie przeglądarki bez
odczytywania ich przez aplikację. Obejmuje to także fragmenty zwracane przez
HTMX oraz względne nagłówki `Location` po formularzach.

## 0.7.2 — w przygotowaniu

**Naprawa: pliki statyczne oddawaly 404 pod Ingressem.** To byla wlasciwa
przyczyna „GUI nie dziala", ktorej poprzednia poprawka nie usunela. Middleware
wpisywal prefiks Ingressu do `scope["root_path"]`, co lamie umowe ASGI:
`root_path` ma byc POCZATKIEM `scope["path"]`, a Home Assistant prefiks juz
zdjal. Starlette 0.38 liczy trase jako `path` minus `root_path` i przekazuje
`root_path` do podaplikacji, wiec `StaticFiles` szukal pliku pod
`<katalog>/static/styl.css` — 404 na arkusz stylow i na HTMX-a, przy dzialajacych
zwyklych trasach. Stad strona bez stylow i martwa gwiazdka obserwacji.

Scope nie jest juz dotykany; prefiks czytamy z naglowka. Sprawdzone przez
atrape Ingressu (osobny port, zdejmowanie prefiksu, naglowek X-Ingress-Path),
a nie tylko testami jednostkowymi.

**Ujednolicenie nazw paliwa.** Serwisy uzywaja ROZNYCH SLOW, nie tylko roznej
wielkosci liter: `Olej napedowy` (poleasingowe, bez ogonka), `Olej napędowy`
(EFL, z ogonkiem) i `Diesel` (autoprzetarg) to jedno paliwo; `Hybryda`
i `Hybryda/benzyna` — drugie. Lista rozwijana miala piec pozycji na trzy
paliwa. Slownik jest jawny, migracja `007_paliwa` porzadkuje dane juz zebrane.

## 0.7.1 — w przygotowaniu

Ujednolicenie zapisu marek.

- poleasingowe i autoprzetarg pisza marki WERSALIKAMI (`TESLA`), EFL zwyklym
  zapisem (`Tesla`) — lista rozwijana miala przez to po dwa wpisy na marke,
  filtr po jednym z nich gubil polowe wynikow, a `v_market_stats` liczyl dwie
  osobne mediany dla tego samego modelu
- marka zapisywana jest teraz w postaci kanonicznej, w warstwie
  antykorupcyjnej, wiec obejmuje wszystkie adaptery naraz
- skroty zostaja wersalikami: `BMW`, `MAN`, `DAF`, `SEAT` (`Bmw` wyglada
  na literowke)
- jawna lista aliasow laczy rozne nazwy tej samej marki: `MERCEDES` z
  `MERCEDES-BENZ`, `VW` z `Volkswagen`, `SKODA` i `ŠKODA` ze `Škoda`,
  `CITROEN` z `Citroën`
- migracja `006_marki` porzadkuje wiersze juz zebrane, w tym aukcje
  zakonczone — tych przemiat listy juz nie dotknie, a to one niosa ceny
  koncowe

## 0.7.0 — w przygotowaniu

**Naprawa: interfejs nie dzialal pod Ingressem.** Adresy powstawaly przez
`url_for`, ktory buduje adres BEZWZGLEDNY i bierze host z zadania widzianego
przez add-on — czyli wewnetrzny adres kontenera (`172.30.33.5:8099`). Home
Assistant proxuje Ingress i nie przekazuje zewnetrznego hosta, wiec
przegladarka dostawala odsylacze do hosta, do ktorego nie ma dostepu: arkusz
stylow sie nie wczytywal, HTMX tez, a bez HTMX-a gwiazdka obserwacji
i doladowanie kolejnej strony po prostu nic nie robily. Adresy sa teraz
wzgledne wobec origin.

**Naprawa: brakujaca data zakonczenia.** Aukcje odkryte przed 0.5.0 mialy
pusty termin odpytu i nigdy nie dostaly odpytu szczegolow, wiec zostawaly
z pustym `ends_at` na zawsze. Migracja `005_uzupelnij_terminy` planuje im
ten jeden odpyt.

**Zdjecia na karcie aukcji.** Pobierane na zadanie, podawane przez add-on
(nie hotlink), cache na dysku z twardym limitem 100 MB. Adresy NIE trafiaja
do bazy. Trasa przyjmuje indeks zdjecia, nie adres — inaczej add-on bylby
otwartym proxy do sieci lokalnej.

## 0.6.0 — w przygotowaniu

Adapter autoprzetarg.pl (RECON.md §4.4) — trzecie zrodlo.

- czyta anonimowo komplet danych technicznych; jako jedyny serwis podaje
  **VIN juz na liscie**, wiec deduplikacja po VIN dziala bez wchodzenia
  w szczegoly
- termin z ukrytego `auctionEndDate`, czytany z zasiegu KAFELKA — pole stoi
  po `href` swojego kafelka i parowanie "pole -> nastepny link" przesuwa
  caly wynik o jeden
- `bid_count` zostaje PUSTE, nie zerowe: serwis nie podaje liczby ofert bez
  zalogowania, a zero znaczyloby "nikt nie licytowal"
- zniknieta aukcja (302 po terminie) zapisuje sam status, bez kasowania
  ostatniej znanej ceny

NAPRAWA DOTYCZACA WSZYSTKICH ADAPTEROW: `Accept-Encoding` wpisany recznie
jako `gzip, br` oglaszal brotli, ktorego httpx bez dodatkowego pakietu nie
umie rozpakowac. autoprzetarg wybieral brotli i dostawalismy bajty nie do
odczytania — parser widzial pusta liste i NIE zglaszal bledu. Dolozony
`brotli`, naglowek ustawia teraz httpx.

## 0.5.0 — w przygotowaniu

Interfejs i uzupelnienie danych.

- **nowo odkryta aukcja dostaje JEDEN odpyt szczegolow**, potem wraca do
  trybu "wystarcza przemiat". Bez tego lista poleasingowe nie miala godziny
  zakonczenia (serwis podaje na niej sama date dzienna), wiec nie dalo sie
  zdecydowac, co warto obserwowac
- gwiazdka w liscie: obserwowanie jednym klikniecieciem, bez wchodzenia
  w szczegoly; wlacza i wylacza tez regularny odpyt aukcji
- filtry zwarte — rzadko uzywane pod "Wiecej filtrow", ktore otwiera sie
  samo, gdy cos w srodku dziala
- sortowanie klikane w naglowkach kolumn, z zachowaniem filtrow
- kolor pilnosci: aukcja konczaca sie w ciagu kwadransa widoczna bez
  czytania kolumny z data
- licznik pozycji i rozroznienie "jeszcze nic nie zebrano" od "nic nie
  pasuje do tych filtrow" — te dwie sytuacje wymagaja czego innego
- nazwy sortowan po ludzku zamiast `koniec-desc`

## 0.4.1 — w przygotowaniu

Naprawa: dodatek nie pokazywal ZADNYCH ofert.

- `przemiec_liste` nie bylo wywolywane NIGDZIE. Dispatcher z etapu 9
  odswiezal wylacznie aukcje juz obecne w bazie, a nic ich tam nie wstawialo,
  wiec baza zostawala pusta na zawsze. Testy dispatchera same wstawialy
  aukcje, wiec przechodzily mimo braku funkcji.
- migracja `004_przemiat`: `source.last_sweep_at` — bez tego przemiat szedlby
  przy kazdym obrocie petli albo po kazdym restarcie
- zbiorczy zapis z przemiatu UZUPELNIA pola, nigdy nie kasuje: lista wie
  mniej niz strona szczegolow (poleasingowe nie podaje na liscie godziny
  zakonczenia), wiec `ends_at` i status z odpytu szczegolow zostaja
- dodanie do watchlisty wlacza pojedynczy odpyt, usuniecie go wylacza —
  bez tego obserwowanie niczego nie zmienialo

## 0.4.0 — w przygotowaniu

Adapter poleasingowe.pl (RECON.md §4.2) — drugie zrodlo, bez logowania.

- parser czyta blok inicjalizujacy Alpine.js, bo tam serwis renderuje
  komplet danych: cene, liczbe ofert, postapienie i ABSOLUTNA date konca
  z jawna strefa. Widoczny tekst podaje tylko "16 godzin"
- `content_hash` liczony z bloku aukcji, nie z calej strony: trzy kolejne
  zadania daja trzy rozne tresci (token CSRF w trzech miejscach + karuzela
  polecen), a hash samego bloku byl identyczny
- login zwyciezcy (`winner`) wycinany w parserze — to dane osobowe osoby
  trzeciej, ktorych nie chroni zadne haslo
- lista NIE ustawia `ends_at`: podaje date bez godziny, a falszywa precyzja
  byla by gorsza niz jej brak

## 0.3.0 — w przygotowaniu

Harmonogram odpytywania (SPEC.md §11).

- `PollingPolicy` jako czysta funkcja: pelna tabela progow od 24 h do floora
- floor wyliczany z okna dogrywki serwisu (polowa okna, nie mniej niz 10 s),
  a nie ze stalej — poleasingowe 15 s, autoprzetarg i leasygroup 60 s
- kubelek tokenow per zrodlo, z jitterem doliczanym tylko gdy i tak czekamy
- bezpiecznik per zrodlo: po serii bledow zrodlo pauzuje, pozostale pracuja
- petla spi do najblizszego terminu, nie budzi sie na stalym ticku
- tani odpyt: adapter porownuje hash surowych bajtow PRZED parsowaniem
- `run_log` po kazdym przebiegu, z RSS procesu i rozmiarem bazy
- rejestracja zrodel przy starcie z parametrami z rekonesansu

**Zrodla sa domyslnie wylaczone.** Dopoki lista `sources` w opcjach jest
pusta, dodatek niczego nie odpytuje.

## 0.2.1 — w przygotowaniu

Poprawka startu kontenera.

- profil AppArmor blokowal `/init`. W s6-overlay v3 `/init` jest skryptem
  powloki, wiec jadro uruchamia `/bin/sh /init`, a powloka musi ten plik
  ODCZYTAC — samo `ix` daje wykonanie bez odczytu. Objaw w logu dodatku
  brzmial `/bin/sh: can't open '/init': Permission denied` i w niczym nie
  wskazywal na AppArmora.
- profil przepisany na zasade: odczyt szeroki, ZAPIS waski. Kontener i tak
  nie ma podniesionych uprawnien, wiec wartosc profilu siedzi w tym, ze
  dodatek nie moze pisac po `/ssl`, `/config`, `/media`, `/addons` ani po
  katalogach innych dodatkow w `/share`.

## 0.2.0 — w przygotowaniu

Interfejs operacyjny (SPEC.md §12).

- lista z filtrami, sortowaniem i paginacja keyset (bez `OFFSET`)
- widoki: koncza sie w 24 h, nowe od ostatniej wizyty, obserwowane, archiwum
- karta aukcji z linkami do oferty i do dashboardow Grafany
- watchlist: notatka, cena docelowa, wyroznienie po zejsciu do progu
- zapisane filtry
- panel diagnostyczny: stan bazy, pula polaczen, dryf zegara, RSS, rozmiar
  bazy, tabela zrodel z resetem blokady logowania
- migracja `003_domkniecie`: parametry domkniecia aukcji per zrodlo

Poprawki blokujace instalacje, obie widoczne wylacznie w dzienniku
Supervisora:

- `apparmor` w `config.yaml` musi byc boolean — nazwa profilu powodowala
  odrzucenie calego pliku i dodatek nie pojawial sie w sklepie
- tag obrazu bazowego musi miec pelna postac `3.12-alpine3.22`; `3.12-alpine`
  nie istnieje w GHCR i budowanie wywalalo sie po kilku minutach

## 0.1.0 — w przygotowaniu

Pierwsza wersja. Jeszcze nie do uzytku produkcyjnego.

- warstwy domenowa i dostepu do danych, migracje `001_init` i `002_reporting`
- widoki w schemacie `reporting` dla Grafany
- adapter `aukcje.efl.com.pl`
- opakowanie add-onu: Ingress, s6, AppArmor
