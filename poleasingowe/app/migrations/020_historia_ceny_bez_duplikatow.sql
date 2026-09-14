-- Historia ma pokazywać zmiany kwoty, a nie każde odświeżenie aukcji.
-- Zachowujemy pierwszy wpis każdej serii tej samej ceny; kolejne nie niosą
-- informacji o zmianie ceny i tylko zaśmiecają kartę aukcji.
WITH kolejne AS (
    SELECT
        id,
        price,
        currency,
        lag(price) OVER historia AS poprzednia_cena,
        lag(currency) OVER historia AS poprzednia_waluta
    FROM app.price_snapshot
    WINDOW historia AS (PARTITION BY auction_id ORDER BY ts, id)
)
DELETE FROM app.price_snapshot AS snapshot
USING kolejne
WHERE snapshot.id = kolejne.id
  AND kolejne.price = kolejne.poprzednia_cena
  AND kolejne.currency = kolejne.poprzednia_waluta;
