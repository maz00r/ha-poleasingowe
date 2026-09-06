# fixtures/autoprzetarg/ — do wypełnienia ręcznie

Z tego serwisu nie pobieram nic automatycznie (decyzja z 2026-09-06:
`robots.txt` autoprzetarg.pl zawiera wpis `User-agent: ClaudeBot / Disallow: /`).
Pliki wrzuca tu użytkownik, zapisując strony z przeglądarki.

Potrzebne do domknięcia rekonesansu (SPEC.md §4):

| Plik | Co to |
|---|---|
| `robots.txt` | zrzut, dla kompletu |
| `lista-01.html` | lista aukcji samochodowych, strona 1 |
| `lista-02.html` | strona 2 — żeby rozpoznać kształt paginacji |
| `szczegoly-<id>.html` | pojedyncza aukcja, aktywna |
| `szczegoly-zakonczona-<id>.html` | aukcja **zakończona** — punkt (g) |
| `regulamin.html` | `/regulamin` — punkt (a), reguła dogrywki |
| `logowanie.html` | `/uzytkownik/logowanie` — captcha/2FA, pola formularza |

Wersje po zalogowaniu nazywaj z sufiksem `-auth-`
(np. `szczegoly-auth-123.html`). Są objęte `.gitignore` i nie trafią do repo.

Czego nie da się odtworzyć ze statycznego zrzutu, a czego wymaga §4:

- **(b)** jak długo cena jest widoczna po wygaśnięciu — wymaga odpytania
  strony aukcji po 2 s, 5 s, 15 s, 60 s i 5 min od `ends_at`;
- **(e)** czy w końcówce cena odświeża się AJAX-em — wymaga zakładki
  Network w trakcie trwania końcówki.

Jeśli ich nie zmierzysz, zostaną w `RECON.md` jako niewiadome.
