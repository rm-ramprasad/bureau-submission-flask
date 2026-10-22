-- 009: custom rules built on the Rules & policies page (no SQL). The form's definition is kept as JSON next to
-- the SQL it compiles to, so the rule can be shown in plain words and edited. Hand-written rules leave it NULL.

ALTER TABLE validation_rule
    ADD COLUMN definition TEXT,
    ADD COLUMN created_by VARCHAR(100);
