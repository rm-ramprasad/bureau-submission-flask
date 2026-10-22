-- GENERATED from the tenant mapping (version 8b6af6641b3d903b). Do not edit.
SELECT  -- party_email.email1
    (b.customer_id) AS party_key,
    (b.email1) AS email,
    (1) AS priority
FROM nbfc_001.borrower_demographics b
WHERE b.email1 IS NOT NULL
UNION ALL
SELECT  -- party_email.email2
    (b.customer_id) AS party_key,
    (b.email2) AS email,
    (2) AS priority
FROM nbfc_001.borrower_demographics b
WHERE b.email2 IS NOT NULL
UNION ALL
SELECT  -- party_email.email3
    (b.customer_id) AS party_key,
    (b.email3) AS email,
    (3) AS priority
FROM nbfc_001.borrower_demographics b
WHERE b.email3 IS NOT NULL;
