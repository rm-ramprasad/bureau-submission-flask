-- GENERATED from the tenant mapping (version 8b6af6641b3d903b). Do not edit.
SELECT  -- account_holder.main
    (a.loan_id) AS account_key,
    (a.customer_id) AS party_key,
    CASE (CASE WHEN a.party_role = 'PRIMARY' AND EXISTS (SELECT 1 FROM nbfc_001.account_details j WHERE j.loan_id = a.loan_id AND j.party_role = 'JOINT') THEN 'JOINT' ELSE a.party_role END) WHEN 'PRIMARY' THEN '1' WHEN 'ADDON' THEN '2' WHEN 'GUARANTOR' THEN '3' WHEN 'JOINT' THEN '4' WHEN 'DECEASED' THEN '5' ELSE (CASE WHEN a.party_role = 'PRIMARY' AND EXISTS (SELECT 1 FROM nbfc_001.account_details j WHERE j.loan_id = a.loan_id AND j.party_role = 'JOINT') THEN 'JOINT' ELSE a.party_role END) END AS ownership_indicator
FROM nbfc_001.account_details a
WHERE a.disbursement_date <= :as_of_date;
