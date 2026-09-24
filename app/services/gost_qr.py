"""
GOST R 56042-2014 parser and generator + Account 40821 payment splitter.
Complies with Bank of Russia checksum algorithm (weights 7, 1, 3 mod 10).
Federal laws: 103-FZ (Payment agents) & 59-FZ (Direct utility contracts).
"""

import logging
from typing import Dict, Any, List, Optional
from app.models.schemas import (
    GostQrParseResponse,
    SplitPaymentRecipient
)

logger = logging.getLogger("max_gost_qr_service")

WEIGHTS = [7, 1, 3] * 8  # 24 weights for 20-23 characters

def verify_bank_account_checksum(bik: str, account: str) -> bool:
    """
    Validates Russian 20-digit bank account number according to Bank of Russia rules.
    Uses BIK (or RKC BIK digits) + 20-digit account string.
    Weights: 7, 1, 3 repeat cycle. Sum modulo 10 must be 0.
    """
    if not bik or not account or len(bik) != 9 or len(account) != 20:
        return False
    
    # For accounts starting with 30101 (correspondent), use BIK digits 4, 5, 6
    # For regular settlement accounts (40702, 40821, etc.), use BIK digits 6, 7, 8 (0-indexed)
    if account.startswith("30101") or account.startswith("30102"):
        control_string = "0" + bik[4:6] + account
    else:
        control_string = bik[6:9] + account
    
    total = 0
    for i in range(23):
        weight = [7, 1, 3][i % 3]
        digit = int(control_string[i])
        total += (digit * weight) % 10
        
    return (total % 10) == 0

def parse_gost_qr_payload(payload: str) -> GostQrParseResponse:
    """
    Parses a string formatted according to GOST R 56042-2014.
    Example payload:
    ST00012|Name=ООО "УК ДОМОВОЙ СЕРВИС"|PersonalAcc=40702810938000012345|BankName=ПАО СБЕРБАНК|BIC=044525225|CorrespAcc=30101810400000000225|PayeeINN=7701234567|KPP=770101001|Sum=485050|PersAcc=1004567890|Period=092026|TechCode=01
    """
    payload = payload.strip().lstrip("\ufeff")
    if not payload.upper().startswith("ST0001"):
        raise ValueError("Строка не соответствует стандарту ГОСТ Р 56042-2014 (префикс ST0001X отсутствует)")
    
    parts = payload.split("|")
    version = parts[0].upper()
    tags: Dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            tags[k.strip().lower()] = v.strip()
            
    name = tags.get("name", "Управляющая организация")
    payee_account = tags.get("personalacc", "")
    bik = tags.get("bic", "")
    cor_account = tags.get("correspacc")
    inn = tags.get("payeeinn") or tags.get("inn", "")
    kpp = tags.get("kpp")
    pers_acc = tags.get("persacc") or tags.get("payerid") or tags.get("els", "ELS-77-2026-99")
    period = tags.get("period", "09.2026")
    
    # Sum in kopecks or rubles (supports comma or dot decimals)
    raw_sum = tags.get("sum", "0").strip().replace(",", ".")
    try:
        amount_rubles = float(raw_sum) / 100.0 if raw_sum.isdigit() else float(raw_sum)
    except ValueError:
        amount_rubles = 0.0

    account_valid = verify_bank_account_checksum(bik, payee_account) if (len(bik) == 9 and len(payee_account) == 20) else False

    # Simulate automatic split under 103-FZ & 59-FZ (Account 40821)
    split_details: List[SplitPaymentRecipient] = []
    if amount_rubles > 0:
        # 1. Водоканал (ХВС / Водоотведение) ~25%
        water_amount = round(amount_rubles * 0.25, 2)
        # 2. Теплосеть (Отопление + ГВС) ~45%
        heat_amount = round(amount_rubles * 0.45, 2)
        # 3. Энергосбыт ~15%
        power_amount = round(amount_rubles * 0.15, 2)
        # 4. УК (Содержание жилого помещения) - остаток
        uk_amount = round(amount_rubles - (water_amount + heat_amount + power_amount), 2)
        
        split_details = [
            SplitPaymentRecipient(
                recipient_name="АО «Мосводоканал» (Прямой договор 59-ФЗ)",
                inn="7701987654",
                account_40821="40821810500000001001",
                bik=bik or "044525225",
                amount_rubles=water_amount,
                purpose=f"Оплата ХВС и водоотведения за {period}, ЛС {pers_acc}"
            ),
            SplitPaymentRecipient(
                recipient_name="ПАО «МОЭК» (ПАО «Т Плюс»)",
                inn="7720556677",
                account_40821="40821810500000002002",
                bik=bik or "044525225",
                amount_rubles=heat_amount,
                purpose=f"Оплата отопления и ГВС за {period}, ЛС {pers_acc}"
            ),
            SplitPaymentRecipient(
                recipient_name="АО «Мосэнергосбыт»",
                inn="7736520080",
                account_40821="40821810500000003003",
                bik=bik or "044525225",
                amount_rubles=power_amount,
                purpose=f"Электроэнергия за {period}, ЛС {pers_acc}"
            ),
            SplitPaymentRecipient(
                recipient_name=f"{name} (Содержание жилья)",
                inn=inn or "7701234567",
                account_40821=payee_account or "40702810938000012345",
                bik=bik or "044525225",
                amount_rubles=uk_amount,
                purpose=f"Содержание и текущий ремонт общего имущества МКД за {period}"
            )
        ]

    # MVP STUB: SBP_BANK -> PASS
    logger.info(
        "MVP STUB: SBP_BANK -> PASS (amount=%.2f, split_count=%d, account=%s)",
        amount_rubles,
        len(split_details),
        payee_account
    )

    return GostQrParseResponse(
        is_valid_gost=True,
        format_version=version,
        recipient_name=name,
        inn=inn,
        kpp=kpp,
        cor_account=cor_account,
        bank_bik=bik,
        payee_account=payee_account,
        is_account_checksum_valid=account_valid,
        personal_account=pers_acc,
        period=period,
        total_amount_rubles=amount_rubles,
        is_40821_split_supported=True,
        split_details=split_details
    )

def generate_gost_qr_string(
    recipient_name: str,
    personal_acc: str,
    bank_bik: str,
    cor_acc: str,
    inn: str,
    kpp: str,
    payer_pers_acc: str,
    amount_rubles: float,
    period: str = "092026"
) -> str:
    """
    Generates compliant GOST R 56042-2014 QR payload.
    """
    kopecks = int(round(amount_rubles * 100))
    return (
        f"ST00012|Name={recipient_name}|PersonalAcc={personal_acc}|BankName=ПАО СБЕРБАНК|"
        f"BIC={bank_bik}|CorrespAcc={cor_acc}|PayeeINN={inn}|KPP={kpp}|"
        f"Sum={kopecks}|PersAcc={payer_pers_acc}|Period={period}|TechCode=01"
    )
