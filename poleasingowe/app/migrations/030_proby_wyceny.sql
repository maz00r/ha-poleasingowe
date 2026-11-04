-- 030 — próby automatycznej wyceny z Otomoto (0.34.0).
--
-- Automat wycenia każdą aktywną aukcję, a część z nich wycenić się nie da
-- (model nieznany Otomoto). Gdyby pamiętał to tylko w procesie, każdy deploy
-- zaczynałby od tych samych porażek i znów płacił za dopasowanie modelu.
-- Stąd osobna tabela: jeden wiersz na aukcję, licznik prób i ostatni błąd.
-- `last_attempt_at` niesie też dobowy limit wycen.
--
-- Osobno od `ai_valuation`, bo próba nie jest wyceną: nieudana nie ma kwoty,
-- a udana nie może kasować tego, co wycena mówi o aucie.

CREATE TABLE IF NOT EXISTS app.ai_valuation_attempt (
    auction_id      bigint PRIMARY KEY
                    REFERENCES app.auction(id) ON DELETE CASCADE,
    attempts        integer     NOT NULL DEFAULT 1,
    last_attempt_at timestamptz NOT NULL,
    -- NULL znaczy, że ostatnia próba się udała.
    last_error      text,

    CONSTRAINT ai_valuation_attempt_attempts_check CHECK (attempts >= 1)
);

CREATE INDEX IF NOT EXISTS ai_valuation_attempt_czas_idx
    ON app.ai_valuation_attempt (last_attempt_at);

COMMENT ON TABLE app.ai_valuation_attempt IS
    'Proby automatycznej wyceny z Otomoto, jedna linia na aukcje.';

-- BEZ grantu dla `grafana_ro` — jak `ai_valuation` (010_wyceny.sql).
