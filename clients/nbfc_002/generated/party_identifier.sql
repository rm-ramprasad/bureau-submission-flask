-- GENERATED from the tenant mapping (version 69d7cfb86c407738). Do not edit.
SELECT  -- party_identifier.pan
    (b.customer_id) AS party_key,
    ('01') AS id_type,
    (b.pan_number) AS id_number,
    NULL AS issue_date,
    NULL AS expiry_date
FROM nbfc_002.borrower_demographics b
WHERE b.pan_number IS NOT NULL
UNION ALL
SELECT  -- party_identifier.passport
    (b.customer_id) AS party_key,
    ('02') AS id_type,
    (b.passport_number) AS id_number,
    (b.passport_issue_date) AS issue_date,
    (b.passport_expiry_date) AS expiry_date
FROM nbfc_002.borrower_demographics b
WHERE b.passport_number IS NOT NULL
UNION ALL
SELECT  -- party_identifier.voter_id
    (b.customer_id) AS party_key,
    ('03') AS id_type,
    (b.voter_id_number) AS id_number,
    (b.voter_id_issue_date) AS issue_date,
    NULL AS expiry_date
FROM nbfc_002.borrower_demographics b
WHERE b.voter_id_number IS NOT NULL
UNION ALL
SELECT  -- party_identifier.driving_licence
    (b.customer_id) AS party_key,
    ('04') AS id_type,
    (b.driving_license_number) AS id_number,
    (b.driving_license_issue_date) AS issue_date,
    (b.driving_license_expiry_date) AS expiry_date
FROM nbfc_002.borrower_demographics b
WHERE b.driving_license_number IS NOT NULL
UNION ALL
SELECT  -- party_identifier.ration_card
    (b.customer_id) AS party_key,
    ('05') AS id_type,
    (b.ration_card_number) AS id_number,
    NULL AS issue_date,
    NULL AS expiry_date
FROM nbfc_002.borrower_demographics b
WHERE b.ration_card_number IS NOT NULL
UNION ALL
SELECT  -- party_identifier.aadhaar
    (b.customer_id) AS party_key,
    ('06') AS id_type,
    (b.aadhaar_number) AS id_number,
    NULL AS issue_date,
    NULL AS expiry_date
FROM nbfc_002.borrower_demographics b
WHERE b.aadhaar_number IS NOT NULL
UNION ALL
SELECT  -- party_identifier.ckyc
    (b.customer_id) AS party_key,
    ('09') AS id_type,
    (b.ckyc_number) AS id_number,
    NULL AS issue_date,
    NULL AS expiry_date
FROM nbfc_002.borrower_demographics b
WHERE b.ckyc_number IS NOT NULL
UNION ALL
SELECT  -- party_identifier.gram_g
    (b.customer_id) AS party_key,
    ('10') AS id_type,
    (b.gram_g_id) AS id_number,
    NULL AS issue_date,
    NULL AS expiry_date
FROM nbfc_002.borrower_demographics b
WHERE b.gram_g_id IS NOT NULL;
