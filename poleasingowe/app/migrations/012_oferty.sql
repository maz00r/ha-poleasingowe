-- 012_oferty.sql — realna lista ofert zamiast zgadywania (SPEC.md §11.8).
--
-- CO SIE ZMIENIA. Do tej pory kompletnosc historii licytacji szacowalismy
-- z roznic `bid_count` miedzy snapshotami (`price_snapshot.bid_gap`). To jest
-- oszacowanie z zalozenia — i dla EFL zalozenie falszywe, bo `bid_count`
-- liczy tam UCZESTNIKOW licytacji proxy, nie postapienia (§11.8, `003`).
--
-- Tymczasem EFL pokazuje liste ofert WPROST, w tej samej odpowiedzi HTTP,
-- ktora i tak pobieramy po cene: zakladka "Oferty" jest inline w HTML i nie
-- wymaga logowania (RECON.md §4.1). §11.8 mowi jasno, ze taka lista ma
-- pierwszenstwo przed czestszym odpytywaniem. Koszt w zadaniach: ZERO.
--
-- DLACZEGO OSOBNA TABELA, A NIE KOLUMNY W `price_snapshot`. To sa dwie rozne
-- rzeczy i zlanie ich klamaloby w obie strony. Snapshot to NASZ odczyt stanu
-- w chwili, w ktorej akurat zapytalismy. Oferta to zdarzenie po stronie
-- serwisu, z wlasnym momentem zlozenia — wczesniejszym niz nasz odczyt,
-- czasem o kilka dni.
--
-- CO ZYSKUJEMY PONAD SERWIS. EFL pokazuje jeden wiersz na uczestnika,
-- AKTUALIZOWANY W MIEJSCU: ten sam kod 106125 mial 48 600 zl 4.09 o 13:00,
-- a 7.09 o 10:42 juz 51 600 zl — starszej oferty nie widac nigdzie. My
-- zapisujemy kazdy zaobserwowany stan jako osobny wiersz, wiec nasze archiwum
-- odtwarza przebieg, ktory serwis o sobie zapomina.
--
-- PSEUDONIM ZAMIAST KODU SERWISU. Kolumna `uczestnik` NIE jest kodem oferty
-- z EFL. To skrot liczony z pary (aukcja, kod), wiec ten sam licytant dostaje
-- INNY pseudonim w kazdej aukcji. Zachowujemy to, co jest tu potrzebne — ze
-- dwie oferty w TEJ aukcji pochodza od tego samego licytanta — i tracimy to,
-- czego nie chcemy miec: mozliwosc zbudowania profilu licytanta przez wiele
-- aukcji. To jest pseudonimizacja, nie anonimizacja: kody sa krotkie, wiec
-- majac aukcje i kod da sie skrot odtworzyc. Chroni przed przypadkowym
-- zbieraniem profili, nie przed kims, kto celowo szuka konkretnej osoby.

CREATE TABLE IF NOT EXISTS app.offer (
    id            bigserial PRIMARY KEY,
    auction_id    bigint      NOT NULL
                  REFERENCES app.auction(id) ON DELETE CASCADE,
    uczestnik     text        NOT NULL,
    amount        numeric(12,2) NOT NULL,
    currency      text        NOT NULL DEFAULT 'PLN',
    -- Czas WEDLUG SERWISU. EFL podaje go z dokladnoscia do ulamka sekundy
    -- ("2026.09.07 10:42:05.8800") i bez strefy — mapper zaklada czas polski
    -- i przelicza na UTC (§8.2).
    placed_at     timestamptz NOT NULL,
    -- Czas WEDLUG NAS. Roznica `first_seen_at - placed_at` mowi, o ile
    -- spoznil sie nasz odczyt — jedyna miara opoznienia, jaka mamy, bo
    -- serwis nie zdradza, kiedy oferta pojawila sie na stronie.
    first_seen_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT offer_currency_check CHECK (currency IN ('PLN', 'EUR')),
    CONSTRAINT offer_amount_nieujemna CHECK (amount >= 0),
    CONSTRAINT offer_uczestnik_niepusty CHECK (uczestnik <> ''),
    -- Klucz naturalny. Ten sam odpyt powtorzony pare razy w fazie domkniecia
    -- NIE ma tworzyc duplikatow, a podniesienie oferty przez tego samego
    -- licytanta ma byc nowym wierszem — rozroznia je moment zlozenia.
    CONSTRAINT offer_naturalny UNIQUE (auction_id, uczestnik, placed_at)
);

-- Odczyt jest zawsze "wszystkie oferty tej aukcji, po kolei" — karta aukcji
-- nie ma innego zapytania o oferty.
CREATE INDEX IF NOT EXISTS offer_auction_placed_idx
    ON app.offer (auction_id, placed_at DESC);

COMMENT ON TABLE app.offer IS
    'Oferty odczytane wprost ze strony aukcji (SPEC.md §11.8). Zrodlo prawdy '
    'o przebiegu licytacji tam, gdzie serwis je podaje — w odroznieniu od '
    'price_snapshot.bid_gap, ktory jest oszacowaniem z roznic licznika.';

COMMENT ON COLUMN app.offer.uczestnik IS
    'Pseudonim wazny TYLKO w obrebie jednej aukcji: skrot z pary (aukcja, kod '
    'serwisu). Umozliwia grupowanie ofert jednego licytanta w tej aukcji '
    'i uniemozliwia sledzenie go miedzy aukcjami.';

COMMENT ON COLUMN app.offer.placed_at IS
    'Moment zlozenia wedlug serwisu (UTC). Nie mylic z first_seen_at.';

-- Rola raportowa nie dostaje tabeli bazowej (§9) — jesli oferty maja trafic
-- na dashboard, wlasciwa droga jest przez widok w `reporting`.
