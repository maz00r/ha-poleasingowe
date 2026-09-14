# RECON dawro.pl — ETAP 0
Wygenerowano: 2026-09-14T10:10:00+02:00  
Żądań w tym przebiegu: 106
## Listy i identyfikatory
- Adres skanu: `/aukcje/sortuj,data-zakonczenia,kierunek,rosnaco,strona,N,ilosc,100,wyswietlanie,boxy` (sortowanie po dacie zakończenia rosnąco, 100/stronę, układ *boxy*)
- `external_id`: liczba ze ścieżki `/aukcja/<id>,<slug>` — slug jest zmienny, `<id>` stały
- Aukcji na liście: **24**
- Kodowanie: treść jest UTF-8, mimo że nagłówek HTTP mówi `iso-8859-1` — nagłówkowi nie ufać
- robots.txt: `Allow: /` dla wszystkich; sitemap `https://www.dawro.pl/sitemap.xml`
- Sprzedawcy w tej próbce: MultiDealer, STELLANTIS, VELOBANK

## Pokrycie pól (szczegóły)
| pole | jest | brak |
|---|---:|---:|
| vin | 24 | 0 |
| nr_rejestracyjny | 24 | 0 |
| parking | 24 | 0 |
| opis_modelu | 24 | 0 |
| rok_produkcji | 24 | 0 |
| przebieg | 20 | 4 |
| pojemnosc | 24 | 0 |
| moc | 24 | 0 |
| paliwo | 0 | 24 |
| skrzynia | 0 | 24 |
| nadwozie | 0 | 24 |
| sprzedawca | 24 | 0 |
| forma_sprzedazy | 24 | 0 |
| zdjecia | 24 | 0 |
| cena_wywolawcza | 24 | 0 |
| podstawa_ceny | 0 | 24 |

## Cena
- Podstawa ceny ustalana **wyłącznie** z jawnego „netto”/„brutto” na stronie; brak oznaczenia → `UNKNOWN`, bez przeliczania VAT.
- Rozkład w próbce: {"UNKNOWN": 24}
- „Najwyższa oferta” na stronie szczegółów ładowana AJAX-em przez `POST /WebService/PasekInformacyjny/` (`{id}` → `{kwota, twoja_oferta}`). Endpoint jest publiczny (strona woła go anonimowo co 2 s), ale ten skrypt go **nie wywołuje**. Serwerowo najwyższą ofertę podaje kafelek listy (`div.najwyzsza-oferta`).

## Galeria
- Aukcji ze zdjęciami: 24/24
- Pełne zdjęcie: `href` znacznika `a.jackbox` w `#zdjecie-male` (`/cache/zdjecia/…`)
  - `16761`: 8 zdjęć, pierwsze/ostatnie OK
  - `16762`: 8 zdjęć, pierwsze/ostatnie OK
  - `16763`: 11 zdjęć, pierwsze/ostatnie OK
  - `16764`: 8 zdjęć, pierwsze/ostatnie OK
  - `16770`: 8 zdjęć, pierwsze/ostatnie OK

## Domknięcie
- Status: **wykonane**
- Próbek: 32 (`[0, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600]` s po terminie, run-up co 10 s od T-180 s)
- Surowe odczyty: `pomiar-domkniecie-*.json`, zrzuty `domkniecie-*.html`
- Strona przestaje być stroną aukcji przy: nie zaobserwowano w oknie pomiaru
- Przycisk „PRZYSTĄP DO AUKCJI” znika przy: post+0s
- Cena wywoławcza przestaje być czytana przy: post+0s

## Do ustalenia w planie adaptera — rozstrzygnięte (RECON.md §4.5)
- ~~Drabinka domknięcia~~ — **`(5, 20)` s**: okno odzyskania ceny zmierzone jako zero, drabinka nie ma czego łapać.
- ~~Polityka brutto/netto per sprzedawca~~ — **bez przeliczania**: kwota zapisywana tak, jak podał ją serwis (jak EFL/autoprzetarg/poleasingowe), bo próbka nie dała żadnego dowodu podstawy.
- ~~Czy `POST /WebService/PasekInformacyjny/` wolno odpytywać w adapterze~~ — **nie**: kontrakt endpointu nie był zmierzony (tylko obserwowany w JS-ie strony), więc adapter trzyma się kafelka listy; `price_current` odświeża się przy przemiacie, nie przy odpycie szczegółów.
