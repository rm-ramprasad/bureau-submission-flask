-- GENERATED from the tenant mapping (version 69d7cfb86c407738). Do not edit.
SELECT  -- extension_attribute.main
    ('account') AS entity,
    (x.loan_id) AS entity_key,
    ('scheme_code') AS attr_name,
    (x.scheme_code) AS attr_value
FROM nbfc_002.loan_ext x
WHERE x.scheme_code IS NOT NULL;
