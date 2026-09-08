-- 010_wyceny.sql — wycena AI zapisana na stale (SPEC.md §8.2, §12).
--
-- Do tej pory wycena lezala w cache'u na dysku, kluczowanym HASHEM DANYCH
-- WEJSCIOWYCH — razem z porownaniami z zakonczonych aukcji. Kazda kolejna
-- zakonczona aukcja tego modelu zmieniala te porownania, wiec klucz przestawal
-- pasowac i karta liczyla wycene OD NOWA: inna kwota przy kazdym wejsciu
-- i kolejne platne zadanie do dostawcy.
--
-- Wycena jest teraz wierszem w bazie, jednym na aukcje. Raz policzona zostaje
-- i sie nie zmienia, dopoki uzytkownik sam nie kliknie "Przelicz".
--
-- Dlaczego osobna tabela, a nie kolumny w `auction`: to jest opinia modelu
-- o aukcji, a nie fakt o niej. Trzymanie tego w `auction` mieszaloby dane
-- zebrane ze zrodla z tym, co dopisalismy sami (§8.4).

CREATE TABLE IF NOT EXISTS app.ai_valuation (
    auction_id      bigint PRIMARY KEY
                    REFERENCES app.auction(id) ON DELETE CASCADE,
    value_amount    numeric(12,2) NOT NULL,
    min_amount      numeric(12,2) NOT NULL,
    max_amount      numeric(12,2) NOT NULL,
    currency        text          NOT NULL DEFAULT 'PLN',
    -- Poziom cen ofertowych na portalach: SZACUNEK modelu z jego wiedzy,
    -- nie odczyt z OtoMoto ani OLX. NULL znaczy "nie umiem oszacowac"
    -- i jest poprawna odpowiedzia (§12).
    portal_amount   numeric(12,2),
    confidence      text          NOT NULL,
    rationale       text          NOT NULL,
    assumptions     text[]        NOT NULL DEFAULT '{}',
    model           text          NOT NULL,
    prompt_version  integer       NOT NULL,
    created_at      timestamptz   NOT NULL DEFAULT now(),

    CONSTRAINT ai_valuation_currency_check CHECK (currency IN ('PLN', 'EUR')),
    CONSTRAINT ai_valuation_confidence_check
        CHECK (confidence IN ('niska', 'średnia', 'wysoka')),
    -- Ten warunek nie da sie wyrazic w JSON Schema, wiec pilnujemy go i tu,
    -- i w kodzie: niespojny przedzial wyglada wiarygodnie i dlatego jest
    -- grozniejszy niz brak wyceny.
    CONSTRAINT ai_valuation_przedzial_spojny
        CHECK (min_amount <= value_amount AND value_amount <= max_amount),
    CONSTRAINT ai_valuation_kwoty_nieujemne
        CHECK (min_amount >= 0 AND (portal_amount IS NULL OR portal_amount >= 0))
);

COMMENT ON TABLE app.ai_valuation IS
    'Wycena orientacyjna z modelu jezykowego, jedna na aukcje. Opinia, nie fakt.';

-- BEZ grantu dla `grafana_ro`. Rola raportowa widzi wylacznie widoki
-- w `reporting` (§9) i tak ma zostac — gdyby wyceny mialy trafic na
-- dashboard, wlasciwa droga jest przez nowy widok, a nie przez otwarcie
-- tabeli bazowej.
