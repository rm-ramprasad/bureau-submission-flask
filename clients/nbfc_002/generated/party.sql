-- GENERATED from the tenant mapping (version 69d7cfb86c407738). Do not edit.
SELECT  -- party.main
    (b.customer_id) AS party_key,
    (CONCAT_WS(' ', b.first_name, b.middle_name, b.last_name)) AS full_name,
    (b.dob) AS birth_date,
    CASE (b.sex) WHEN 'M' THEN '2' WHEN 'F' THEN '1' WHEN 'T' THEN '3' WHEN 'MALE' THEN '2' WHEN 'FEMALE' THEN '1' WHEN 'TRANSGENDER' THEN '3' ELSE (b.sex) END AS gender,
    CASE (b.occupation) WHEN 'SALARIED' THEN '01' WHEN 'PROFESSIONAL' THEN '02' WHEN 'SELF_EMPLOYED' THEN '03' WHEN 'OTHER' THEN '04' ELSE (b.occupation) END AS occupation,
    (b.income_amount) AS income,
    CASE (b.income_basis) WHEN 'GROSS' THEN 'G' WHEN 'NET' THEN 'N' ELSE (b.income_basis) END AS income_basis,
    CASE (b.income_period) WHEN 'MONTHLY' THEN 'M' WHEN 'ANNUAL' THEN 'A' ELSE (b.income_period) END AS income_period
FROM nbfc_002.borrower_demographics b;
