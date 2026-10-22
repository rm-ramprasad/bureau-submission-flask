-- GENERATED from submissions/canonical/model.yaml (canonical_version 1.0). Do not edit.
-- Regenerate: python -m submissions.canonical.generate --out <this file>
-- Canonical snapshot tables (c_*), snapshot header and UCRF reference data.

CREATE TABLE extract_snapshot (
	snapshot_id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(30) NOT NULL,
	submission_type VARCHAR(20) NOT NULL,
	as_of_date DATE NOT NULL,
	batch_id VARCHAR(40) COMMENT 'Batch that triggered the extract (NULL for ad hoc extracts)',
	canonical_version VARCHAR(20) NOT NULL,
	mapping_version VARCHAR(64) COMMENT 'Hash or version of the tenant mapping used',
	status VARCHAR(12) NOT NULL COMMENT 'BUILDING / EXTRACTED / VALIDATED / FAILED / PURGED',
	build_owner VARCHAR(100) COMMENT 'Worker building the snapshot (build lock)',
	row_counts TEXT COMMENT 'JSON: rows per canonical table',
	error_message TEXT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (snapshot_id),
	CONSTRAINT uq_extract_snapshot_batch UNIQUE (batch_id, tenant_id, submission_type),
	FOREIGN KEY(tenant_id) REFERENCES tenant (tenant_id)
);

CREATE INDEX ix_extract_snapshot_tenant ON extract_snapshot (tenant_id, as_of_date);

CREATE TABLE ref_code (
	code_list VARCHAR(40) NOT NULL,
	code VARCHAR(10) NOT NULL,
	description VARCHAR(200) NOT NULL,
	is_active BOOL NOT NULL,
	PRIMARY KEY (code_list, code)
)COMMENT='UCRF code lists from the canonical model (generated)';

CREATE TABLE ref_state_pin (
	state_code VARCHAR(10) NOT NULL,
	pin_prefix_min INTEGER NOT NULL,
	pin_prefix_max INTEGER NOT NULL,
	PRIMARY KEY (state_code)
)COMMENT='Valid first two PIN digits per state code, spec Appendix C (generated)';

CREATE TABLE c_party (
	row_id BIGINT NOT NULL AUTO_INCREMENT,
	snapshot_id BIGINT NOT NULL,
	party_key VARCHAR(50) COMMENT 'required Source borrower key',
	full_name VARCHAR(200) COMMENT '[PN.01-05] required Full name. Formatter splits into 26-char fields',
	birth_date DATE COMMENT '[PN.07]',
	gender VARCHAR(40) COMMENT '[PN.08] codes: gender',
	occupation VARCHAR(40) COMMENT '[TL.46] codes: occupation',
	income NUMERIC(20, 2) COMMENT '[TL.47]',
	income_basis VARCHAR(40) COMMENT '[TL.48] codes: income_basis',
	income_period VARCHAR(40) COMMENT '[TL.49] codes: income_period',
	PRIMARY KEY (row_id),
	FOREIGN KEY(snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
)COMMENT='One borrower (individual)';

CREATE INDEX ix_c_party_party_key ON c_party (snapshot_id, party_key);

CREATE TABLE c_party_identifier (
	row_id BIGINT NOT NULL AUTO_INCREMENT,
	snapshot_id BIGINT NOT NULL,
	party_key VARCHAR(50) COMMENT 'required',
	id_type VARCHAR(40) COMMENT '[ID.01] required codes: id_type',
	id_number VARCHAR(50) COMMENT '[ID.02] required As held at source. Delimiters stripped by validation/formatter',
	issue_date DATE COMMENT '[ID.03]',
	expiry_date DATE COMMENT '[ID.04]',
	PRIMARY KEY (row_id),
	FOREIGN KEY(snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
)COMMENT='One identity document of a borrower';

CREATE INDEX ix_c_party_identifier_party_key ON c_party_identifier (snapshot_id, party_key);

CREATE TABLE c_party_phone (
	row_id BIGINT NOT NULL AUTO_INCREMENT,
	snapshot_id BIGINT NOT NULL,
	party_key VARCHAR(50) COMMENT 'required',
	phone_number VARCHAR(60) COMMENT '[PT.01] required May hold several numbers when type is 00',
	extension VARCHAR(10) COMMENT '[PT.02]',
	phone_type VARCHAR(40) COMMENT '[PT.03] codes: phone_type',
	priority INTEGER COMMENT '1 = first reported. Lower sorts first',
	PRIMARY KEY (row_id),
	FOREIGN KEY(snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
)COMMENT='One telephone entry of a borrower';

CREATE INDEX ix_c_party_phone_party_key ON c_party_phone (snapshot_id, party_key);

CREATE TABLE c_party_email (
	row_id BIGINT NOT NULL AUTO_INCREMENT,
	snapshot_id BIGINT NOT NULL,
	party_key VARCHAR(50) COMMENT 'required',
	email VARCHAR(100) COMMENT '[EC.01] required',
	priority INTEGER,
	PRIMARY KEY (row_id),
	FOREIGN KEY(snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
)COMMENT='One e-mail address of a borrower';

CREATE INDEX ix_c_party_email_party_key ON c_party_email (snapshot_id, party_key);

CREATE TABLE c_party_address (
	row_id BIGINT NOT NULL AUTO_INCREMENT,
	snapshot_id BIGINT NOT NULL,
	party_key VARCHAR(50) COMMENT 'required',
	address_line_1 VARCHAR(200) COMMENT '[PA.01] required Lines are re-fitted to 40 chars by the formatter without splitting words',
	address_line_2 VARCHAR(200) COMMENT '[PA.02]',
	address_line_3 VARCHAR(200) COMMENT '[PA.03]',
	address_line_4 VARCHAR(200) COMMENT '[PA.04]',
	address_line_5 VARCHAR(200) COMMENT '[PA.05]',
	state_code VARCHAR(40) COMMENT '[PA.06] codes: state_code',
	pin_code VARCHAR(20) COMMENT '[PA.07] As held at source. Non-digits stripped by validation/formatter',
	address_category VARCHAR(40) COMMENT '[PA.08] codes: address_category',
	residence_code VARCHAR(40) COMMENT '[PA.09] codes: residence_code',
	priority INTEGER,
	PRIMARY KEY (row_id),
	FOREIGN KEY(snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
)COMMENT='One address of a borrower';

CREATE INDEX ix_c_party_address_party_key ON c_party_address (snapshot_id, party_key);

CREATE TABLE c_account (
	row_id BIGINT NOT NULL AUTO_INCREMENT,
	snapshot_id BIGINT NOT NULL,
	account_key VARCHAR(50) COMMENT 'required Source account key',
	account_number VARCHAR(50) COMMENT '[TL.03] required',
	account_type VARCHAR(40) COMMENT '[TL.04] required codes: account_type',
	date_opened DATE COMMENT '[TL.08] required',
	date_last_payment DATE COMMENT '[TL.09]',
	date_closed DATE COMMENT '[TL.10]',
	date_reported DATE COMMENT '[TL.11] Defaults to the request as-of date',
	high_credit_amount NUMERIC(20, 2) COMMENT '[TL.12] required Sanctioned amount, or highest credit used for card types',
	current_balance NUMERIC(20, 2) COMMENT '[TL.13] required Signed. Negative = credit balance',
	amount_overdue NUMERIC(20, 2) COMMENT '[TL.14]',
	days_past_due INTEGER COMMENT '[TL.15]',
	suit_filed_status VARCHAR(40) COMMENT '[TL.21] codes: suit_filed_status',
	credit_facility_status VARCHAR(40) COMMENT '[TL.22] codes: credit_facility_status',
	asset_classification VARCHAR(40) COMMENT '[TL.26] codes: asset_classification',
	collateral_value NUMERIC(20, 2) COMMENT '[TL.34]',
	collateral_type VARCHAR(40) COMMENT '[TL.35] codes: collateral_type',
	credit_limit NUMERIC(20, 2) COMMENT '[TL.36]',
	cash_limit NUMERIC(20, 2) COMMENT '[TL.37]',
	rate_of_interest NUMERIC(10, 4) COMMENT '[TL.38]',
	repayment_tenure INTEGER COMMENT '[TL.39] Months',
	emi_amount NUMERIC(20, 2) COMMENT '[TL.40]',
	written_off_total NUMERIC(20, 2) COMMENT '[TL.41]',
	written_off_principal NUMERIC(20, 2) COMMENT '[TL.42]',
	settlement_amount NUMERIC(20, 2) COMMENT '[TL.43]',
	payment_frequency VARCHAR(40) COMMENT '[TL.44] codes: payment_frequency',
	actual_payment_amount NUMERIC(20, 2) COMMENT '[TL.45]',
	old_member_code VARCHAR(10) COMMENT '[TL.16]',
	old_member_short_name VARCHAR(16) COMMENT '[TL.17]',
	old_account_number VARCHAR(50) COMMENT '[TL.18]',
	old_account_type VARCHAR(40) COMMENT '[TL.19] codes: account_type',
	old_ownership_indicator VARCHAR(40) COMMENT '[TL.20] codes: ownership_indicator',
	PRIMARY KEY (row_id),
	FOREIGN KEY(snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
)COMMENT='One credit facility';

CREATE INDEX ix_c_account_account_key ON c_account (snapshot_id, account_key);

CREATE TABLE c_account_holder (
	row_id BIGINT NOT NULL AUTO_INCREMENT,
	snapshot_id BIGINT NOT NULL,
	account_key VARCHAR(50) COMMENT 'required',
	party_key VARCHAR(50) COMMENT 'required',
	ownership_indicator VARCHAR(40) COMMENT '[TL.05] required codes: ownership_indicator',
	PRIMARY KEY (row_id),
	FOREIGN KEY(snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
)COMMENT='Link of a borrower to an account. Each row becomes one UCRF record';

CREATE INDEX ix_c_account_holder_account_key ON c_account_holder (snapshot_id, account_key);

CREATE INDEX ix_c_account_holder_party_key ON c_account_holder (snapshot_id, party_key);

CREATE TABLE c_account_history (
	row_id BIGINT NOT NULL AUTO_INCREMENT,
	snapshot_id BIGINT NOT NULL,
	account_key VARCHAR(50) COMMENT 'required',
	history_month DATE COMMENT '[TH.01] required Any day of the month. Formatter resets the day to 01',
	days_past_due INTEGER COMMENT '[TH.02]',
	asset_classification VARCHAR(40) COMMENT '[TH.02] codes: asset_classification Used when days_past_due is absent',
	amount_overdue NUMERIC(20, 2) COMMENT '[TH.03]',
	high_credit_amount NUMERIC(20, 2) COMMENT '[TH.04]',
	credit_limit NUMERIC(20, 2) COMMENT '[TH.05]',
	cash_limit NUMERIC(20, 2) COMMENT '[TH.06]',
	current_balance NUMERIC(20, 2) COMMENT '[TH.07] required',
	date_last_payment DATE COMMENT '[TH.08]',
	actual_payment_amount NUMERIC(20, 2) COMMENT '[TH.09]',
	PRIMARY KEY (row_id),
	FOREIGN KEY(snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
)COMMENT='One month of account history (initial historical load only)';

CREATE INDEX ix_c_account_history_account_key ON c_account_history (snapshot_id, account_key);

CREATE TABLE c_extension_attribute (
	row_id BIGINT NOT NULL AUTO_INCREMENT,
	snapshot_id BIGINT NOT NULL,
	entity VARCHAR(30) COMMENT 'required party or account',
	entity_key VARCHAR(50) COMMENT 'required',
	attr_name VARCHAR(64) COMMENT 'required',
	attr_value VARCHAR(500),
	PRIMARY KEY (row_id),
	FOREIGN KEY(snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
)COMMENT='NBFC-specific values for custom validation rules. Never written to bureau files';

CREATE INDEX ix_c_extension_attribute_entity_entity_key ON c_extension_attribute (snapshot_id, entity, entity_key);

INSERT INTO ref_code (code_list, code, description, is_active) VALUES ('gender', '1', 'Female', true), ('gender', '2', 'Male', true), ('gender', '3', 'Transgender', true), ('id_type', '01', 'PAN', true), ('id_type', '02', 'Passport', true), ('id_type', '03', 'Voter ID', true), ('id_type', '04', 'Driving Licence', true), ('id_type', '05', 'Ration Card', true), ('id_type', '06', 'UID (Aadhaar)', true), ('id_type', '07', 'Additional ID 1 (future use)', false), ('id_type', '08', 'Additional ID 2 (future use)', false), ('id_type', '09', 'CKYC', true), ('id_type', '10', 'G RAM G', true), ('phone_type', '00', 'Not Classified', true), ('phone_type', '01', 'Mobile', true), ('phone_type', '02', 'Home', true), ('phone_type', '03', 'Office', true), ('address_category', '01', 'Permanent', true), ('address_category', '02', 'Residence', true), ('address_category', '03', 'Office', true), ('address_category', '04', 'Not Categorized', true), ('address_category', '05', 'Mortgage Property', true), ('residence_code', '01', 'Owned', true), ('residence_code', '02', 'Rented', true), ('state_code', '01', 'Jammu & Kashmir', true), ('state_code', '02', 'Himachal Pradesh', true), ('state_code', '03', 'Punjab', true), ('state_code', '04', 'Chandigarh', true), ('state_code', '05', 'Uttarakhand', true), ('state_code', '06', 'Haryana', true), ('state_code', '07', 'Delhi', true), ('state_code', '08', 'Rajasthan', true), ('state_code', '09', 'Uttar Pradesh', true), ('state_code', '10', 'Bihar', true), ('state_code', '11', 'Sikkim', true), ('state_code', '12', 'Arunachal Pradesh', true), ('state_code', '13', 'Nagaland', true), ('state_code', '14', 'Manipur', true), ('state_code', '15', 'Mizoram', true), ('state_code', '16', 'Tripura', true), ('state_code', '17', 'Meghalaya', true), ('state_code', '18', 'Assam', true), ('state_code', '19', 'West Bengal', true), ('state_code', '20', 'Jharkhand', true), ('state_code', '21', 'Orissa', true), ('state_code', '22', 'Chhattisgarh', true), ('state_code', '23', 'Madhya Pradesh', true), ('state_code', '24', 'Gujarat', true), ('state_code', '25', 'Dadra & Nagar Haveli and Daman & Diu', true), ('state_code', '27', 'Maharashtra', true), ('state_code', '28', 'Andhra Pradesh', true), ('state_code', '29', 'Karnataka', true), ('state_code', '30', 'Goa', true), ('state_code', '31', 'Lakshadweep', true), ('state_code', '32', 'Kerala', true), ('state_code', '33', 'Tamil Nadu', true), ('state_code', '34', 'Pondicherry', true), ('state_code', '35', 'Andaman & Nicobar Islands', true), ('state_code', '36', 'Telangana', true), ('state_code', '37', 'Ladakh', true), ('state_code', '77', 'Foreign Address', true), ('state_code', '99', 'APO Address', true), ('account_type', '00', 'Other', true), ('account_type', '01', 'Auto Loan (Personal)', true), ('account_type', '02', 'Housing Loan', true), ('account_type', '03', 'Property Loan', true), ('account_type', '04', 'Loan Against Shares / Securities', true), ('account_type', '05', 'Personal Loan', true), ('account_type', '06', 'Consumer Loan', true), ('account_type', '07', 'Gold Loan', true), ('account_type', '08', 'Education Loan', true), ('account_type', '09', 'Loan to Professional', true), ('account_type', '10', 'Credit Card', true), ('account_type', '11', 'Leasing', true), ('account_type', '12', 'Overdraft', true), ('account_type', '13', 'Two-Wheeler Loan', true), ('account_type', '14', 'Non-Funded Credit Facility', true), ('account_type', '15', 'Loan Against Bank Deposits', true), ('account_type', '16', 'Fleet Card', true), ('account_type', '17', 'Commercial Vehicle Loan', true), ('account_type', '18', 'Telco - Wireless', false), ('account_type', '19', 'Telco - Broadband', false), ('account_type', '20', 'Telco - Landline', false), ('account_type', '21', 'Seller Financing', true), ('account_type', '23', 'GECL Loan Secured', true), ('account_type', '24', 'GECL Loan Unsecured', true), ('account_type', '25', 'Working Capital Loan', true), ('account_type', '26', 'Commercial Equipment Loan', true), ('account_type', '27', 'Agriculture Loan', true), ('account_type', '28', 'Overdraft against FD', true), ('account_type', '29', 'Overdraft against Shares / Securities', true), ('account_type', '30', 'Overdraft against Collateral', true), ('account_type', '31', 'Secured Credit Card', true), ('account_type', '32', 'Used Car Loan', true), ('account_type', '33', 'Construction Equipment Loan', true), ('account_type', '34', 'Tractor Loan', true), ('account_type', '35', 'Corporate Credit Card', true), ('account_type', '36', 'Kisan Credit Card', true), ('account_type', '37', 'Loan on Credit Card', true), ('account_type', '38', 'Prime Minister Jan Dhan Yojana - Overdraft', true), ('account_type', '39', 'Mudra Loans - Shishu / Kishor / Tarun', true), ('account_type', '40', 'Microfinance - Business Loan', true), ('account_type', '41', 'Microfinance - Personal Loan', true), ('account_type', '42', 'Microfinance - Housing Loan', true), ('account_type', '43', 'Microfinance - Others', true), ('account_type', '44', 'Pradhan Mantri Awas Yojana - CLSS', true), ('account_type', '45', 'P2P Personal Loan', true), ('account_type', '46', 'P2P Auto Loan', true), ('account_type', '47', 'P2P Education Loan', true), ('account_type', '50', 'Business Loan - Secured', true), ('account_type', '51', 'Business Loan - General', true), ('account_type', '52', 'Business Loan - Priority Sector - Small Business', true), ('account_type', '53', 'Business Loan - Priority Sector - Agriculture', true), ('account_type', '54', 'Business Loan - Priority Sector - Others', true), ('account_type', '55', 'Business Non-Funded Credit Facility - General', true), ('account_type', '56', 'Business Non-Funded Credit Facility - Priority Sector - Small Business', true), ('account_type', '57', 'Business Non-Funded Credit Facility - Priority Sector - Agriculture', true), ('account_type', '58', 'Business Non-Funded Credit Facility - Priority Sector - Others', true), ('account_type', '59', 'Business Loan Against Bank Deposits', true), ('account_type', '61', 'Business Loan - Unsecured', true), ('account_type', '69', 'Short Term Personal Loan', true), ('account_type', '70', 'Priority Sector - Gold Loan', true), ('account_type', '71', 'Temporary Overdraft', true), ('card_account_type', '10', 'Credit Card', true), ('card_account_type', '16', 'Fleet Card', true), ('card_account_type', '31', 'Secured Credit Card', true), ('card_account_type', '35', 'Corporate Credit Card', true), ('ownership_indicator', '1', 'Individual', true), ('ownership_indicator', '2', 'Authorised User', true), ('ownership_indicator', '3', 'Guarantor', true), ('ownership_indicator', '4', 'Joint', true), ('ownership_indicator', '5', 'Deceased', true), ('suit_filed_status', '00', 'No Suit Filed', true), ('suit_filed_status', '01', 'Suit Filed', true), ('suit_filed_status', '02', 'Wilful Default', true), ('suit_filed_status', '03', 'Suit Filed (Wilful Default)', true), ('credit_facility_status', '00', 'Restructured Loan', true), ('credit_facility_status', '01', 'Restructured Loan (Govt. Mandated)', true), ('credit_facility_status', '02', 'Written-off', true), ('credit_facility_status', '03', 'Settled', true), ('credit_facility_status', '04', 'Post (WO) Settled', true), ('credit_facility_status', '05', 'Account Sold', true), ('credit_facility_status', '06', 'Written Off and Account Sold', true), ('credit_facility_status', '07', 'Account Purchased', true), ('credit_facility_status', '08', 'Account Purchased and Written Off', true), ('credit_facility_status', '09', 'Account Purchased and Settled', true), ('credit_facility_status', '10', 'Account Purchased and Restructured', true), ('credit_facility_status', '11', 'Restructured due to Natural Calamity', true), ('credit_facility_status', '12', 'Restructured due to COVID-19', true), ('credit_facility_status', '13', 'Post Write Off Closed', true), ('credit_facility_status', '14', 'Restructured & Closed', true), ('credit_facility_status', '15', 'Auctioned & Settled', true), ('credit_facility_status', '16', 'Repossessed & Settled', true), ('credit_facility_status', '17', 'Guarantee Invoked', true), ('credit_facility_status', '99', 'Clear existing status', true), ('asset_classification', '01', 'Standard', true), ('asset_classification', '02', 'Substandard', true), ('asset_classification', '03', 'Doubtful', true), ('asset_classification', '04', 'Loss', true), ('asset_classification', '05', 'Special Mention Account', true), ('collateral_type', '00', 'None', true), ('collateral_type', '01', 'Property', true), ('collateral_type', '02', 'Gold', true), ('collateral_type', '03', 'Shares', true), ('collateral_type', '04', 'Savings Account & FD', true), ('collateral_type', '05', 'Multiple Securities', true), ('collateral_type', '06', 'Others', true), ('payment_frequency', '01', 'Weekly', true), ('payment_frequency', '02', 'Fortnightly', true), ('payment_frequency', '03', 'Monthly', true), ('payment_frequency', '04', 'Quarterly', true), ('payment_frequency', '05', 'Bullet', true), ('payment_frequency', '06', 'Daily', true), ('payment_frequency', '07', 'Half-yearly', true), ('payment_frequency', '08', 'Yearly', true), ('payment_frequency', '09', 'On-demand', true), ('occupation', '01', 'Salaried', true), ('occupation', '02', 'Self-Employed Professional', true), ('occupation', '03', 'Self-Employed', true), ('occupation', '04', 'Others', true), ('income_basis', 'G', 'Gross', true), ('income_basis', 'N', 'Net', true), ('income_period', 'M', 'Monthly', true), ('income_period', 'A', 'Annual', true);

INSERT INTO ref_state_pin (state_code, pin_prefix_min, pin_prefix_max) VALUES ('01', 18, 19), ('02', 17, 17), ('03', 14, 16), ('04', 14, 16), ('05', 24, 26), ('06', 12, 13), ('07', 11, 11), ('08', 30, 34), ('09', 20, 28), ('10', 80, 85), ('11', 73, 73), ('12', 78, 79), ('13', 79, 79), ('14', 79, 79), ('15', 79, 79), ('16', 72, 79), ('17', 79, 79), ('18', 78, 79), ('19', 70, 74), ('20', 81, 83), ('21', 75, 77), ('22', 46, 49), ('23', 45, 48), ('24', 36, 39), ('25', 36, 39), ('27', 40, 44), ('28', 50, 56), ('29', 53, 59), ('30', 40, 40), ('31', 67, 68), ('32', 67, 69), ('33', 53, 67), ('34', 53, 67), ('35', 74, 74), ('36', 50, 56), ('37', 18, 19), ('99', 90, 99);
