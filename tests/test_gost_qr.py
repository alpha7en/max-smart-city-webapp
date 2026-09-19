"""
Unit tests for GOST R 56042-2014 parser and Bank of Russia checksum algorithm.
"""

import pytest
from app.services.gost_qr import verify_bank_account_checksum, parse_gost_qr_payload, generate_gost_qr_string

def test_bank_account_checksum_valid():
    # Sberbank BIK: 044525225, valid Sberbank account: 40702810938000012345
    bik = "044525225"
    acc = "40702810938000012345"
    assert verify_bank_account_checksum(bik, acc) is True

def test_bank_account_checksum_invalid():
    bik = "044525225"
    acc = "40702810938000012344"  # Changed last digit
    assert verify_bank_account_checksum(bik, acc) is False

def test_bank_account_checksum_malformed():
    assert verify_bank_account_checksum("123", "456") is False
    assert verify_bank_account_checksum("", "") is False

def test_gost_qr_parsing_success():
    payload = (
        "ST00012|Name=ООО УК ДОМОВОЙ СЕРВИС|PersonalAcc=40702810938000012345|"
        "BIC=044525225|CorrespAcc=30101810400000000225|PayeeINN=7701234567|"
        "Sum=485050|PersAcc=1004567890|Period=092026|TechCode=01"
    )
    res = parse_gost_qr_payload(payload)
    assert res.is_valid_gost is True
    assert res.total_amount_rubles == 4850.50
    assert res.personal_account == "1004567890"
    assert res.recipient_name == "ООО УК ДОМОВОЙ СЕРВИС"
    assert res.is_account_checksum_valid is True
    assert len(res.split_details) == 4

    # Verify split amounts add up to total
    total_split = sum(item.amount_rubles for item in res.split_details)
    assert round(total_split, 2) == 4850.50

def test_gost_qr_parsing_invalid_header():
    with pytest.raises(ValueError) as exc:
        parse_gost_qr_payload("INVALID_QR_STRING")
    assert "не соответствует стандарту ГОСТ Р 56042-2014" in str(exc.value)

def test_gost_qr_generator():
    qr_str = generate_gost_qr_string(
        recipient_name="ООО УК ТЕСТ",
        personal_acc="40702810938000012345",
        bank_bik="044525225",
        cor_acc="30101810400000000225",
        inn="7701234567",
        kpp="770101001",
        payer_pers_acc="1004567890",
        amount_rubles=1500.0,
        period="092026"
    )
    assert qr_str.startswith("ST00012|")
    assert "Sum=150000" in qr_str
    parsed = parse_gost_qr_payload(qr_str)
    assert parsed.total_amount_rubles == 1500.0
