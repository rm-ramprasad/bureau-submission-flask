"""Mock LMS source data: two NBFC schemas with the same 52 scenarios, different column layouts.

  nbfc_001  base LMS layout:   borrower_demographics, account_details
  nbfc_002  customised layout: renamed columns, long product/gender codes, DPD moved to loan_ext,
            plus a custom scheme_code field (exercises the per-NBFC mapping override)

Covers every UCRF consumer data element plus deliberate negative cases. Each account row carries the
expected pipeline outcome in its test_* columns (the oracle for validation tests).

Usage:
  py mock_data/generate_mock_data.py                 # writes mock_data/mock_lms.sql (MySQL)
  py mock_data/generate_mock_data.py --load <url>    # also loads both schemas into that server
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import (
    Column, Date, Engine, Integer, MetaData, Numeric, PrimaryKeyConstraint, String, Table, create_engine,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

SEED = 374
AS_OF = date(2026, 9, 30)
MEMBER_ID = "NB01234567"
MEMBER_SHORT = "DEMO NBFC"

rnd = random.Random(SEED)

# ------------------------------------------------------------------ reference pools
FIRST_M = ["Rajesh", "Amit", "Suresh", "Vikram", "Arjun", "Rahul", "Sanjay", "Manoj", "Karthik", "Pradeep",
           "Anil", "Deepak", "Harish", "Naveen", "Ravi", "Imran", "Joseph", "Gurpreet"]
FIRST_F = ["Priya", "Anjali", "Sunita", "Kavita", "Lakshmi", "Meena", "Pooja", "Divya", "Fatima", "Neha",
           "Shalini", "Rekha", "Asha", "Mary", "Harpreet"]
MIDDLE = ["Kumar", "Prasad", "Devi", "Mohan", "Chandra", "Lal", "Nath", None, None, None]
LAST = ["Sharma", "Patel", "Reddy", "Iyer", "Nair", "Singh", "Gupta", "Das", "Khan", "Menon", "Joshi",
        "Rao", "Pillai", "Mehta", "Banerjee", "Verma", "Kulkarni", "Fernandes", "Choudhary", "Agarwal"]

# (city, client state spelling, PIN prefix 3 digits, STD code, DL/vehicle prefix)
CITIES = [
    ("Mumbai", "Maharashtra", "400", "022", "MH01"), ("Pune", "Maharashtra", "411", "020", "MH12"),
    ("Bengaluru", "Karnataka", "560", "080", "KA01"), ("Chennai", "Tamil Nadu", "600", "044", "TN09"),
    ("Hyderabad", "Telangana", "500", "040", "TS09"), ("New Delhi", "Delhi", "110", "011", "DL03"),
    ("Kolkata", "West Bengal", "700", "033", "WB02"), ("Ahmedabad", "Gujarat", "380", "079", "GJ01"),
    ("Jaipur", "Rajasthan", "302", "0141", "RJ14"), ("Lucknow", "Uttar Pradesh", "226", "0522", "UP32"),
    ("Kochi", "Kerala", "682", "0484", "KL07"), ("Patna", "Bihar", "800", "0612", "BR01"),
    ("Bhopal", "Madhya Pradesh", "462", "0755", "MP04"), ("Chandigarh", "Chandigarh", "160", "0172", "CH01"),
    ("Guwahati", "Assam", "781", "0361", "AS01"), ("Bhubaneswar", "Odisha", "751", "0674", "OD02"),
    ("Panaji", "Goa", "403", "0832", "GA01"), ("Ranchi", "Jharkhand", "834", "0651", "JH01"),
    ("Dehradun", "Uttarakhand", "248", "0135", "UK07"), ("Gurugram", "Haryana", "122", "0124", "HR26"),
    ("Ludhiana", "Punjab", "141", "0161", "PB10"), ("Shimla", "Himachal Pradesh", "171", "0177", "HP03"),
    ("Raipur", "Chhattisgarh", "492", "0771", "CG04"), ("Srinagar", "Jammu & Kashmir", "190", "0194", "JK01"),
]
STREETS = ["MG Road", "Station Road", "Gandhi Nagar", "Nehru Street", "Park Avenue", "Lake View Road",
           "Temple Street", "Market Road", "Civil Lines", "Ring Road"]
AREAS = ["Sector 4", "Shivaji Nagar", "Indira Colony", "Old Town", "Green Park", "Vasant Vihar", "Anna Nagar",
         "Salt Lake", "Banjara Hills", "Koramangala"]
EMAIL_DOMAINS = ["gmail.com", "yahoo.co.in", "outlook.com", "rediffmail.com", "hotmail.com"]

PRODUCTS = {  # client product code -> (kind, secured collateral)
    "HL": ("term", "PROPERTY"), "LAP": ("term", "PROPERTY"), "PL": ("term", "NONE"), "AL": ("term", "OTHER"),
    "UCL": ("term", "OTHER"), "TWL": ("term", "OTHER"), "CVL": ("term", "OTHER"), "GL": ("term", "GOLD"),
    "EDU": ("term", "NONE"), "CDL": ("term", "NONE"), "BLU": ("term", "NONE"), "BLS": ("term", "PROPERTY"),
    "MFP": ("term", "NONE"), "MFB": ("term", "NONE"), "KCC": ("term", "OTHER"), "LAS": ("term", "SHARES"),
    "LOC": ("term", "NONE"), "CC": ("card", "NONE"), "SCC": ("card", "DEPOSIT"), "CCC": ("card", "NONE"),
    "FLC": ("card", "NONE"), "OD": ("od", "DEPOSIT"),
}


# ------------------------------------------------------------------ generators for IDs
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return str(_VERHOEFF_INV[c])


def gen_aadhaar(valid: bool = True) -> str:
    body = str(rnd.randint(2, 9)) + "".join(str(rnd.randint(0, 9)) for _ in range(10))
    chk = verhoeff_check_digit(body)
    if not valid:
        chk = str((int(chk) + 1) % 10)
    n = body + chk
    return f"{n[:4]} {n[4:8]} {n[8:]}"  # stored with spaces, adapter/core strips


def gen_pan(last: str, holder: str = "P") -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return ("".join(rnd.choice(letters) for _ in range(3)) + holder + last[0].upper()
            + f"{rnd.randint(0, 9999):04d}" + rnd.choice(letters))


def gen_mobile() -> str:
    while True:
        n = str(rnd.choice("6789")) + "".join(str(rnd.randint(0, 9)) for _ in range(9))
        if len(set(n)) > 3:
            return n


def rand_date(start: date, end: date) -> date:
    return start + timedelta(days=rnd.randint(0, (end - start).days))


# ------------------------------------------------------------------ customer builder
@dataclass
class Customer:
    customer_id: str
    first_name: str | None
    middle_name: str | None
    last_name: str | None
    birth_date: date | None
    sex: str | None
    occupation: str | None = None
    income_amount: float | None = None
    income_basis: str | None = None
    income_period: str | None = None
    kyc: list[tuple] = field(default_factory=list)       # (type, no, issue, expiry)
    contacts: list[tuple] = field(default_factory=list)  # (type, no, ext, primary)
    emails: list[tuple] = field(default_factory=list)    # (email, primary)
    addresses: list[tuple] = field(default_factory=list)


_cust_seq = 0


def new_customer(*, sex: str | None = None, name: tuple | None = None, ids: tuple = ("PAN", "AADHAAR"),
                 phones: int = 1, landline: bool = False, emails: int = 1, addresses: int = 1,
                 city: tuple | None = None, dob: date | None | str = "auto") -> Customer:
    global _cust_seq
    _cust_seq += 1
    sex = sex or rnd.choice("MMMF")
    if name is None:
        first = rnd.choice(FIRST_M if sex == "M" else FIRST_F)
        mid = rnd.choice(MIDDLE)
        if mid == "Devi" and sex == "M":
            mid = "Kumar"
        name = (first, mid, rnd.choice(LAST))
    birth = rand_date(date(1958, 1, 1), date(2002, 12, 31)) if dob == "auto" else dob
    occ = rnd.choice(["SALARIED", "SALARIED", "PROFESSIONAL", "SELF_EMPLOYED", "OTHER"])
    c = Customer(f"C{_cust_seq:05d}", *name, birth, sex, occ,
                 rnd.choice([35000, 52000, 78000, 120000, 240000]) if occ == "SALARIED" else rnd.choice([600000, 1200000, 2500000]),
                 rnd.choice(["GROSS", "NET"]), "MONTHLY" if occ == "SALARIED" else "ANNUAL")
    city = city or rnd.choice(CITIES)
    last = name[2] or name[0]
    for t in ids:
        issue = rand_date(date(2008, 1, 1), date(2022, 12, 31))
        if t == "PAN":
            c.kyc.append(("PAN", gen_pan(last), None, None))
        elif t == "AADHAAR":
            c.kyc.append(("AADHAAR", gen_aadhaar(), None, None))
        elif t == "PASSPORT":
            c.kyc.append(("PASSPORT", rnd.choice("JKLMNPRSTVZ") + f"{rnd.randint(1000000, 9999999)}",
                          issue, date(issue.year + 10, issue.month, min(issue.day, 28)) - timedelta(days=1)))
        elif t == "VOTER":
            c.kyc.append(("VOTER", "".join(rnd.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(3))
                          + f"{rnd.randint(0, 9999999):07d}", issue, None))
        elif t == "DL":
            c.kyc.append(("DL", f"{city[4]} {issue.year}{rnd.randint(0, 9999999):07d}", issue,
                          date(issue.year + 20, issue.month, min(issue.day, 28))))
        elif t == "RATION":
            c.kyc.append(("RATION", f"{rnd.randint(10**11, 10**12 - 1)}", issue, None))
        elif t == "CKYC":
            c.kyc.append(("CKYC", f"{rnd.randint(10**13, 10**14 - 1)}", None, None))
        elif t == "GRAMG":
            c.kyc.append(("GRAMG", f"{city[4][:2]}-{rnd.randint(10, 40)}-{rnd.randint(100, 999)}-"
                                   f"{rnd.randint(100, 999)}-{rnd.randint(100, 999)}/{rnd.randint(100, 999)}{last}",
                          None, None))
    for i in range(phones):
        c.contacts.append(("MOBILE", gen_mobile(), None, int(i == 0)))
    if landline:
        c.contacts.append(("OFFICE", f"{city[3]}-{rnd.randint(2000000, 2999999)}", str(rnd.randint(100, 999)), 0))
    for i in range(emails):
        handle = f"{name[0]}.{(name[2] or 'x').split()[-1]}{rnd.randint(1, 99) if i else ''}".lower()
        c.emails.append((f"{handle}@{EMAIL_DOMAINS[(i + _cust_seq) % len(EMAIL_DOMAINS)]}", int(i == 0)))
    types = ["PERM", "RES", "OFF", "MORT", "OTH"]
    for i in range(addresses):
        cty = city if i == 0 else rnd.choice(CITIES)
        c.addresses.append((types[i], f"Flat {rnd.randint(1, 999)}, {rnd.choice(['Sai', 'Shanti', 'Lotus', 'Royal'])} Residency",
                            f"{rnd.randint(1, 120)}, {rnd.choice(STREETS)}", rnd.choice(AREAS), cty[0], cty[1],
                            f"{cty[2]}{rnd.randint(1, 99):03d}", "IN", rnd.choice(["OWN", "RENT"]), int(i == 0)))
    return c


# ------------------------------------------------------------------ loan builder
_loan_seq = 0


def new_loan(product: str, *, disb: date | None = None, **over) -> dict:
    global _loan_seq
    _loan_seq += 1
    kind, coll = PRODUCTS[product]
    disb = disb or rand_date(date(2016, 1, 1), date(2026, 3, 31))
    branch = f"B{rnd.randint(101, 140)}"
    L = dict(loan_id=f"L{_loan_seq:05d}", loan_number=f"{branch}{product}{rnd.randint(10**7, 10**8 - 1)}",
             branch_code=branch, product_code=product, member_code=MEMBER_ID, member_short_name=MEMBER_SHORT,
             disbursement_date=disb, reported_date=AS_OF, closed_date=None, days_past_due=0, asset_classification="STD", suit_status="NONE",
             facility_status=None, collateral_type=coll, collateral_value=None, credit_limit=None,
             cash_limit=None, interest_rate_percent=None, tenure_months=None, emi_amount=None, writeoff_total_amount=None,
             writeoff_principal_amount=None, settlement_amount=None, pay_frequency="MONTHLY", overdue_amount=0,
             previous_member_code=None, previous_member_short_name=None, previous_loan_number=None, previous_product_code=None,
             previous_party_role=None)
    if kind == "term":
        sanction = rnd.choice([50000, 150000, 300000, 750000, 1500000, 3500000, 6000000])
        if product in ("MFP", "MFB"):
            sanction = rnd.choice([25000, 40000, 60000])
        tenure = rnd.choice([12, 24, 36, 60, 120, 240]) if product in ("HL", "LAP") else rnd.choice([12, 24, 36, 48, 60])
        roi = rnd.choice([8.45, 9.15, 10.5, 12.75, 14.0, 16.5, 21.25, 24.0])
        r = roi / 1200
        emi = round(sanction * r * (1 + r) ** tenure / ((1 + r) ** tenure - 1))
        months_run = max(1, (AS_OF.year - disb.year) * 12 + AS_OF.month - disb.month)
        outstanding = max(1000.0, sanction * max(0.05, 1 - months_run / tenure)) + rnd.random()
        L.update(sanction_amount=sanction, tenure_months=tenure, interest_rate_percent=roi, emi_amount=emi,
                 outstanding_amount=round(outstanding, 2), last_payment_amount=emi,
                 last_payment_date=AS_OF - timedelta(days=rnd.randint(2, 25)))
        if coll != "NONE":
            L["collateral_value"] = round(sanction * rnd.choice([1.1, 1.25, 1.5, 2.0]))
        if product == "GL":
            L.update(pay_frequency="BULLET", emi_amount=None, tenure_months=12)
        if product in ("MFP", "MFB"):
            L["pay_frequency"] = rnd.choice(["WEEKLY", "FORTNIGHTLY"])
    elif kind == "card":
        limit = rnd.choice([50000, 100000, 200000, 500000])
        bal = round(limit * rnd.uniform(0.05, 0.7), 2)
        L.update(sanction_amount=round(limit * rnd.uniform(0.7, 1.0)), credit_limit=limit, cash_limit=limit // 5,
                 interest_rate_percent=36.0, outstanding_amount=bal, last_payment_amount=round(bal * 0.3),
                 last_payment_date=AS_OF - timedelta(days=rnd.randint(2, 25)), pay_frequency="MONTHLY")
        if coll != "NONE":
            L["collateral_value"] = round(limit * 1.1)
    else:  # od
        limit = rnd.choice([100000, 300000, 500000])
        L.update(sanction_amount=limit, outstanding_amount=round(limit * rnd.uniform(0.2, 0.9), 2), interest_rate_percent=11.5,
                 collateral_value=round(limit * 1.1), last_payment_date=AS_OF - timedelta(days=rnd.randint(2, 25)),
                 last_payment_amount=rnd.choice([5000, 12000]), pay_frequency="ONDEMAND")
    L.update(over)
    return L


# ------------------------------------------------------------------ scenarios
def build():
    custs: list[Customer] = []
    accounts: list[dict] = []   # one row per account x holder

    def add(no, loan, holders, expected="ACCEPT", rule="", desc=""):
        for c, role in holders:
            if c not in custs:
                custs.append(c)
            exp = expected(c, role) if callable(expected) else expected
            accounts.append(dict(loan, customer_id=c.customer_id, party_role=role, test_scenario_number=no,
                                 test_expected=exp, test_rule_reference=rule, test_description=desc))

    P = "PRIMARY"
    # ---------- positive coverage
    c1 = new_customer(ids=("PAN", "AADHAAR", "CKYC"), addresses=2, landline=True)
    add(1, new_loan("HL", disb=date(2019, 4, 15)), [(c1, P)], desc="Home loan, individual, office landline with extension")
    c2a, c2b = new_customer(sex="M"), new_customer(sex="F")
    add(2, new_loan("HL"), [(c2a, P), (c2b, "JOINT")], desc="Joint home loan -> one record per holder, ownership 4")
    add(3, new_loan("PL"), [(new_customer(), P)], desc="Personal loan clean")
    c4 = new_customer()
    add(4, new_loan("PL"), [(c4, P), (new_customer(), "GUARANTOR")], desc="PL with guarantor -> ownership 3 record")
    add(5, new_loan("AL"), [(new_customer(ids=("PAN", "DL")), P)], desc="Auto loan, DL id, collateral Others")
    add(6, new_loan("GL", pay_frequency="BULLET"), [(new_customer(), P)], desc="Gold loan, bullet repayment, gold collateral")
    add(7, new_loan("CC"), [(c1, P)], desc="Credit card, credit + cash limit, same borrower as #1")
    c8 = new_customer()
    add(8, new_loan("CC"), [(c8, P), (new_customer(), "ADDON")], desc="Credit card with add-on user (ownership 2, type 10)")
    add(9, new_loan("CC", outstanding_amount=-2450.0), [(new_customer(), P)], desc="Credit card with credit balance (trailing '-')")
    add(10, new_loan("SCC"), [(new_customer(), P)], desc="Secured credit card (31) against FD")
    add(11, new_loan("CCC"), [(new_customer(landline=True), P)], desc="Corporate credit card (35)")
    add(12, new_loan("FLC"), [(new_customer(), P)], desc="Fleet card (16)")
    add(13, new_loan("OD"), [(new_customer(), P)], desc="Overdraft (12) - no EMI allowed")
    c14 = new_customer(addresses=1)
    c14.addresses.append(("RES", "Apt 12B, 400 Huron Street", "Toronto ON", None, "Toronto", "Foreign", "M5S2G4", "CA", "RENT", 0))
    add(14, new_loan("EDU"), [(c14, P)], desc="Education loan with foreign address (state 77)")
    add(15, new_loan("TWL"), [(new_customer(), P)], desc="Two-wheeler loan")
    add(16, new_loan("CVL"), [(new_customer(), P)], desc="Commercial vehicle loan in individual name")
    c17 = new_customer()
    c17.occupation, c17.income_amount, c17.income_basis, c17.income_period = "SELF_EMPLOYED", 1800000, "GROSS", "ANNUAL"
    add(17, new_loan("BLU"), [(c17, P)], desc="Business loan unsecured, annual gross income")
    add(18, new_loan("MFP"), [(new_customer(ids=("VOTER", "GRAMG"), emails=0), P)], desc="Microfinance, Voter ID + G RAM G, weekly")
    add(19, new_loan("KCC", pay_frequency="YEARLY"), [(new_customer(ids=("AADHAAR", "RATION"), emails=0), P)], desc="Kisan credit card, ration card")
    add(20, new_loan("LAP"), [(new_customer(), P)], desc="Loan against property")
    add(21, new_loan("CDL", tenure_months=12), [(new_customer(), P)], desc="Consumer durable loan")
    add(22, new_loan("LOC"), [(new_customer(), P)], desc="Loan on credit card (37)")
    add(23, new_loan("PL", disb=date(2021, 2, 10), closed_date=date(2026, 6, 18), last_payment_date=date(2026, 6, 18),
                     outstanding_amount=0, overdue_amount=0), [(new_customer(), P)], desc="Closed loan, zero balance")
    add(24, new_loan("HL", facility_status="RESTRUCT_COVID", disb=date(2018, 8, 1)), [(new_customer(), P)],
        desc="Restructured due to COVID-19 (12)")
    add(25, new_loan("PL", facility_status="WRITTEN_OFF", days_past_due=1100, overdue_amount=210000, asset_classification="LOSS",
                     writeoff_total_amount=265000, writeoff_principal_amount=240000, suit_status="SUIT_FILED", disb=date(2019, 1, 5)),
        [(new_customer(), P)], desc="Written off (02), DPD 1100 -> capped 900, suit filed")
    add(26, new_loan("PL", facility_status="SETTLED", days_past_due=0, writeoff_total_amount=90000, writeoff_principal_amount=80000,
                     settlement_amount=55000), [(new_customer(), P)], desc="Settled (03) with written-off + settlement")
    add(27, new_loan("AL", facility_status="POST_WO_SETTLED", writeoff_total_amount=120000, writeoff_principal_amount=110000,
                     settlement_amount=70000), [(new_customer(), P)], desc="Post write-off settled (04)")
    add(28, new_loan("AL", facility_status="REPOSSESSED_SETTLED", settlement_amount=180000), [(new_customer(), P)],
        desc="Repossessed & settled (16)")
    add(29, new_loan("GL", facility_status="AUCTIONED_SETTLED", settlement_amount=42000), [(new_customer(), P)],
        desc="Auctioned & settled (15)")
    c30p, c30g = new_customer(), new_customer()
    add(30, new_loan("BLU", facility_status="GUARANTEE_INVOKED", days_past_due=240, overdue_amount=310000, asset_classification="DBT"),
        [(c30p, P), (c30g, "GUARANTOR")],
        expected=lambda c, r: "ACCEPT" if r == "GUARANTOR" else "CONFIG_REJECT_RECORD",
        rule="TL.22=17 requires TL.05=3", desc="Guarantee invoked (17): guarantor OK, primary configurable reject")
    add(31, new_loan("PL", days_past_due=None, asset_classification="SMA", overdue_amount=0), [(new_customer(), P)],
        desc="Asset classification only (SMA), DPD absent")
    add(32, new_loan("PL"), [(new_customer(), "DECEASED")], desc="Deceased borrower (ownership 5)")
    add(33, new_loan("PL", previous_member_code="NB09999999", previous_member_short_name="OLD FINCORP",
                     previous_loan_number="OLD-778812", previous_product_code="CDL"), [(new_customer(), P)],
        desc="Member code, account no. and account type changed (TL.16-19)")
    add(34, new_loan("HL", disb=date(2003, 7, 1), tenure_months=360),
        [(new_customer(ids=(), phones=0, emails=0, dob=None), P)],
        desc="Pre-2005 account: no DOB, no ID, no phone - still valid")
    c34 = custs[-1]
    c34.sex = None
    c35 = new_customer()
    c35.addresses = [("PERM", "HQ 21 Infantry Division", "C/O 56 APO", None, None, "APO", "956789", "IN", None, 1)]
    add(35, new_loan("PL"), [(c35, P)], desc="APO address (state 99, PIN 9xxxxx)")
    add(36, new_loan("HL"), [(new_customer(ids=("PAN", "PASSPORT", "VOTER", "DL", "RATION", "AADHAAR", "CKYC", "GRAMG")), P)],
        desc="All 8 ID types populated")
    add(37, new_loan("PL"), [(new_customer(phones=3, landline=True, emails=3, addresses=3), P)],
        desc="All repeating slots filled: 4 phones, 3 emails, 3 addresses")
    add(38, new_loan("TWL"), [(new_customer(sex="M", name=("Ramesh", "Kumar", "S/O Suresh Kumar")), P)],
        desc="Relationship descriptor S/O in name")
    add(39, new_loan("MFB"), [(new_customer(sex="F", name=("Lakshmamma", None, None), ids=(), emails=0), P)],
        desc="Single-word name with valid mobile -> accepted")

    # ---------- negative coverage
    add(40, new_loan("PL", disb=date(2015, 5, 5)),
        [(new_customer(sex="M", name=("Raju", None, None), ids=(), phones=0, emails=0), P)],
        "REJECT_RECORD", "PN 6.1 #3", "Single-word name, no ID, no phone")
    add(41, new_loan("PL"), [(new_customer(name=("Anil", "2nd", "Verma")), P)], "REJECT_RECORD", "PN 6.1 #8",
        "Digits in name")
    c42 = new_customer(ids=(), phones=0)
    c42.kyc.append(("PAN", gen_pan(c42.last_name, holder="C"), None, None))
    add(42, new_loan("PL"), [(c42, P)], "REJECT_RECORD", "1.3 / 7.1 PAN",
        "Only ID is a company PAN (4th char C), no phone")
    c43 = new_customer(ids=("PAN",))
    c43.kyc.append(("AADHAAR", gen_aadhaar(valid=False), None, None))
    add(43, new_loan("PL"), [(c43, P)], "REJECT_SEGMENT:ID", "7.1 UID", "Aadhaar fails Verhoeff check; record survives")
    c44 = new_customer(phones=0)
    c44.contacts = [("MOBILE", "9876543210", None, 1), ("HOME", "1111111", None, 0)]
    add(44, new_loan("PL"), [(c44, P)], "REJECT_SEGMENT:PT", "8.1", "Sequential and repeated-digit phones")
    c45 = new_customer(emails=0)
    c45.emails = [("ramesh@@gmail", 1), ("ab cd@yahoo.com", 0)]
    add(45, new_loan("PL"), [(c45, P)], "REJECT_SEGMENT:EC", "App E", "Malformed emails")
    c46 = new_customer(addresses=0)
    c46.addresses = [("PERM", "12 Lake Road", "Andheri East", None, "Mumbai", "Maharashtra", "560001", "IN", "OWN", 1)]
    add(46, new_loan("HL"), [(c46, P)], "REJECT_RECORD", "10.2 #4", "Only address has PIN outside state range")
    add(47, new_loan("PL", closed_date=date(2026, 8, 31), outstanding_amount=15400.0), [(new_customer(), P)],
        "REJECT_RECORD", "TL.10", "Date closed with positive balance")
    add(48, new_loan("PL", days_past_due=45, overdue_amount=0), [(new_customer(), P)], "REJECT_RECORD", "TL.15",
        "DPD > 0 with zero overdue on non-card")
    add(49, new_loan("PL", facility_status="WRITTEN_OFF", days_past_due=400, overdue_amount=90000, asset_classification="LOSS"),
        [(new_customer(), P)], "CONFIG_REJECT_RECORD", "TL.41/42", "Written-off without written-off amounts")
    add(50, new_loan("CC", emi_amount=4500), [(new_customer(), P)], "REJECT_FIELD:TL.40", "TL.40",
        "EMI reported on credit card")
    add(51, new_loan("PL", days_past_due=None, asset_classification=None), [(new_customer(), P)], "REJECT_RECORD", "TL.15/26",
        "Both DPD and asset classification missing")
    add(52, new_loan("PL", disb=date(2024, 1, 10)), [(new_customer(dob=date(2025, 3, 1)), P)], "REJECT_RECORD",
        "4.1 PN.07", "Date of birth after disbursement date")
    return custs, accounts


def build_tenant(tenant_no: int):
    """Same scenarios for every tenant, different random values and seeds per tenant."""
    global _cust_seq, _loan_seq
    rnd.seed(SEED + tenant_no - 1)  # tenant 1 keeps the original seed
    _cust_seq = _loan_seq = 0
    return build()


# ------------------------------------------------------------------ flatten
KYC_COLS = {"PAN": ("pan_number",), "PASSPORT": ("passport_number", "passport_issue_date", "passport_expiry_date"),
            "VOTER": ("voter_id_number", "voter_id_issue_date"), "DL": ("driving_license_number", "driving_license_issue_date", "driving_license_expiry_date"),
            "RATION": ("ration_card_number",), "AADHAAR": ("aadhaar_number",), "CKYC": ("ckyc_number",), "GRAMG": ("gram_g_id",)}


def flatten(c: Customer) -> dict:
    row = dict(customer_id=c.customer_id, first_name=c.first_name, middle_name=c.middle_name, last_name=c.last_name,
               birth_date=c.birth_date, sex=c.sex, occupation=c.occupation, income_amount=c.income_amount,
               income_basis=c.income_basis, income_period=c.income_period)
    for doc_type, no, issue, expiry in c.kyc:
        row.update(zip(KYC_COLS[doc_type], (no, issue, expiry)))
    for i, (typ, no, ext, _) in enumerate(c.contacts[:4], 1):
        row.update({f"phone{i}_number": no, f"phone{i}_type": typ, f"phone{i}_extension": ext})
    for i, (email, _) in enumerate(c.emails[:3], 1):
        row[f"email{i}"] = email
    for i, (typ, l1, l2, l3, city, state, pin, country, tenure, _) in enumerate(c.addresses[:3], 1):
        row.update({f"address{i}_type": typ, f"address{i}_line1": l1, f"address{i}_line2": l2, f"address{i}_line3": l3,
                    f"address{i}_city": city, f"address{i}_state": state, f"address{i}_pin_code": pin,
                    f"address{i}_country": country, f"address{i}_tenure": tenure})
    return row


# ------------------------------------------------------------------ source table layouts
def _amount(name: str) -> Column:
    return Column(name, Numeric(20, 2))


def _borrower_columns(variant: bool) -> list[Column]:
    cols = [Column("customer_id", String(20), primary_key=True), Column("first_name", String(50)),
            Column("middle_name", String(50)), Column("last_name", String(50)),
            Column("dob" if variant else "birth_date", Date), Column("sex", String(12)),
            Column("occupation", String(20)), _amount("income_amount"), Column("income_basis", String(10)),
            Column("income_period", String(10)), Column("pan_number", String(20)), Column("passport_number", String(20)),
            Column("passport_issue_date", Date), Column("passport_expiry_date", Date),
            Column("voter_id_number", String(30)), Column("voter_id_issue_date", Date),
            Column("driving_license_number", String(30)), Column("driving_license_issue_date", Date),
            Column("driving_license_expiry_date", Date), Column("ration_card_number", String(30)),
            Column("aadhaar_number", String(20)), Column("ckyc_number", String(20)), Column("gram_g_id", String(50))]
    for i in range(1, 5):
        number = "primary_mobile" if variant and i == 1 else f"phone{i}_number"
        cols += [Column(number, String(30)), Column(f"phone{i}_type", String(10)), Column(f"phone{i}_extension", String(10))]
    cols += [Column(f"email{i}", String(100)) for i in range(1, 4)]
    for i in range(1, 4):
        cols += [Column(f"address{i}_{c}", String(n)) for c, n in
                 (("type", 10), ("line1", 100), ("line2", 100), ("line3", 100), ("city", 50), ("state", 50),
                  ("pin_code", 10), ("country", 2), ("tenure", 10))]
    return cols


def _account_columns(variant: bool) -> list[Column]:
    cols = [Column("loan_id", String(20)), Column("customer_id", String(20)), Column("party_role", String(10)),
            Column("loan_number", String(30)), Column("branch_code", String(10)), Column("product_code", String(20)),
            Column("member_code", String(10)), Column("member_short_name", String(16)), _amount("sanction_amount"),
            Column("disbursement_date", Date), Column("last_payment_date", Date), Column("closed_date", Date),
            Column("reported_date", Date), _amount("outstanding_amount"), _amount("overdue_amount")]
    if not variant:
        cols.append(Column("days_past_due", Integer))
    cols += [Column("asset_classification", String(10)), Column("suit_status", String(20)),
             Column("facility_status", String(30)), Column("collateral_type", String(20)), _amount("collateral_value"),
             _amount("credit_limit"), _amount("cash_limit"), Column("interest_rate_percent", Numeric(8, 3)),
             Column("tenure_months", Integer), _amount("emi_amount"), _amount("writeoff_total_amount"),
             _amount("writeoff_principal_amount"), _amount("settlement_amount"), Column("pay_frequency", String(20)),
             _amount("last_payment_amount"), Column("previous_member_code", String(10)),
             Column("previous_member_short_name", String(16)), Column("previous_loan_number", String(30)),
             Column("previous_product_code", String(20)), Column("previous_party_role", String(10)),
             Column("test_scenario_number", Integer), Column("test_expected", String(30)),
             Column("test_rule_reference", String(30)), Column("test_description", String(255)),
             PrimaryKeyConstraint("loan_id", "customer_id")]
    return cols


def source_tables(schema: str, variant: bool) -> dict[str, Table]:
    md = MetaData(schema=schema)
    tables = {"borrower_demographics": Table("borrower_demographics", md, *_borrower_columns(variant)),
              "account_details": Table("account_details", md, *_account_columns(variant))}
    if variant:
        tables["loan_ext"] = Table("loan_ext", md, Column("loan_id", String(20), primary_key=True),
                                   Column("overdue_days", Integer), Column("scheme_code", String(20)))
    return tables


# ------------------------------------------------------------------ tenants
VARIANT_PRODUCTS = {"HL": "HOME_LOAN", "LAP": "LOAN_AGAINST_PROP", "PL": "PERSONAL", "AL": "AUTO", "UCL": "USED_CAR",
                    "TWL": "TWO_WHEELER", "CVL": "COMM_VEHICLE", "GL": "GOLD", "EDU": "EDUCATION",
                    "CDL": "CONSUMER_DURABLE", "BLU": "BIZ_UNSECURED", "BLS": "BIZ_SECURED", "MFP": "MFI_PERSONAL",
                    "MFB": "MFI_BUSINESS", "KCC": "KISAN_CARD", "LAS": "LOAN_AGAINST_SHARES", "LOC": "LOAN_ON_CARD",
                    "CC": "CREDIT_CARD", "SCC": "SECURED_CARD", "CCC": "CORPORATE_CARD", "FLC": "FLEET_CARD",
                    "OD": "OVERDRAFT"}
VARIANT_SEX = {"M": "MALE", "F": "FEMALE", "T": "TRANSGENDER"}
SCHEMES = ["STD-RETAIL", "FESTIVE-26", "STAFF", "DIGITAL"]

TENANTS = {"nbfc_001": (1, False), "nbfc_002": (2, True)}   # schema -> (tenant_no, variant layout)


def tenant_rows(tenant_no: int, variant: bool) -> dict[str, list[dict]]:
    custs, accounts = build_tenant(tenant_no)
    borrowers = [flatten(c) for c in custs]
    if not variant:
        return {"borrower_demographics": borrowers, "account_details": accounts}
    for b in borrowers:
        b["dob"] = b.pop("birth_date")
        if "phone1_number" in b:
            b["primary_mobile"] = b.pop("phone1_number")
        b["sex"] = VARIANT_SEX.get(b["sex"], b["sex"])
    loan_ext = {}
    for a in accounts:
        for key in ("product_code", "previous_product_code"):
            a[key] = VARIANT_PRODUCTS.get(a[key], a[key])
        loan_ext[a["loan_id"]] = dict(loan_id=a["loan_id"], overdue_days=a.pop("days_past_due"),
                                      scheme_code=SCHEMES[int(a["loan_id"][1:]) % len(SCHEMES)])
    return {"borrower_demographics": borrowers, "account_details": accounts, "loan_ext": list(loan_ext.values())}


def all_tenants() -> dict[str, tuple[dict[str, Table], dict[str, list[dict]]]]:
    return {schema: (source_tables(schema, variant), tenant_rows(no, variant))
            for schema, (no, variant) in TENANTS.items()}


# ------------------------------------------------------------------ output
def load_into(engine: Engine, schemas=None) -> dict[str, dict[str, int]]:
    """Create and fill the mock tables. Schemas must already exist (MySQL database / SQLite ATTACH)."""
    counts = {}
    for schema, (tables, rows) in all_tenants().items():
        if schemas and schema not in schemas:
            continue
        md = next(iter(tables.values())).metadata
        md.drop_all(engine)
        md.create_all(engine)
        with engine.begin() as conn:
            for name, table in tables.items():
                full = [{c.name: r.get(c.name) for c in table.columns} for r in rows[name]]
                conn.execute(table.insert(), full)
        counts[schema] = {name: len(r) for name, r in rows.items()}
    return counts


def _literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float, Decimal)):
        return repr(v) if not isinstance(v, Decimal) else str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def mysql_script() -> str:
    out = ["-- Mock LMS source data (nbfc_001 base layout, nbfc_002 customised layout).",
           "-- GENERATED by mock_data/generate_mock_data.py - do not edit. Run as a script (Alt+X in DBeaver)."]
    for schema, (tables, rows) in all_tenants().items():
        out += ["", f"CREATE DATABASE IF NOT EXISTS {schema};"]
        for name, table in tables.items():
            out.append(f"DROP TABLE IF EXISTS {schema}.{name};")
            out.append(str(CreateTable(table).compile(dialect=mysql.dialect())).strip() + ";")
            cols = [c.name for c in table.columns]
            for r in rows[name]:
                out.append(f"INSERT INTO {schema}.{name} ({', '.join(cols)}) VALUES "
                           f"({', '.join(_literal(r.get(c)) for c in cols)});")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).with_name("mock_lms.sql")))
    ap.add_argument("--load", metavar="URL", help="also load into this server (schemas are created first)")
    args = ap.parse_args()
    Path(args.out).write_text(mysql_script(), encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    if args.load:
        engine = create_engine(args.load)
        with engine.begin() as conn:
            for schema in TENANTS:
                conn.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS {schema}")
        for schema, counts in load_into(engine).items():
            print(schema, counts)
