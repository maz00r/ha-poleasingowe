-- 033 — ponownie wystawione auto trzyma wycenę poprzedniego wystawienia.
--
-- Ten sam egzemplarz (VIN) pod nowym numerem aukcji dostawał dotąd własną,
-- nową wycenę — inną kwotę za to samo auto. Od tej wersji dispatcher kopiuje
-- ostatnią wycenę wcześniejszego wystawienia. Tu to samo dla aut już
-- zebranych, które jeszcze nie mają wyceny. Istniejących wycen nie ruszamy.

INSERT INTO app.ai_valuation (
    auction_id, value_amount, min_amount, max_amount, currency, portal_amount,
    confidence, rationale, assumptions, model, prompt_version, created_at,
    source_kind, otomoto_count, otomoto_p25, otomoto_p75, otomoto_search_url,
    otomoto_listings, appraisal_documents
)
SELECT DISTINCT ON (b.id)
    b.id, v.value_amount, v.min_amount, v.max_amount, v.currency,
    v.portal_amount, v.confidence, v.rationale, v.assumptions, v.model,
    v.prompt_version, v.created_at, v.source_kind, v.otomoto_count,
    v.otomoto_p25, v.otomoto_p75, v.otomoto_search_url, v.otomoto_listings,
    v.appraisal_documents
FROM app.auction AS b
JOIN app.auction AS p ON p.vin = b.vin
                      AND p.id <> b.id
                      AND p.duplicate_of IS NULL
JOIN app.ai_valuation AS v ON v.auction_id = p.id
WHERE b.vin IS NOT NULL
  AND b.duplicate_of IS NULL
  AND (p.first_seen_at, p.id) < (b.first_seen_at, b.id)
  AND NOT EXISTS (SELECT 1 FROM app.ai_valuation WHERE auction_id = b.id)
ORDER BY b.id, v.created_at DESC, p.id DESC
ON CONFLICT (auction_id) DO NOTHING;
