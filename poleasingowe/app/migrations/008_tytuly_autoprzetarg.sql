-- 008_tytuly_autoprzetarg.sql — naprawa historycznych tytulow.
--
-- Autoprzetarg dopisuje do nazwy dane techniczne. Parser rozpoznawal `ccm`,
-- ale w czesci aukcji wystepuje `cm3` albo zapis `3,0 DIESEL / 175 KM`.
-- Nowe wpisy czyści mapper, a ta migracja naprawia juz zapisane warianty.

UPDATE app.auction AS a
SET variant = NULLIF(
    btrim(
        regexp_replace(
            a.variant,
            '\s*\d+[,.]?\d*\s*(ccm|cm3|diesel|benzyna|hybryda).*$',
            '',
            'i'
        )
    ),
    ''
)
FROM app.source AS s
WHERE s.id = a.source_id
  AND s.key = 'autoprzetarg'
  AND a.variant ~* '\d+[,.]?\d*\s*(ccm|cm3|diesel|benzyna|hybryda)';
