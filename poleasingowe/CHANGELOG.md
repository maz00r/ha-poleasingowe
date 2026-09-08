# Historia zmian

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
