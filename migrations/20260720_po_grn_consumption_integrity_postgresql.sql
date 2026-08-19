-- PO/GRN ledger integrity and stable business invoice identity.
-- Apply transactionally after taking a database backup.

BEGIN;

ALTER TABLE po_grn_consumption_ledger
    ADD COLUMN IF NOT EXISTS business_invoice_key VARCHAR(255),
    ADD COLUMN IF NOT EXISTS company_code VARCHAR(20),
    ADD COLUMN IF NOT EXISTS fiscal_year INTEGER;
ALTER TABLE po_grn_consumption_ledger
    ALTER COLUMN active_key TYPE VARCHAR(500);

DELETE FROM po_grn_consumption_ledger ledger
WHERE NOT EXISTS (
    SELECT 1 FROM invoices invoice WHERE invoice.id = ledger.invoice_id
);

UPDATE po_grn_consumption_ledger ledger
SET company_code = COALESCE(ledger.company_code, '1000'),
    fiscal_year = COALESCE(
        ledger.fiscal_year,
        EXTRACT(YEAR FROM invoice.invoice_date)::INTEGER
    ),
    business_invoice_key = COALESCE(
        ledger.business_invoice_key,
        regexp_replace(UPPER(COALESCE('1000', '')), '[^A-Z0-9]', '', 'g')
        || '|' ||
        regexp_replace(
            UPPER(COALESCE(invoice.vendor_number, invoice.vendor_name, 'UNKNOWN')),
            '[^A-Z0-9]', '', 'g'
        )
        || '|' ||
        regexp_replace(UPPER(ledger.invoice_number), '[^A-Z0-9]', '', 'g')
        || '|' ||
        EXTRACT(YEAR FROM invoice.invoice_date)::INTEGER::TEXT
    )
FROM invoices invoice
WHERE invoice.id = ledger.invoice_id;

WITH ranked_reservations AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY business_invoice_key, po_number, po_item
               ORDER BY updated_at DESC, created_at DESC, id DESC
           ) AS reservation_rank
    FROM po_grn_consumption_ledger
    WHERE ledger_status = 'RESERVED'
)
UPDATE po_grn_consumption_ledger ledger
SET ledger_status = 'RELEASED',
    active_key = NULL,
    reason = 'Released by business-key migration: duplicate active reservation.'
FROM ranked_reservations ranked
WHERE ledger.id = ranked.id
  AND ranked.reservation_rank > 1;

ALTER TABLE po_grn_consumption_ledger
    ALTER COLUMN business_invoice_key SET NOT NULL,
    ALTER COLUMN company_code SET NOT NULL,
    ALTER COLUMN fiscal_year SET NOT NULL;

ALTER TABLE po_grn_consumption_ledger
    DROP CONSTRAINT IF EXISTS po_grn_consumption_ledger_invoice_id_fkey;
ALTER TABLE po_grn_consumption_ledger
    ADD CONSTRAINT po_grn_consumption_ledger_invoice_id_fkey
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS ix_po_grn_consumption_ledger_po_number
    ON po_grn_consumption_ledger (po_number);
CREATE INDEX IF NOT EXISTS ix_po_grn_consumption_ledger_invoice_id
    ON po_grn_consumption_ledger (invoice_id);
CREATE INDEX IF NOT EXISTS ix_po_grn_ledger_business_invoice_key
    ON po_grn_consumption_ledger (business_invoice_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_po_grn_active_business_reservation
    ON po_grn_consumption_ledger
    (business_invoice_key, po_number, po_item)
    WHERE ledger_status = 'RESERVED';

COMMIT;
