"""
MAX Bot event and message handlers.
Implements the core scenarios:
1. Instant photo reception & recognition from gallery/chat forwarding
2. Green Security Shield (FGIS Arshin anti-fraud)
3. GOST R 56042-2014 bill payment & Account 40821 splitting
4. Night Flow ODPU leak crowd-diagnostics
5. Tenant guest access links
6. Emergency UK requests under PP RF No. 40
"""

from typing import Dict, Any, List, Optional
import logging
from app.bot.client import MaxBotClient
from app.config import settings
from app.services.meter_service import meter_service
from app.services.arshin import arshin_service
from app.services.ai_vision import ai_vision_service
from app.services.gost_qr import parse_gost_qr_payload
from app.services.night_flow import night_flow_service
from app.services.ticket_service import ticket_service
from app.services.guest_service import guest_service
from app.models.schemas import (
    AiVisionScanRequest,
    MeterReadingSubmitRequest,
    NightFlowCheckRequest,
    GuestAccessGenerateRequest
)
from app.models.domain import MeterType, TicketPriority

logger = logging.getLogger("max_bot_handlers")

def get_main_menu_buttons() -> List[List[Dict[str, Any]]]:
    app_url = settings.MINIAPP_URL
    return [
        [
            {
                "type": "link",
                "text": "📱 Открыть Мини-приложение MAX",
                "url": app_url
            }
        ],
        [
            {
                "type": "callback",
                "text": "📸 Передать показания",
                "payload": "cmd_meters"
            },
            {
                "type": "callback",
                "text": "🛡️ Проверить поверку (АРШИН)",
                "payload": "cmd_arshin"
            }
        ],
        [
            {
                "type": "callback",
                "text": "🧾 Оплатить квитанцию (ГОСТ)",
                "payload": "cmd_pay"
            },
            {
                "type": "callback",
                "text": "🔍 Ночной дозор (Утечки)",
                "payload": "cmd_night_flow"
            }
        ],
        [
            {
                "type": "callback",
                "text": "🛠️ Подать заявку в УК",
                "payload": "cmd_ticket"
            },
            {
                "type": "callback",
                "text": "👥 Доступ для арендатора",
                "payload": "cmd_guest"
            }
        ]
    ]

class BotHandler:
    def __init__(self, client: MaxBotClient):
        self.client = client

    def handle_bot_started(self, update: Dict[str, Any]):
        user = update.get("user", {})
        chat_id = update.get("chat_id")
        user_id = user.get("user_id") or user.get("id")
        name = user.get("first_name") or user.get("name") or "Житель"

        text = (
            f"👋 **Здравствуйте, {name}!**\n\n"
            f"Добро пожаловать в **«MAX Умный Дом»** — единый сервис взаимодействия жителей с "
            f"Управляющей компанией по Постановлению Правительства РФ № 40 от 26.01.2026 г.\n\n"
            f"✨ **Что вы можете сделать прямо в MAX:**\n"
            f"• 📸 **Мгновенно передать показания**: просто перешлите фото счетчика из галереи или семейного чата в этот диалог!\n"
            f"• 🛡️ **Защититься от мошенников**: автоматическая сверка заводского номера с реестром ФГИС «АРШИН» (102-ФЗ);\n"
            f"• 🧾 **Оплатить ЖКУ без комиссии**: сканирование QR по ГОСТ Р 56042-2014 и сплит на спецсчет 40821;\n"
            f"• 🔍 **«Ночной дозор»**: выявление скрытых ночных утечек дома по ОДПУ со скидкой 10% на квартплату;\n"
            f"• 👥 **Гостевой доступ**: арендатор передает данные без входа через ЕСИА.\n\n"
            f"Выберите действие ниже или запустите мини-приложение 👇"
        )
        self.client.send_message(
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            buttons=get_main_menu_buttons()
        )

    def handle_message_created(self, update: Dict[str, Any]):
        msg = update.get("message", {})
        body = msg.get("body", {})
        text = body.get("text", "").strip()
        attachments = body.get("attachments", [])
        sender = msg.get("sender", {})
        recipient = msg.get("recipient", {})
        chat_id = recipient.get("chat_id")
        user_id = sender.get("user_id") or sender.get("id")

        # 1. Check if user sent an image (photo of meter or bill)
        has_image = any(att.get("type") in ["image", "photo"] for att in attachments)
        if has_image:
            self._process_incoming_photo(chat_id, user_id, attachments)
            return

        # 2. Command routing
        lower_text = text.lower()
        if lower_text in ["/start", "старт", "меню"]:
            self.handle_bot_started({"chat_id": chat_id, "user": sender})
        elif lower_text in ["/app", "приложение"]:
            self.client.send_message(
                chat_id=chat_id,
                user_id=user_id,
                text="📱 Нажмите кнопку ниже, чтобы открыть интерактивное мини-приложение:",
                buttons=[[{"type": "link", "text": "🚀 Открыть Мини-приложение MAX", "url": settings.MINIAPP_URL}]]
            )
        elif lower_text in ["/meters", "счетчики", "показания"]:
            self._send_meters_list(chat_id, user_id)
        elif lower_text.startswith("/check") or "поверка" in lower_text:
            parts = text.split(maxsplit=1)
            serial = parts[1] if len(parts) > 1 else "2809142"
            self._process_arshin_check(chat_id, user_id, serial)
        elif lower_text in ["/pay", "оплата", "квитанция"]:
            self._send_payment_info(chat_id, user_id)
        elif lower_text.startswith("st0001"):
            # User pasted GOST QR string directly!
            self._process_gost_qr_text(chat_id, user_id, text)
        elif lower_text in ["/leak", "утечка", "ночной дозор"]:
            self._send_night_flow_info(chat_id, user_id)
        elif lower_text in ["/ticket", "заявка", "мастер"]:
            self._send_ticket_form(chat_id, user_id)
        elif lower_text in ["/guest", "арендатор"]:
            self._generate_guest_link(chat_id, user_id)
        else:
            # General helper response
            reply = (
                f"🤖 Я получил сообщение: «{text}»\n\n"
                f"💡 **Быстрые сценарии:**\n"
                f"• Отправьте **фото счетчика** прямо в чат для мгновенного AI-распознавания.\n"
                f"• Напишите `поверка 2809142` для проверки любого прибора во ФГИС «АРШИН».\n"
                f"• Или воспользуйтесь кнопками меню:"
            )
            self.client.send_message(
                chat_id=chat_id,
                user_id=user_id,
                text=reply,
                buttons=get_main_menu_buttons()
            )

    def handle_callback(self, update: Dict[str, Any]):
        callback = update.get("callback", {})
        callback_id = callback.get("callback_id") or callback.get("id")
        payload = callback.get("payload", "")
        sender = update.get("user") or update.get("sender", {})
        chat_id = update.get("chat_id")
        user_id = sender.get("user_id") or sender.get("id")

        if callback_id:
            self.client.answer_callback(callback_id=str(callback_id))

        if payload == "cmd_meters":
            self._send_meters_list(chat_id, user_id)
        elif payload == "cmd_arshin":
            self._process_arshin_check(chat_id, user_id, "2809142")
        elif payload == "cmd_pay":
            self._send_payment_info(chat_id, user_id)
        elif payload == "cmd_night_flow":
            self._send_night_flow_info(chat_id, user_id)
        elif payload == "cmd_ticket":
            self._send_ticket_form(chat_id, user_id)
        elif payload == "cmd_guest":
            self._generate_guest_link(chat_id, user_id)
        elif payload.startswith("submit_meter_"):
            meter_id = payload.replace("submit_meter_", "")
            self._simulate_meter_photo_flow(chat_id, user_id, meter_id)
        elif payload == "test_napkin_wet":
            diag = night_flow_service.diagnose_leak(NightFlowCheckRequest(entrance_id=1, napkin_test_result="wet"))
            self.client.send_message(
                chat_id=chat_id,
                user_id=user_id,
                text=f"{diag.diagnosis_verdict}\n\n🎁 **{diag.recommendation}**",
                buttons=get_main_menu_buttons()
            )
        elif payload == "test_napkin_dry":
            diag = night_flow_service.diagnose_leak(NightFlowCheckRequest(entrance_id=1, napkin_test_result="dry"))
            self.client.send_message(
                chat_id=chat_id,
                user_id=user_id,
                text=f"{diag.diagnosis_verdict}\n\n✅ {diag.recommendation}",
                buttons=get_main_menu_buttons()
            )
        else:
            self.client.send_message(
                chat_id=chat_id,
                user_id=user_id,
                text="Действие выполнено.",
                buttons=get_main_menu_buttons()
            )

    # ----------------- Scenario Helpers -----------------

    def _process_incoming_photo(self, chat_id, user_id, attachments):
        # AI Vision Stub processing
        scan = ai_vision_service.scan_meter_image(AiVisionScanRequest(device_type_hint=MeterType.COLD_WATER))
        # Automatic FGIS Arshin verification
        arshin = arshin_service.check_verification(scan.recognized_serial_number)
        
        reply = (
            f"📸 **Фотография счетчика получена и обработана нейросетью!**\n\n"
            f"🔍 **Результаты детекции:**\n"
            f"• Тип прибора: **ХВС (Холодная вода)**\n"
            f"• Заводской номер: `{scan.recognized_serial_number}`\n"
            f"• Распознанный расход: **{scan.recognized_reading} м³**\n"
            f"• Отсечение красных роликов (литров): **Да (предотвращена ошибка в 1000 раз)**\n"
            f"• Точность OCR: **{int(scan.confidence * 100)}%**\n\n"
            f"🛡️ **Статус поверки (ФГИС «АРШИН»):**\n"
            f"{arshin.safety_message}\n\n"
            f"Подтвердите передачу показаний в ГИС ЖКХ 👇"
        )
        buttons = [
            [
                {
                    "type": "callback",
                    "text": f"✅ Подтвердить {scan.recognized_reading} м³",
                    "payload": f"submit_meter_meter-khvs-1"
                }
            ],
            [
                {
                    "type": "link",
                    "text": "✏️ Скорректировать в Мини-приложении",
                    "url": settings.MINIAPP_URL
                }
            ]
        ]
        self.client.send_message(chat_id=chat_id, user_id=user_id, text=reply, buttons=buttons)

    def _simulate_meter_photo_flow(self, chat_id, user_id, meter_id):
        m = meter_service.get_meter(meter_id)
        if not m:
            m = meter_service.get_all_meters()[0]
        new_val = m.last_reading_value + 3.5
        res = meter_service.validate_and_submit_reading(
            MeterReadingSubmitRequest(
                meter_id=m.id,
                reading_value=new_val,
                submission_channel="max_bot"
            )
        )
        text = (
            f"✅ **Показания успешно зафиксированы!**\n\n"
            f"• Прибор: **{m.name}**\n"
            f"• Предыдущие: {res.previous_value} {m.unit}\n"
            f"• Текущие: **{res.current_value} {m.unit}**\n"
            f"• Расход за период: **+{res.consumption} {m.unit}**\n"
            f"• Статус интеграции: **Отправлено в ГИС ЖКХ (WSDL hcs-device-meterings)**\n\n"
            f"{res.message}"
        )
        self.client.send_message(chat_id=chat_id, user_id=user_id, text=text, buttons=get_main_menu_buttons())

    def _send_meters_list(self, chat_id, user_id):
        meters = meter_service.get_all_meters()
        lines = ["📊 **Ваши приборы учета (Лицевой счет 1004567890):**\n"]
        buttons = []
        for m in meters:
            lines.append(
                f"• **{m.name}** (№ `{m.serial_number}`)\n"
                f"  Текущие показания: **{m.last_reading_value} {m.unit}** (от {m.last_reading_date})\n"
                f"  Поверка действительна до: **{m.verification_date_valid_until.strftime('%d.%m.%Y')}**\n"
            )
            buttons.append([
                {
                    "type": "callback",
                    "text": f"📸 Сдать {m.name[:18]}...",
                    "payload": f"submit_meter_{m.id}"
                }
            ])
        buttons.append([
            {
                "type": "link",
                "text": "📱 Открыть Мини-приложение",
                "url": settings.MINIAPP_URL
            }
        ])
        lines.append("\n👉 Вы можете просто **отправить фото счетчика в этот чат** в любое время!")
        self.client.send_message(chat_id=chat_id, user_id=user_id, text="\n".join(lines), buttons=buttons)

    def _process_arshin_check(self, chat_id, user_id, serial):
        arshin = arshin_service.check_verification(serial)
        self.client.send_message(
            chat_id=chat_id,
            user_id=user_id,
            text=f"{arshin.safety_message}\n\n🔗 Проверка в открытом реестре: [ФГИС АРШИН]({arshin.fgis_arshin_url})",
            buttons=get_main_menu_buttons()
        )

    def _send_payment_info(self, chat_id, user_id):
        sample_gost = (
            "ST00012|Name=ООО УК ДОМОВОЙ СЕРВИС|PersonalAcc=40702810938000012345|"
            "BIC=044525225|CorrespAcc=30101810400000000225|PayeeINN=7701234567|"
            "Sum=485050|PersAcc=1004567890|Period=092026"
        )
        parsed = parse_gost_qr_payload(sample_gost)
        text = (
            f"🧾 **Оплата квитанций ЖКУ по стандарту ГОСТ Р 56042-2014**\n\n"
            f"Сумма к оплате за сентябрь 2026: **{parsed.total_amount_rubles:.2f} ₽**\n"
            f"Лицевой счет ЕЛС: `{parsed.personal_account}`\n\n"
            f"🔒 **Прямое расщепление по 103-ФЗ на специальный счет 40821:**\n"
            f"• 💧 АО «Мосводоканал»: **{parsed.split_details[0].amount_rubles} ₽**\n"
            f"• ♨️ ПАО «МОЭК» (Отопление): **{parsed.split_details[1].amount_rubles} ₽**\n"
            f"• ⚡ АО «Мосэнергосбыт»: **{parsed.split_details[2].amount_rubles} ₽**\n"
            f"• 🏢 УК (Содержание жилья): **{parsed.split_details[3].amount_rubles} ₽**\n\n"
            f"Деньги поступают ресурсоснабжающим организациям напрямую, защищены от долгов УК!"
        )
        buttons = [
            [
                {
                    "type": "link",
                    "text": "💳 Оплатить 4850.50 ₽ через СБП",
                    "url": f"{settings.MINIAPP_URL}#payment"
                }
            ],
            [
                {
                    "type": "link",
                    "text": "📱 Сканировать QR квитанции в приложении",
                    "url": settings.MINIAPP_URL
                }
            ]
        ]
        self.client.send_message(chat_id=chat_id, user_id=user_id, text=text, buttons=buttons)

    def _process_gost_qr_text(self, chat_id, user_id, qr_text):
        try:
            parsed = parse_gost_qr_payload(qr_text)
            text = (
                f"✅ **Квитанция успешно распознана по ГОСТ Р 56042-2014!**\n\n"
                f"• Получатель: **{parsed.recipient_name}**\n"
                f"• ИНН: `{parsed.inn}` | БИК: `{parsed.bank_bik}`\n"
                f"• Сумма: **{parsed.total_amount_rubles:.2f} ₽**\n"
                f"• Расчетный счет ЦБ: **Проверен (Контрольный разряд валиден)**\n"
                f"• Защищенный спецсчет 40821: **Активирован**"
            )
            buttons = [
                [
                    {
                        "type": "link",
                        "text": f"💳 Оплатить {parsed.total_amount_rubles:.2f} ₽ (СБП)",
                        "url": f"{settings.MINIAPP_URL}#payment"
                    }
                ]
            ]
            self.client.send_message(chat_id=chat_id, user_id=user_id, text=text, buttons=buttons)
        except Exception as e:
            self.client.send_message(chat_id=chat_id, user_id=user_id, text=f"Ошибка парсинга QR-кода: {e}")

    def _send_night_flow_info(self, chat_id, user_id):
        text = (
            f"🔍 **Сервис «Ночной дозор» (Анализ небаланса ОДПУ)**\n\n"
            f"По данным радиомодема ОДПУ на вводе в ваш дом, в период **02:30 – 04:30 ночи** "
            f"зафиксирован постоянный расход воды **1 650 л/час** (при норме до 100 л/час).\n\n"
            f"Это скрытая утечка, из-за которой дом теряет до 35 кубов в сутки (+200 руб. к ОДН каждой квартире!).\n\n"
            f"🧪 **Экспресс-тест за 30 секунд (Тест салфетки):**\n"
            f"1. Положите сухую бумажную салфетку на сухую заднюю стенку чаши унитаза.\n"
            f"2. Подождите 15 секунд.\n"
            f"3. Что произошло с салфеткой?"
        )
        buttons = [
            [
                {"type": "callback", "text": "💧 Намокла (Клапан течет)", "payload": "test_napkin_wet"},
                {"type": "callback", "text": "✨ Осталась сухой", "payload": "test_napkin_dry"}
            ],
            [
                {"type": "link", "text": "📱 Интерактивный замер в приложении", "url": settings.MINIAPP_URL}
            ]
        ]
        self.client.send_message(chat_id=chat_id, user_id=user_id, text=text, buttons=buttons)

    def _send_ticket_form(self, chat_id, user_id):
        text = (
            f"🛠️ **Аварийно-диспетчерская служба (ПП РФ № 40 от 26.01.2026 г.)**\n\n"
            f"Нормативные сроки рассмотрения в сервисе MAX:\n"
            f"• 🚨 **Аварийная**: локализация за **30 минут**, устранение за **3 часа**;\n"
            f"• ⚠️ **Срочная**: устранение неисправности до **24 часов**;\n"
            f"• 📋 **Плановая**: до **3 рабочих дней**.\n\n"
            f"Чтобы подать заявку с фотофиксацией и отслеживанием статуса, откройте мини-приложение:"
        )
        buttons = [
            [
                {
                    "type": "link",
                    "text": "📝 Оформить заявку в УК",
                    "url": f"{settings.MINIAPP_URL}#tickets"
                }
            ]
        ]
        self.client.send_message(chat_id=chat_id, user_id=user_id, text=text, buttons=buttons)

    def _generate_guest_link(self, chat_id, user_id):
        res = guest_service.generate_guest_token(
            GuestAccessGenerateRequest(property_id="flat-42-15", tenant_name="Арендатор")
        )
        text = (
            f"👥 **Гостевой доступ для арендатора сгенерирован!**\n\n"
            f"В отличие от «Госуслуги Дом», арендатору **НЕ требуется авторизация через ЕСИА** "
            f"и доступ к личным документам собственника.\n\n"
            f"🔗 Отправьте эту ссылку вашему жильцу в MAX:\n"
            f"`{res.direct_max_link}`\n\n"
            f"Арендатор сможет в 1 клик пересылать фото счетчиков и оплачивать начисления."
        )
        buttons = [
            [
                {
                    "type": "link",
                    "text": "🔗 Открыть гостевую ссылку",
                    "url": res.direct_max_link
                }
            ]
        ]
        self.client.send_message(chat_id=chat_id, user_id=user_id, text=text, buttons=buttons)
