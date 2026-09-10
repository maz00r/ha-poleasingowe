# RECON dawro.pl — ETAP 0
Wygenerowano: 2026-09-10T21:28:22+02:00  
Żądań w tym przebiegu: 74
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
- Status: **pominięte (--static-only)**

## Do ustalenia w planie adaptera
- Drabinka domknięcia (z `pomiar-domkniecie-*.json`).
- Polityka brutto/netto per sprzedawca (kolumna `podstawa_ceny`).
- Czy `POST /WebService/PasekInformacyjny/` wolno odpytywać w adapterze, czy trzymać się kafelka listy.
