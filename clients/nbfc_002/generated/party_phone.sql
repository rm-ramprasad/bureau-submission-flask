-- GENERATED from the tenant mapping (version 69d7cfb86c407738). Do not edit.
SELECT  -- party_phone.phone1
    (b.customer_id) AS party_key,
    (b.primary_mobile) AS phone_number,
    (b.phone1_extension) AS extension,
    CASE (b.phone1_type) WHEN 'MOBILE' THEN '01' WHEN 'HOME' THEN '02' WHEN 'OFFICE' THEN '03' WHEN 'OTHER' THEN '00' ELSE (b.phone1_type) END AS phone_type,
    (1) AS priority
FROM nbfc_002.borrower_demographics b
WHERE b.primary_mobile IS NOT NULL
UNION ALL
SELECT  -- party_phone.phone2
    (b.customer_id) AS party_key,
    (b.phone2_number) AS phone_number,
    (b.phone2_extension) AS extension,
    CASE (b.phone2_type) WHEN 'MOBILE' THEN '01' WHEN 'HOME' THEN '02' WHEN 'OFFICE' THEN '03' WHEN 'OTHER' THEN '00' ELSE (b.phone2_type) END AS phone_type,
    (2) AS priority
FROM nbfc_002.borrower_demographics b
WHERE b.phone2_number IS NOT NULL
UNION ALL
SELECT  -- party_phone.phone3
    (b.customer_id) AS party_key,
    (b.phone3_number) AS phone_number,
    (b.phone3_extension) AS extension,
    CASE (b.phone3_type) WHEN 'MOBILE' THEN '01' WHEN 'HOME' THEN '02' WHEN 'OFFICE' THEN '03' WHEN 'OTHER' THEN '00' ELSE (b.phone3_type) END AS phone_type,
    (3) AS priority
FROM nbfc_002.borrower_demographics b
WHERE b.phone3_number IS NOT NULL
UNION ALL
SELECT  -- party_phone.phone4
    (b.customer_id) AS party_key,
    (b.phone4_number) AS phone_number,
    (b.phone4_extension) AS extension,
    CASE (b.phone4_type) WHEN 'MOBILE' THEN '01' WHEN 'HOME' THEN '02' WHEN 'OFFICE' THEN '03' WHEN 'OTHER' THEN '00' ELSE (b.phone4_type) END AS phone_type,
    (4) AS priority
FROM nbfc_002.borrower_demographics b
WHERE b.phone4_number IS NOT NULL;
