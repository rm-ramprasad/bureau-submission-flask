-- GENERATED from the tenant mapping (version 69d7cfb86c407738). Do not edit.
SELECT  -- party_address.address1
    (b.customer_id) AS party_key,
    (b.address1_line1) AS address_line_1,
    (b.address1_line2) AS address_line_2,
    (b.address1_line3) AS address_line_3,
    (b.address1_city) AS address_line_4,
    NULL AS address_line_5,
    CASE (b.address1_state) WHEN 'Jammu & Kashmir' THEN '01' WHEN 'Himachal Pradesh' THEN '02' WHEN 'Punjab' THEN '03' WHEN 'Chandigarh' THEN '04' WHEN 'Uttarakhand' THEN '05' WHEN 'Haryana' THEN '06' WHEN 'Delhi' THEN '07' WHEN 'Rajasthan' THEN '08' WHEN 'Uttar Pradesh' THEN '09' WHEN 'Bihar' THEN '10' WHEN 'Sikkim' THEN '11' WHEN 'Arunachal Pradesh' THEN '12' WHEN 'Nagaland' THEN '13' WHEN 'Manipur' THEN '14' WHEN 'Mizoram' THEN '15' WHEN 'Tripura' THEN '16' WHEN 'Meghalaya' THEN '17' WHEN 'Assam' THEN '18' WHEN 'West Bengal' THEN '19' WHEN 'Jharkhand' THEN '20' WHEN 'Odisha' THEN '21' WHEN 'Orissa' THEN '21' WHEN 'Chhattisgarh' THEN '22' WHEN 'Madhya Pradesh' THEN '23' WHEN 'Gujarat' THEN '24' WHEN 'Maharashtra' THEN '27' WHEN 'Andhra Pradesh' THEN '28' WHEN 'Karnataka' THEN '29' WHEN 'Goa' THEN '30' WHEN 'Kerala' THEN '32' WHEN 'Tamil Nadu' THEN '33' WHEN 'Puducherry' THEN '34' WHEN 'Telangana' THEN '36' WHEN 'Ladakh' THEN '37' WHEN 'Foreign' THEN '77' WHEN 'APO' THEN '99' ELSE (b.address1_state) END AS state_code,
    (b.address1_pin_code) AS pin_code,
    COALESCE(CASE (b.address1_type) WHEN 'PERM' THEN '01' WHEN 'RES' THEN '02' WHEN 'OFF' THEN '03' WHEN 'OTH' THEN '04' WHEN 'MORT' THEN '05' ELSE (b.address1_type) END, '04') AS address_category,
    CASE (b.address1_tenure) WHEN 'OWN' THEN '01' WHEN 'RENT' THEN '02' ELSE (b.address1_tenure) END AS residence_code,
    (1) AS priority
FROM nbfc_002.borrower_demographics b
WHERE b.address1_line1 IS NOT NULL
UNION ALL
SELECT  -- party_address.address2
    (b.customer_id) AS party_key,
    (b.address2_line1) AS address_line_1,
    (b.address2_line2) AS address_line_2,
    (b.address2_line3) AS address_line_3,
    (b.address2_city) AS address_line_4,
    NULL AS address_line_5,
    CASE (b.address2_state) WHEN 'Jammu & Kashmir' THEN '01' WHEN 'Himachal Pradesh' THEN '02' WHEN 'Punjab' THEN '03' WHEN 'Chandigarh' THEN '04' WHEN 'Uttarakhand' THEN '05' WHEN 'Haryana' THEN '06' WHEN 'Delhi' THEN '07' WHEN 'Rajasthan' THEN '08' WHEN 'Uttar Pradesh' THEN '09' WHEN 'Bihar' THEN '10' WHEN 'Sikkim' THEN '11' WHEN 'Arunachal Pradesh' THEN '12' WHEN 'Nagaland' THEN '13' WHEN 'Manipur' THEN '14' WHEN 'Mizoram' THEN '15' WHEN 'Tripura' THEN '16' WHEN 'Meghalaya' THEN '17' WHEN 'Assam' THEN '18' WHEN 'West Bengal' THEN '19' WHEN 'Jharkhand' THEN '20' WHEN 'Odisha' THEN '21' WHEN 'Orissa' THEN '21' WHEN 'Chhattisgarh' THEN '22' WHEN 'Madhya Pradesh' THEN '23' WHEN 'Gujarat' THEN '24' WHEN 'Maharashtra' THEN '27' WHEN 'Andhra Pradesh' THEN '28' WHEN 'Karnataka' THEN '29' WHEN 'Goa' THEN '30' WHEN 'Kerala' THEN '32' WHEN 'Tamil Nadu' THEN '33' WHEN 'Puducherry' THEN '34' WHEN 'Telangana' THEN '36' WHEN 'Ladakh' THEN '37' WHEN 'Foreign' THEN '77' WHEN 'APO' THEN '99' ELSE (b.address2_state) END AS state_code,
    (b.address2_pin_code) AS pin_code,
    COALESCE(CASE (b.address2_type) WHEN 'PERM' THEN '01' WHEN 'RES' THEN '02' WHEN 'OFF' THEN '03' WHEN 'OTH' THEN '04' WHEN 'MORT' THEN '05' ELSE (b.address2_type) END, '04') AS address_category,
    CASE (b.address2_tenure) WHEN 'OWN' THEN '01' WHEN 'RENT' THEN '02' ELSE (b.address2_tenure) END AS residence_code,
    (2) AS priority
FROM nbfc_002.borrower_demographics b
WHERE b.address2_line1 IS NOT NULL
UNION ALL
SELECT  -- party_address.address3
    (b.customer_id) AS party_key,
    (b.address3_line1) AS address_line_1,
    (b.address3_line2) AS address_line_2,
    (b.address3_line3) AS address_line_3,
    (b.address3_city) AS address_line_4,
    NULL AS address_line_5,
    CASE (b.address3_state) WHEN 'Jammu & Kashmir' THEN '01' WHEN 'Himachal Pradesh' THEN '02' WHEN 'Punjab' THEN '03' WHEN 'Chandigarh' THEN '04' WHEN 'Uttarakhand' THEN '05' WHEN 'Haryana' THEN '06' WHEN 'Delhi' THEN '07' WHEN 'Rajasthan' THEN '08' WHEN 'Uttar Pradesh' THEN '09' WHEN 'Bihar' THEN '10' WHEN 'Sikkim' THEN '11' WHEN 'Arunachal Pradesh' THEN '12' WHEN 'Nagaland' THEN '13' WHEN 'Manipur' THEN '14' WHEN 'Mizoram' THEN '15' WHEN 'Tripura' THEN '16' WHEN 'Meghalaya' THEN '17' WHEN 'Assam' THEN '18' WHEN 'West Bengal' THEN '19' WHEN 'Jharkhand' THEN '20' WHEN 'Odisha' THEN '21' WHEN 'Orissa' THEN '21' WHEN 'Chhattisgarh' THEN '22' WHEN 'Madhya Pradesh' THEN '23' WHEN 'Gujarat' THEN '24' WHEN 'Maharashtra' THEN '27' WHEN 'Andhra Pradesh' THEN '28' WHEN 'Karnataka' THEN '29' WHEN 'Goa' THEN '30' WHEN 'Kerala' THEN '32' WHEN 'Tamil Nadu' THEN '33' WHEN 'Puducherry' THEN '34' WHEN 'Telangana' THEN '36' WHEN 'Ladakh' THEN '37' WHEN 'Foreign' THEN '77' WHEN 'APO' THEN '99' ELSE (b.address3_state) END AS state_code,
    (b.address3_pin_code) AS pin_code,
    COALESCE(CASE (b.address3_type) WHEN 'PERM' THEN '01' WHEN 'RES' THEN '02' WHEN 'OFF' THEN '03' WHEN 'OTH' THEN '04' WHEN 'MORT' THEN '05' ELSE (b.address3_type) END, '04') AS address_category,
    CASE (b.address3_tenure) WHEN 'OWN' THEN '01' WHEN 'RENT' THEN '02' ELSE (b.address3_tenure) END AS residence_code,
    (3) AS priority
FROM nbfc_002.borrower_demographics b
WHERE b.address3_line1 IS NOT NULL;
