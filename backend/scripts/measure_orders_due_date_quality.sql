-- Bildirim F2 / Adım A: orders.due_date veri kalitesi ölçümü.
-- PostgreSQL 16 içindir. Yalnız SELECT çalıştırır; veri veya şema değiştirmez.
--
-- Sonuç kümeleri:
--   1. Genel sayılar
--   2. Tenant (company_id) kırılımı
--   3. Parse edilemeyen en fazla 20 farklı ham değer

WITH classified AS (
    SELECT
        company_id,
        due_date,
        CASE
            WHEN btrim(due_date) = '' THEN 'blank'
            WHEN due_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                 AND pg_input_is_valid(due_date, 'date')
                THEN 'valid_iso_date'
            ELSE 'unparseable'
        END AS quality
    FROM orders
    WHERE due_date IS NOT NULL
)
SELECT
    COUNT(*) AS total_non_null_due_date,
    COUNT(*) FILTER (WHERE quality = 'valid_iso_date') AS valid_yyyy_mm_dd,
    COUNT(*) FILTER (WHERE quality = 'blank') AS blank_or_whitespace,
    COUNT(*) FILTER (WHERE quality = 'unparseable') AS unparseable
FROM classified;

WITH classified AS (
    SELECT
        company_id,
        due_date,
        CASE
            WHEN btrim(due_date) = '' THEN 'blank'
            WHEN due_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                 AND pg_input_is_valid(due_date, 'date')
                THEN 'valid_iso_date'
            ELSE 'unparseable'
        END AS quality
    FROM orders
    WHERE due_date IS NOT NULL
)
SELECT
    company_id,
    COUNT(*) AS total_non_null_due_date,
    COUNT(*) FILTER (WHERE quality = 'valid_iso_date') AS valid_yyyy_mm_dd,
    COUNT(*) FILTER (WHERE quality = 'blank') AS blank_or_whitespace,
    COUNT(*) FILTER (WHERE quality = 'unparseable') AS unparseable
FROM classified
GROUP BY company_id
ORDER BY unparseable DESC, blank_or_whitespace DESC, company_id;

WITH classified AS (
    SELECT
        company_id,
        due_date,
        CASE
            WHEN btrim(due_date) = '' THEN 'blank'
            WHEN due_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                 AND pg_input_is_valid(due_date, 'date')
                THEN 'valid_iso_date'
            ELSE 'unparseable'
        END AS quality
    FROM orders
    WHERE due_date IS NOT NULL
),
unparseable_values AS (
    SELECT
        due_date,
        COUNT(*) AS row_count,
        COUNT(DISTINCT company_id) AS tenant_count
    FROM classified
    WHERE quality = 'unparseable'
    GROUP BY due_date
)
SELECT
    due_date AS sample_value,
    row_count,
    tenant_count
FROM unparseable_values
ORDER BY row_count DESC, due_date
LIMIT 20;
