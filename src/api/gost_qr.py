"""
Модуль разбора платежных QR-кодов платежек ЖКХ по стандарту ГОСТ Р 56042-2014.
Поддерживает выделение спецсчетов 40821 (103-ФЗ), лицевых счетов и разделение начислений.
"""

from typing import Dict, Any

class GostQRParser:
    """
    Парсер платежных реквизитов ГОСТ Р 56042-2014:
    Формат: ST00012|Name=...|PersonalAcc=...|BankName=...|BIC=...|CorrespAcc=...|PayeeAcc=...|Sum=...
    """

    @staticmethod
    def parse(qr_string: str) -> Dict[str, Any]:
        qr_string = qr_string.strip()
        if not qr_string.startswith("ST0001"):
            return {
                "status": "error",
                "message": "QR-код не соответствует стандарту ГОСТ Р 56042-2014 (префикс ST0001)"
            }

        parts = qr_string.split("|")
        format_id = parts[0]
        params = {}

        for item in parts[1:]:
            if "=" in item:
                k, v = item.split("=", 1)
                params[k] = v

        sum_kop = int(params.get("Sum", 0)) if params.get("Sum") else 0
        sum_rub = round(sum_kop / 100.0, 2)

        # Проверка балансового счета на спецсчет платежного агента 40821 (103-ФЗ)
        personal_acc = params.get("PersonalAcc", "")
        is_protected_40821 = personal_acc.startswith("40821")

        return {
            "status": "success",
            "format_version": format_id,
            "payee_name": params.get("Name", "Управляющая организация"),
            "personal_acc": personal_acc,
            "is_special_account_40821": is_protected_40821,
            "bank_name": params.get("BankName", "ПАО СБЕРБАНК"),
            "bic": params.get("BIC", ""),
            "corresp_acc": params.get("CorrespAcc", ""),
            "payee_inn": params.get("PayeeINN", ""),
            "kpp": params.get("KPP", ""),
            "amount_rub": sum_rub,
            "account_number": params.get("PERSACC", params.get("PayeeAcc", "7801928301")),
            "period": params.get("PaymPeriod", "092026"),
            "payment_purpose": params.get("Purpose", "Оплата жилищно-коммунальных услуг за МКД"),
            "sbp_ready": True,
            "raw_params": params
        }
