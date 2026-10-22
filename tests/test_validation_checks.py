import pytest

from submissions.validation.checks.addresses import pin_format_problem, pin_state_problem
from submissions.validation.checks.emails import email_problem
from submissions.validation.checks.identifiers import id_number_problem, verhoeff_valid
from submissions.validation.checks.names import check_name
from submissions.validation.checks.phones import number_problem, valid_numbers


# ---------------------------------------------------------------- names (§6.1, Appendix A)
@pytest.mark.parametrize("name,digits,bad,descriptor_first,valid_token,words", [
    ("Haren Patel", False, "", False, True, 2),
    ("Anil 2nd Verma", True, "", False, True, 3),
    ("Ramesh Kumar S/O Suresh Kumar", False, "", False, True, 4),
    ("Ramesh S\\O Suresh", False, "", False, True, 2),
    ("S/O Suresh", False, "", True, True, 1),
    ("Mr. Rakesh", False, "", False, True, 1),            # leading salutation ignored
    ("Rakesh Mr.", False, "", False, True, 2),            # trailing salutation is part of the name
    ("Amit & Sons", False, "&", False, True, 3),
    ("C/O Amit", False, "/", False, True, 2),
    ("A. B.", False, "", False, False, 2),                # initials only
    ("O'Brien", False, "", False, True, 1),
])
def test_check_name(name, digits, bad, descriptor_first, valid_token, words):
    c = check_name(name)
    assert (c.has_digits, c.disallowed_chars, c.starts_with_descriptor, c.has_valid_token, c.word_count) == \
        (digits, bad, descriptor_first, valid_token, words)


# ---------------------------------------------------------------- identifiers (§7.1)
def test_verhoeff():
    assert verhoeff_valid("234123412346")           # known-valid sample
    assert not verhoeff_valid("234123412345")


@pytest.mark.parametrize("id_type,number,ok", [
    ("01", "ABCPD1234E", True), ("01", "abcpd 1234 e", True), ("01", "ABCCD1234E", False), ("01", "ABCPD123E", False),
    ("02", "A-9388067", True), ("02", "AB12345", True), ("02", "1A234567", False), ("02", "A12345", False),
    ("03", "MT/08/039/0060725", True), ("03", "ABC1234567", True), ("03", "AB12", False),
    ("06", "2341 2341 2346", True), ("06", "2341 2341 2345", False), ("06", "23412341234", False),
    ("09", "12345678901234", True), ("09", "1234567890123456", False),
    ("10", "MH-12-123-123-123/123Patil", True), ("10", "MH-12-123-123-123/Patil", False),
    ("04", "anything goes", True), ("05", "x", True),
])
def test_id_number_problem(id_type, number, ok):
    assert (id_number_problem(id_type, number) is None) == ok


# ---------------------------------------------------------------- phones (§8.1)
@pytest.mark.parametrize("raw,phone_type,ok", [
    ("9876501234", "01", True), ("+91 98765 01234", "01", True), ("09876501234", "01", True),
    ("9876543210", "01", False),      # descending sequence
    ("1111111", "02", False),         # repeated digit
    ("1800123456", "03", False),      # starts with 1
    ("4876501234", "01", False),      # Indian mobile must start 5-9
    ("022-26541234", "03", True),
    ("1234", "02", False),            # fewer than 5 digits
    ("+44 20 7946 0958", "01", True), # ISD number
])
def test_number_problem(raw, phone_type, ok):
    assert (number_problem(raw, phone_type) is None) == ok


def test_multiple_numbers_only_for_unclassified():
    assert valid_numbers("5436734,645231,012 3", "00") == ["5436734", "645231"]   # truncated last number dropped
    assert valid_numbers("876543/9123456780", "00") == ["9123456780"]
    assert valid_numbers("", "01") == []


# ---------------------------------------------------------------- e-mail (Appendix E)
@pytest.mark.parametrize("email,ok", [
    ("abc@gmail.com", True), ("kumar@yahoo.co.in", True), ("A.B-c_d@Mail.COM", True),
    ("ramesh@@gmail", False), ("ab cd@yahoo.com", False), ("a@gmail.com", False), ("abc@g.com", False),
    ("abc@gmail.c", False), ("abc@gmail.", False), ("abc@gmail..com", False), ("abc@gmail", False),
    ("abc@gmail.c0m", False), ("ab#c@gmail.com", False),
])
def test_email_problem(email, ok):
    assert (email_problem(email) is None) == ok


def test_email_double_dot_configurable():
    assert email_problem("abc@gmail..com", strict_dots=False) is None
    assert email_problem("abc@gmail...com", strict_dots=False) is not None


# ---------------------------------------------------------------- PIN (§10.2, Appendix C)
@pytest.mark.parametrize("pin,state,format_ok,state_ok", [
    ("604002", "33", True, True), ("604-002", "33", True, True), ("900 000", "99", False, True),
    ("400001", "27", True, True), ("560001", "27", True, False), ("604", "33", False, True),
    ("6040020000", "33", False, True), ("M5S2G4", "77", True, True), ("956789", "99", True, True),
])
def test_pin_rules(pin, state, format_ok, state_ok):
    assert (pin_format_problem(pin, state) is None) == format_ok
    assert (pin_state_problem(pin, state) is None) == state_ok
