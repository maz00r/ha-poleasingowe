# Historia zmian

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
