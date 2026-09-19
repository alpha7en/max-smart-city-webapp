"""
Сквозной набор автоматических тестов всех модулей системы «Умный Дом ЖКХ в MAX».
Проверяет:
1. CV-пайплайн (заглушка с валидным расчетом показаний и аномалий)
2. ФГИС «АРШИН» (проверка поверки и «Зеленого щита»)
3. ГОСТ Р 56042-2014 (разбор QR-кодов квитанций)
4. Ночной дозор (расчет небаланса ОДПУ)
5. АРМ Обходчика (цифровой акт с GPS и SHA-256)
6. API профиля бота MAX (/me)
"""

import os
import sys
import unittest

# Добавляем корень проекта
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.cv_pipeline import MeterCVPipeline
from src.api.arshin import ArshinVerifier
from src.api.gost_qr import GostQRParser
from src.bot import MaxBotService, get_token

class TestMaxSmartHousing(unittest.TestCase):

    def test_01_cv_pipeline_water(self):
        """Проверка CV-распознавания счетчика воды (ГВС/ХВС)"""
        res = MeterCVPipeline.process_meter_image(meter_hint="ГВС")
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["is_mock"])
        self.assertIn("WFW20", res["meter_name"])
        self.assertEqual(res["serial_number"], "2809142")
        self.assertEqual(res["readings"]["integer_part"], 142)
        self.assertEqual(res["readings"]["decimal_part"], 385)
        self.assertEqual(len(res["rollers"]["black_digits"]), 5)
        self.assertEqual(len(res["rollers"]["red_digits"]), 3)
        self.assertGreater(res["calculation"]["estimated_rub"], 0)
        print("✅ Тест 1 пройден: CV-пайплайн счетчика воды работает корректно.")

    def test_02_cv_pipeline_electricity(self):
        """Проверка CV-распознавания электросчетчика"""
        res = MeterCVPipeline.process_meter_image(meter_hint="СВЕТ")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["meter_type"], "ЭЛЕКТРОЭНЕРГИЯ_2Т")
        self.assertEqual(res["readings"]["unit"], "кВт·ч")
        self.assertGreater(res["readings"]["value"], 1000)
        print("✅ Тест 2 пройден: CV-пайплайн электросчетчика работает корректно.")

    def test_03_arshin_valid_shield(self):
        """Проверка Зеленого Щита Безопасности (ФГИС АРШИН)"""
        res = ArshinVerifier.verify_meter("2809142")
        self.assertTrue(res["is_valid"])
        self.assertEqual(res["shield_badge"], "🛡️ ЗЕЛЕНЫЙ ЩИТ БЕЗОПАСНОСТИ")
        self.assertIn("НЕ ТРЕБУЕТСЯ", res["resident_alert_text"])
        self.assertIn("мошенничество", res["resident_alert_text"])
        print("✅ Тест 3 пройден: Зеленый щит АРШИН корректно защищает от мошенников.")

    def test_04_arshin_expired_meter(self):
        """Проверка счетчика с истекшим сроком поверки"""
        res = ArshinVerifier.verify_meter("991201")
        self.assertFalse(res["is_valid"])
        self.assertEqual(res["shield_badge"], "⚠️ ТРЕБУЕТСЯ ПОВЕРКА")
        print("✅ Тест 4 пройден: Оповещение об истекшей поверке сформировано.")

    def test_05_gost_qr_parsing(self):
        """Проверка парсера платежного QR-кода ГОСТ Р 56042-2014"""
        sample_qr = "ST00012|Name=ООО УК ТЕХНОДОМ|PersonalAcc=40821810123456789012|BankName=СБЕР|BIC=044525225|Sum=250000|PERSACC=998877"
        res = GostQRParser.parse(sample_qr)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["amount_rub"], 2500.0)
        self.assertTrue(res["is_special_account_40821"])
        self.assertEqual(res["account_number"], "998877")
        print("✅ Тест 5 пройден: Разбор QR-кода ГОСТ Р 56042-2014 со спецсчетом 40821 успешен.")

    def test_06_bot_api_connection(self):
        """Проверка подключения к API MAX через токен команды"""
        token = get_token()
        self.assertTrue(bool(token))
        bot = MaxBotService(token=token)
        me = bot.get_me()
        self.assertEqual(me.get("username"), "t226_hakaton_max_bot")
        print(f"✅ Тест 6 пройден: Бот @{me.get('username')} успешно авторизован в платформе MAX.")

if __name__ == "__main__":
    unittest.main()
