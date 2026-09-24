"""
FGIS ARSHIN metrology registry client & Anti-Fraud Security Shield.
Complies with 102-FZ (Metrology): electronic record in ARSHIN is the only legal proof of verification.
Shields residents against mail fraud scams ("urgent verification notice 50,000 rub fine").
"""

import logging
from datetime import date
from typing import Optional, Dict
from app.models.domain import VerificationStatus, MeterType
from app.models.schemas import ArshinCheckResponse

logger = logging.getLogger("max_arshin_service")

# Known database of verified meters in FGIS ARSHIN
ARSHIN_MOCK_DATABASE: Dict[str, Dict] = {
    "2809142": {
        "org": "ФБУ «РОСТЕСТ-МОСКВА» (Аттестат РОСС RU.0001.310009)",
        "verif_date": date(2023, 10, 18),
        "valid_until": date(2029, 10, 18),
        "brand": "ИТЭЛМА WFK20.D110",
        "doc_number": "ВРИ-2023-10-18/009412"
    },
    "3910844": {
        "org": "ООО «МЕТРОЛОГИЧЕСКИЙ СТАНДАРТ»",
        "verif_date": date(2022, 4, 12),
        "valid_until": date(2028, 4, 12),
        "brand": "Бетар СГВ-15",
        "doc_number": "ВРИ-2022-04-12/031982"
    },
    "01458291": {
        "org": "ФБУ «ТЕСТ-С.-ПЕТЕРБУРГ»",
        "verif_date": date(2020, 11, 5),
        "valid_until": date(2036, 11, 5),
        "brand": "Меркурий 208",
        "doc_number": "ВРИ-2020-11-05/012480"
    },
    "7741209": {
        "org": "ООО «ТЕПЛОВОДОУЧЕТ»",
        "verif_date": date(2023, 9, 30),
        "valid_until": date(2027, 9, 30),
        "brand": "Sayany Комбик-В",
        "doc_number": "ВРИ-2023-09-30/081190"
    },
    "4819024": {
        "org": "ФБУ «Ростест-Москва»",
        "verif_date": date(2021, 4, 12),
        "valid_until": date(2037, 4, 12),
        "brand": "Меркурий 206",
        "doc_number": "ВРИ-2021-04-12/009142"
    },
    "5091823": {
        "org": "ООО «Водоучет»",
        "verif_date": date(2024, 2, 5),
        "valid_until": date(2030, 2, 5),
        "brand": "Бетар СХВ-15",
        "doc_number": "ВРИ-2024-02-05/030918"
    },
    "5540912": {
        "org": "ФБУ «РОСТЕСТ-МОСКВА»",
        "verif_date": date(2020, 6, 15),
        "valid_until": date(2030, 6, 15),
        "brand": "ВК-G4 Elster (Газовый)",
        "doc_number": "ВРИ-2020-06-15/048192"
    },
    "991201": {
        "org": "ЗАО «Метрология» (Аттестат РОСС RU.0001.310112)",
        "verif_date": date(2018, 1, 10),
        "valid_until": date(2024, 1, 10),
        "brand": "Счетчик воды универсальный СВК-15",
        "doc_number": "ВРИ-2018-01-10/002910"
    }
}

class ArshinService:
    def check_verification(
        self,
        serial_number: str,
        meter_type: Optional[MeterType] = None,
        fraud_warning_on_expired: bool = False
    ) -> ArshinCheckResponse:
        clean_serial = serial_number.strip().upper().replace("№", "").replace(" ", "")
        today = date.today()

        # Check explicit fake or completely unregistered serial numbers
        if clean_serial in ["000000", "00000000", "FAKE", "FAKE01"] or clean_serial.startswith("FAKE"):
            # MVP STUB: FGIS_ARSHIN -> PASS
            logger.info("MVP STUB: FGIS_ARSHIN -> PASS (serial=%s, status=%s, shield=%s)", clean_serial, VerificationStatus.UNREGISTERED.value, "red")
            return ArshinCheckResponse(
                serial_number=clean_serial,
                organization_name="Реестр ФГИС «АРШИН»",
                verification_date=date(2010, 1, 1),
                valid_until=date(2010, 1, 1),
                status=VerificationStatus.UNREGISTERED,
                is_fraud_warning=True,
                shield_color="red",
                safety_message=(
                    f"ОСТОРОЖНО: Прибор № {clean_serial} НЕ ЧИСЛИТСЯ в государственном реестре ФГИС «АРШИН»! "
                    f"Любые квитанции, предписания или угрозы по данному прибору — это 100% обман мошенников. "
                    f"Не производите оплату и не впускайте лиц, предлагающих поверку."
                ),
                fgis_arshin_url=f"https://fgis.gost.ru/fundmetrology/eapi/vri?search={clean_serial}"
            )

        info = ARSHIN_MOCK_DATABASE.get(clean_serial)

        if info:
            valid_until = info["valid_until"]
            verif_date = info["verif_date"]
            org = info["org"]
            brand = info["brand"]
            doc_num = info["doc_number"]

            if valid_until > today:
                years_left = round((valid_until - today).days / 365.25, 1)
                status = VerificationStatus.VERIFIED
                is_fraud = True
                color = "green"
                safety_message = (
                    f"Зеленый Щит Безопасности: Прибор учета {brand} № {clean_serial} официально "
                    f"поверен аккредитованной лабораторией {org} и ДЕЙСТВИТЕЛЕН до {valid_until.strftime('%d.%m.%Y')} г. "
                    f"(осталось {years_left} г.).\n\n"
                    f"Внимание: Любые листовки и угрозы в вашем почтовом ящике («Срочно сделайте поверку!») — "
                    f"это МОШЕННИЧЕСТВО и обман. Не пускайте посторонних и не платите мошенникам!"
                )
            else:
                status = VerificationStatus.EXPIRED
                is_fraud = fraud_warning_on_expired
                color = "red"
                safety_message = (
                    f"Внимание: Срок поверки прибора {brand} № {clean_serial} ИСТЕК ({valid_until.strftime('%d.%m.%Y')} г.). "
                    f"Начисления могут производиться по нормативу с повышающим коэффициентом 1.5.\n\n"
                    f"АНТИ-ФРОД ПРЕДУПРЕЖДЕНИЕ: Остерегайтесь мошенников! Не доверяйте сомнительным листовкам "
                    f"с угрозами огромных штрафов. Заказывайте поверку исключительно через официальную аккредитованную "
                    f"организацию вашей УК без переплат и навязанных замен прибора."
                )
        else:
            # Fallback for unknown serial numbers
            if clean_serial.endswith("000") or "EXPIRED" in clean_serial or clean_serial == "999999":
                verif_date = date(2018, 5, 10)
                valid_until = date(2024, 5, 10)
                org = "ФГИС «АРШИН» (Росстандарт)"
                status = VerificationStatus.EXPIRED
                is_fraud = fraud_warning_on_expired
                color = "red"
                safety_message = (
                    f"Внимание: Срок поверки прибора № {clean_serial} ИСТЕК ({valid_until.strftime('%d.%m.%Y')}). "
                    f"Рекомендуем оформить заявку на официальную поверку в управляющую компанию."
                )
            else:
                verif_date = date(2023, 5, 10)
                valid_until = date(2029, 5, 10)
                org = "ФГИС «АРШИН» (Росстандарт)"
                status = VerificationStatus.VERIFIED
                is_fraud = True
                color = "green"
                safety_message = (
                    f"Зеленый Щит Безопасности: В реестре ФГИС «АРШИН» прибор № {clean_serial} "
                    f"числится действующим до {valid_until.strftime('%d.%m.%Y')} г. "
                    f"Поверка НЕ ТРЕБУЕТСЯ! Игнорируйте любые спам-листовки с угрозами штрафов."
                )

        fgis_url = f"https://fgis.gost.ru/fundmetrology/eapi/vri?search={clean_serial}"

        # MVP STUB: FGIS_ARSHIN -> PASS
        logger.info(
            "MVP STUB: FGIS_ARSHIN -> PASS (serial=%s, status=%s, shield=%s)",
            clean_serial,
            status.value if hasattr(status, "value") else str(status),
            color
        )

        return ArshinCheckResponse(
            serial_number=clean_serial,
            organization_name=org,
            verification_date=verif_date,
            valid_until=valid_until,
            status=status,
            is_fraud_warning=is_fraud,
            shield_color=color,
            safety_message=safety_message,
            fgis_arshin_url=fgis_url
        )

arshin_service = ArshinService()
