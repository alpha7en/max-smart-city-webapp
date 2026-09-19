"""
Основной сервис чат-бота для мессенджера MAX.
Использует официальный протокол platform-api2.max.ru.
Поддерживает Long Polling (GET /updates) и отправку сообщений (POST /messages).
"""

import os
import ssl
import json
import time
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional

from src.api.cv_pipeline import MeterCVPipeline
from src.api.arshin import ArshinVerifier
from src.api.gost_qr import GostQRParser

def get_token() -> str:
    """Безопасное чтение токена без вывода в логи"""
    if os.getenv("BOT_TOKEN"):
        return os.getenv("BOT_TOKEN").strip().strip('\"\'')
    for fpath in ['token.env', '.env']:
        if os.path.exists(fpath):
            with open(fpath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    token = line.split('=', 1)[1].strip().strip('\"\'') if '=' in line else line.strip().strip('\"\'')
                    if token:
                        return token
    raise ValueError("Токен бота не найден! Укажите переменные в token.env или BOT_TOKEN.")

class MaxBotService:
    def __init__(self, token: str = None, webapp_url: str = "http://localhost:8000"):
        self.token = token or get_token()
        self.webapp_url = os.getenv("WEBAPP_URL", webapp_url)
        self.base_url = "https://platform-api2.max.ru"
        self.ctx = ssl._create_unverified_context()
        self.bot_info = None

    def _api_request(self, endpoint: str, method: str = "GET", params: dict = None, body: dict = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"
        
        headers = {
            "Authorization": self.token,
            "User-Agent": "MAX-SmartCity-Bot/1.0"
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode('utf-8')

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, context=self.ctx, timeout=35) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def get_me(self) -> dict:
        if not self.bot_info:
            self.bot_info = self._api_request("/me")
        return self.bot_info

    def send_message(self, chat_id: Optional[int] = None, user_id: Optional[int] = None, text: str = "", attachments: list = None) -> dict:
        params = {}
        if chat_id:
            params["chat_id"] = chat_id
        elif user_id:
            params["user_id"] = user_id
        else:
            raise ValueError("Требуется указать chat_id или user_id")

        body = {"text": text}
        if attachments:
            body["attachments"] = attachments

        return self._api_request("/messages", method="POST", params=params, body=body)

    def answer_callback(self, callback_id: str, notification: str = None) -> dict:
        """Ответ на нажатие callback-кнопки"""
        try:
            body = {"notification": notification or "Принято!"}
            return self._api_request(f"/answers?callback_id={callback_id}", method="POST", body=body)
        except Exception as e:
            print(f"Ошибка ответа на callback {callback_id}: {e}")
            return {}

    def build_main_keyboard(self) -> dict:
        """Построение клавиатуры в формате MAX UI Inline Keyboard"""
        return {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [
                        {
                            "type": "link",
                            "text": "📱 Открыть Mini App (Камера / Сканер)",
                            "url": self.webapp_url
                        }
                    ],
                    [
                        {
                            "type": "callback",
                            "text": "💧 Тест скан ХВС",
                            "payload": "scan_hvs"
                        },
                        {
                            "type": "callback",
                            "text": "🔥 Тест скан ГВС",
                            "payload": "scan_gvs"
                        },
                        {
                            "type": "callback",
                            "text": "⚡ Тест свет 2Т",
                            "payload": "scan_light"
                        }
                    ],
                    [
                        {
                            "type": "callback",
                            "text": "🛡️ Проверить АРШИН",
                            "payload": "check_arshin"
                        },
                        {
                            "type": "callback",
                            "text": "🌙 Ночной дозор (ОДПУ)",
                            "payload": "night_patrol"
                        }
                    ],
                    [
                        {
                            "type": "callback",
                            "text": "🔑 Арендатор (Гость)",
                            "payload": "tenant_guest"
                        },
                        {
                            "type": "callback",
                            "text": "📋 Обходчик УК",
                            "payload": "uk_inspector"
                        }
                    ]
                ]
            }
        }

    def handle_photo_scan(self, chat_id: Optional[int], user_id: Optional[int], meter_hint: str = "ГВС"):
        """Обработка фотографии счетчика"""
        # 1. Вызов CV-пайплайна
        cv_res = MeterCVPipeline.process_meter_image(meter_hint=meter_hint)
        # 2. Проверка в ФГИС «АРШИН»
        arshin_res = ArshinVerifier.verify_meter(cv_res["serial_number"])

        text = (
            f"📸 <b>[ЗАГЛУШКА ИИ / CV MOCK] Распознавание завершено!</b>\n\n"
            f"🔹 <b>Прибор:</b> {cv_res['meter_name']}\n"
            f"🔢 <b>Заводской номер:</b> № {cv_res['serial_number']}\n"
            f"📊 <b>Показания:</b> <code>{cv_res['readings']['formatted_reading']} {cv_res['readings']['unit']}</code>\n"
            f"📈 <b>Расход за месяц:</b> +{cv_res['calculation']['delta']} {cv_res['readings']['unit']}\n"
            f"💰 <b>К начислению:</b> {cv_res['calculation']['estimated_rub']} ₽\n\n"
            f"------------------------------------\n"
            f"{arshin_res['shield_badge']}\n"
            f"{arshin_res['resident_alert_text']}\n"
            f"------------------------------------"
        )

        confirm_keyboard = {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [
                        {
                            "type": "callback",
                            "text": "✅ Подтвердить и передать в УК",
                            "payload": "confirm_readings"
                        },
                        {
                            "type": "callback",
                            "text": "💳 Оплатить через СБП",
                            "payload": "pay_sbp"
                        }
                    ],
                    [
                        {
                            "type": "link",
                            "text": "⚙️ Скорректировать в Mini App",
                            "url": self.webapp_url
                        }
                    ]
                ]
            }
        }

        self.send_message(chat_id=chat_id, user_id=user_id, text=text, attachments=[confirm_keyboard])

    def process_update(self, update: dict):
        """Маршрутизация входящих событий"""
        upd_type = update.get("update_type")

        # 1. Пользователь впервые запустил бота
        if upd_type == "bot_started":
            user = update.get("user", {})
            user_id = user.get("user_id") or user.get("id")
            chat_id = update.get("chat_id")
            name = user.get("first_name", "Житель")
            
            welcome = (
                f"Здравствуйте, {name}!\n\n"
                f"Я сервис <b>«Умный Дом ЖКХ в MAX»</b> 🏢\n\n"
                f"🚀 <b>Главная фишка:</b> Чтобы передать показания, вам не нужно блуждать по приложениям — "
                f"<b>просто отправьте фото счетчика прямо сюда в чат</b> (или перешлите из чата с близкими)!\n\n"
                f"Что умеет сервис:\n"
                f"• 📸 Распознавание воды, света и газа (YOLOv8 + OCR роликов)\n"
                f"• 🛡️ Зеленый Щит (защита от фальшивых поверок мошенников по 102-ФЗ)\n"
                f"• 🌙 Ночной дозор (поиск скрытых утечек ОДПУ вместе с УК)\n"
                f"• 🔑 Гостевой доступ для Арендаторов без учетной записи ЕСИА\n"
                f"• 🧾 Сканирование платежек по ГОСТ Р 56042-2014 и оплата СБП"
            )
            self.send_message(chat_id=chat_id, user_id=user_id, text=welcome, attachments=[self.build_main_keyboard()])

        # 2. Новое сообщение от пользователя
        elif upd_type == "message_created":
            msg = update.get("message", {})
            body = msg.get("body", {})
            text = (body.get("text") or "").strip().lower()
            sender = msg.get("sender", {})
            recipient = msg.get("recipient", {})
            chat_id = recipient.get("chat_id")
            user_id = sender.get("user_id") or sender.get("id")
            attachments = body.get("attachments", [])

            # Проверка наличия прикрепленной фотографии
            has_photo = any(att.get("type") in ["image", "photo", "file"] for att in attachments)
            if has_photo:
                self.send_message(chat_id=chat_id, user_id=user_id, text="🔍 Фотография получена! Анализирую прибор учета нейросетью...")
                self.handle_photo_scan(chat_id, user_id, meter_hint="ГВС")
                return

            # Текстовые команды
            if text in ["/start", "старт", "start", "меню"]:
                welcome = "Выберите интересующий раздел или отправьте фото счетчика прямо в чат:"
                self.send_message(chat_id=chat_id, user_id=user_id, text=welcome, attachments=[self.build_main_keyboard()])

            elif "показани" in text or "счетчик" in text or "фото" in text:
                self.handle_photo_scan(chat_id, user_id, meter_hint="ХВС")

            elif "аршин" in text or "поверк" in text:
                res = ArshinVerifier.verify_meter("2809142")
                self.send_message(
                    chat_id=chat_id, user_id=user_id,
                    text=f"<b>{res['shield_badge']}</b>\n\n{res['resident_alert_text']}\n\nБаза: {res['law_reference']}"
                )

            elif "дозор" in text or "утечк" in text:
                msg_leak = (
                    "🌙 <b>«Ночной дозор» ОДПУ:</b>\n"
                    "В подъезде № 2 зафиксирован ночной небаланс 35 л/мин.\n\n"
                    "🧪 <b>Тест с салфеткой:</b>\n"
                    "Положите сухую салфетку на чашу унитаза на 15 сек. Если промокла — течет бачок.\n"
                    "Сообщите в бот и получите скидку 10% на содержание дома!"
                )
                self.send_message(chat_id=chat_id, user_id=user_id, text=msg_leak)

            elif "аренд" in text:
                msg_guest = (
                    "🔑 <b>Гостевой доступ для Арендатора:</b>\n"
                    "Гостевая ссылка: <code>https://max.ru/t226_hakaton_max_bot?guest=kv48_7f9a2</code>\n"
                    "Жилец сможет передавать показания и оплачивать счета без доступа к вашим Госуслугам и документам собственности."
                )
                self.send_message(chat_id=chat_id, user_id=user_id, text=msg_guest)

            elif "обходчик" in text:
                msg_insp = (
                    "📋 <b>АРМ Обходчика УК:</b>\n"
                    "Сформирован цифровой акт осмотра прибора учета с GPS-привязкой и SHA-256 хэшем фото.\n"
                    "Данные переданы в 1С:ЖКХ."
                )
                self.send_message(chat_id=chat_id, user_id=user_id, text=msg_insp)

            else:
                echo = (
                    f"Я получил ваше сообщение: «{body.get('text', '')}».\n\n"
                    f"💡 Чтобы передать показания счетчика — просто <b>пришлите мне фото</b> в этот диалог.\n"
                    f"Или откройте Mini App по кнопке ниже."
                )
                self.send_message(chat_id=chat_id, user_id=user_id, text=echo, attachments=[self.build_main_keyboard()])

        # 3. Нажатие на Callback-кнопку
        elif upd_type == "message_callback":
            cb = update.get("callback", {})
            cb_id = cb.get("callback_id")
            payload = cb.get("payload", "")
            user = update.get("user", {})
            user_id = user.get("user_id") or user.get("id")
            chat_id = update.get("chat_id")

            if cb_id:
                self.answer_callback(cb_id, "Запрос обработан!")

            if payload == "scan_hvs":
                self.handle_photo_scan(chat_id, user_id, meter_hint="ХВС")
            elif payload == "scan_gvs":
                self.handle_photo_scan(chat_id, user_id, meter_hint="ГВС")
            elif payload == "scan_light":
                self.handle_photo_scan(chat_id, user_id, meter_hint="СВЕТ")
            elif payload == "check_arshin":
                res = ArshinVerifier.verify_meter("2809142")
                self.send_message(chat_id=chat_id, user_id=user_id, text=f"<b>{res['shield_badge']}</b>\n\n{res['resident_alert_text']}")
            elif payload == "night_patrol":
                self.send_message(chat_id=chat_id, user_id=user_id, text="🌙 Ночной дозор активен! Запустите тест бачка салфеткой в Mini App.")
            elif payload == "tenant_guest":
                self.send_message(chat_id=chat_id, user_id=user_id, text="🔑 Гостевая ссылка для жильца сгенерирована: https://max.ru/t226_hakaton_max_bot?guest=demo")
            elif payload == "uk_inspector":
                self.send_message(chat_id=chat_id, user_id=user_id, text="📋 Режим обходчика УК: сформирован акт осмотра с GPS-штампом для 1С:ЖКХ.")
            elif payload == "confirm_readings":
                self.send_message(chat_id=chat_id, user_id=user_id, text="✅ Показания успешно переданы в ГИС ЖКХ и УК! Квитанция обновлена.")
            elif payload == "pay_sbp":
                self.send_message(chat_id=chat_id, user_id=user_id, text="💳 Ссылка на оплату через СБП сформирована. Перевод поступает на спецсчет 40821 (103-ФЗ).")

    def run_polling(self):
        """Запуск цикла Long Polling"""
        me = self.get_me()
        print("=" * 60)
        print(f"🚀 Бот @{me.get('username')} успешно запущен и слушает события!")
        print(f"🔗 Имя: {me.get('name') or me.get('first_name')}")
        print(f"🆔 ID: {me.get('user_id') or me.get('id')}")
        print("=" * 60)

        marker = None
        while True:
            try:
                params = {"marker": marker} if marker else {}
                data = self._api_request("/updates", params=params)
                marker = data.get("marker", marker)
                for upd in data.get("updates", []):
                    self.process_update(upd)
            except urllib.error.HTTPError as e:
                print(f"HTTP Ошибка {e.code}: {e.reason}")
                time.sleep(3)
            except Exception as e:
                if "timed out" not in str(e).lower():
                    print("Ошибка соединения polling:", e)
                time.sleep(1)

if __name__ == "__main__":
    bot = MaxBotService()
    bot.run_polling()
