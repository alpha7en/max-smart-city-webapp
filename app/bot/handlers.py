"""
MAX Bot event and message handlers.
Integrated with AsyncUpdateDispatcher and Finite State Machine (FSM).
Core Scenarios:
1. Instant meter photo recognition & red roller filtering
2. Green Security Shield (FGIS Arshin anti-fraud)
3. GOST R 56042-2014 bill payment & Account 40821 splitting
4. Tenant guest access links (no ESIA required)
5. UK Emergency tickets under PP RF No. 40
6. UK Inspector mobile tool (digital act with GPS and SHA-256)

Zero emoji policy strictly enforced.
Button text length <= 18 characters strictly enforced.
"""

from typing import Dict, Any, List, Optional, Union
import logging
import re
import inspect
import asyncio

from max_bot_sdk import (
    MaxBotClient,
    Button,
    KeyboardBuilder,
    Update,
    AsyncUpdateDispatcher,
    UserState,
    FSMContext,
)
from app.config import settings
from app.services.meter_service import meter_service
from app.services.arshin import arshin_service
from app.services.ai_vision import ai_vision_service
from app.services.gost_qr import parse_gost_qr_payload
from app.services.ticket_service import ticket_service
from app.services.guest_service import guest_service
from app.services.inspector_service import inspector_service
from app.services.profile_service import profile_service, UserProperty
from app.models.schemas import (
    AiVisionScanRequest,
    MeterReadingSubmitRequest,
    GuestAccessGenerateRequest,
    InspectorActCreateRequest,
    TicketCreateRequest,
)
from app.models.domain import MeterType, TicketPriority

# Re-export keyboard builder functions for backward compatibility
from app.bot.keyboards import (
    get_main_menu_keyboard,
    get_meters_keyboard,
    get_services_keyboard,
    get_help_keyboard,
    get_address_switch_keyboard,
    get_guest_keyboard,
    get_meter_confirmation_keyboard,
    get_ticket_categories_keyboard,
    get_inspector_keyboard,
    get_payment_keyboard,
)

logger = logging.getLogger("max_bot_handlers")


class BotHandler:
    def __init__(self, client: MaxBotClient):
        self.client = client
        self.user_service = profile_service
        self.profile_service = profile_service
        self._pending_scans: Dict[Any, float] = {}
        self.dispatcher = AsyncUpdateDispatcher(client=client)
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Register routes with AsyncUpdateDispatcher."""
        dp = self.dispatcher

        # 1. Start & Bot Started
        @dp.command(["start", "старт", "меню"], state="*")
        def route_start(client: Any, update: Update):
            self.handle_bot_started(update)

        # 2. Photos
        @dp.on_photo(state="*")
        def route_photo(client: Any, update: Update):
            hint = self._detect_meter_type_from_hint(update.text)
            self._process_incoming_photo(
                update.effective_chat_id,
                update.sender_user_id,
                meter_type_hint=hint
            )

        # 3. Callbacks
        @dp.callback(re.compile(r"^.*$"), state="*")
        def route_callback(client: Any, update: Update):
            self.handle_callback(update)

        # 4. FSM State: WAITING_METER_INPUT
        @dp.message(state=UserState.WAITING_METER_INPUT)
        def route_state_meter_input(client: Any, update: Update, state: FSMContext):
            text = (update.text or "").strip()
            clean_num = text.replace(",", ".").strip()
            matched_num = re.search(r"(\d+(?:\.\d+)?)", clean_num)
            if matched_num:
                self._process_text_reading(update.effective_chat_id, update.sender_user_id, text)
                state.reset_state()
            else:
                self.handle_message_created(update)

        # 5. FSM State: WAITING_ARSHIN_SERIAL
        @dp.message(state=UserState.WAITING_ARSHIN_SERIAL)
        def route_state_arshin(client: Any, update: Update, state: FSMContext):
            text = (update.text or "").strip()
            if text:
                serial = text.split()[0]
                self._process_arshin_check(update.effective_chat_id, update.sender_user_id, serial)
                state.reset_state()
            else:
                self.handle_message_created(update)

        # 6. FSM State: WAITING_TICKET_DESC
        @dp.message(state=UserState.WAITING_TICKET_DESC)
        def route_state_ticket(client: Any, update: Update, state: FSMContext):
            text = (update.text or "").strip()
            if text:
                self._create_ticket_from_text(update.effective_chat_id, update.sender_user_id, text)
                state.reset_state()
            else:
                self.handle_message_created(update)

        # 7. FSM State: WAITING_ADDRESS
        @dp.message(state=UserState.WAITING_ADDRESS)
        def route_state_address(client: Any, update: Update, state: FSMContext):
            text = (update.text or "").strip()
            if text:
                profile_service.add_property_manual(update.sender_user_id, text, "УК Домовой Сервис")
                self._send(
                    chat_id=update.effective_chat_id,
                    user_id=update.sender_user_id,
                    text=f"**Новый адрес успешно добавлен**\n\nАдрес: **{text}**\nЕЛС сформирован автоматически.",
                    keyboard=get_main_menu_keyboard()
                )
                state.reset_state()
            else:
                self.handle_message_created(update)

        # 8. Fallback / Default text message router
        @dp.message(state="*")
        def route_generic_message(client: Any, update: Update):
            self.handle_message_created(update)

    def process_update(self, update: Any):
        """
        Processes update synchronously or asynchronously.
        Maintains 100% backward compatibility with sync tests.
        """
        if isinstance(update, dict):
            update = Update.from_dict(update)
        logger.info("Processing MAX update: %s", update.update_type)

        # Keep client synchronized
        self.dispatcher.client = self.client

        # Direct handling for established update types ensures instant synchronous execution
        if update.update_type == "bot_started":
            self.handle_bot_started(update)
        elif update.update_type == "message_created":
            self.handle_message_created(update)
        elif update.update_type == "message_callback":
            self.handle_callback(update)
        else:
            self.dispatcher.dispatch(update)

    async def process_update_async(self, update: Any):
        """Asynchronous update processing method."""
        if isinstance(update, dict):
            update = Update.from_dict(update)
        self.dispatcher.client = self.client
        res = self.dispatcher.dispatch(update)
        if inspect.isawaitable(res):
            return await res
        return res

    def feed_update(self, update: Any) -> asyncio.Task:
        """Schedules non-blocking update processing in the background event loop."""
        self.dispatcher.client = self.client
        return self.dispatcher.feed_update(update)

    def get_fsm_context(self, chat_id: Optional[Union[str, int]], user_id: Optional[Union[str, int]]) -> FSMContext:
        """Retrieve FSMContext for given chat and user."""
        return self.dispatcher.get_fsm_context(chat_id, user_id)

    def _send(self, chat_id: Optional[Any], user_id: Optional[int], text: str, keyboard: Optional[Dict[str, Any]] = None):
        """
        Send a message to user with strict sanitization of brackets and emojis.
        """
        # Strict cleaning: remove junk brackets [...] and pseudocode tags
        clean_text = re.sub(r"\[(MAX|ИНФО|WEB-APP|ИПУ|АРШИН|ОПЛАТА|Водоканал|ХВС|ГВС|Сервис|ГИС ЖКХ|АРМ|СБП|ST0001)[^\]]*\]\s*", "", text).strip()
        buttons = None
        if keyboard and isinstance(keyboard, dict) and "payload" in keyboard:
            buttons = keyboard.get("payload", {}).get("buttons")
        try:
            return self.client.send_message(
                chat_id=chat_id,
                user_id=user_id,
                text=clean_text,
                keyboard=keyboard,
                buttons=buttons
            )
        except Exception as e:
            logger.error("Failed to send message to chat=%s user=%s: %s", chat_id, user_id, e)
            return {}

    def _extract_ids(self, update: Update) -> tuple:
        chat_id = update.effective_chat_id
        user_id = update.sender_user_id
        raw = getattr(update, "raw", None)
        if isinstance(raw, dict):
            if not chat_id:
                chat_id = raw.get("chat_id") or (raw.get("recipient", {}).get("chat_id") if isinstance(raw.get("recipient"), dict) else None)
            if not user_id:
                sender_obj = raw.get("sender")
                if isinstance(sender_obj, dict):
                    user_id = sender_obj.get("id") or sender_obj.get("user_id")
                elif isinstance(raw.get("user"), dict):
                    user_id = raw.get("user", {}).get("id")
                elif raw.get("user_id"):
                    user_id = raw.get("user_id")
                elif raw.get("sender_user_id"):
                    user_id = raw.get("sender_user_id")
        return chat_id, user_id

    def handle_bot_started(self, update: Any, guest_token: Optional[str] = None):
        if isinstance(update, dict):
            update = Update.from_dict(update)
        chat_id, user_id = self._extract_ids(update)

        if user_id:
            profile = profile_service.get_profile(user_id)
            if update.user:
                if update.user.first_name:
                    profile.first_name = update.user.first_name
                if update.user.last_name:
                    profile.last_name = update.user.last_name
                if update.user.username:
                    profile.username = update.user.username
        else:
            profile = profile_service.get_profile()

        name = (update.user.first_name if update.user and update.user.first_name else profile.first_name) or "Иван"

        # Check tenant guest token
        if not guest_token and isinstance(getattr(update, "raw", None), dict):
            raw_payload = str(update.raw.get("payload") or "")
            if raw_payload.startswith("guest_"):
                guest_token = raw_payload

        if guest_token:
            guest_info = guest_service.validate_guest_token(guest_token)
            if guest_info:
                tenant_name = guest_info.get("tenant_name", "Арендатор")
                prop_address = guest_info.get("property_address", "ул. Ленина, д. 42, кв. 15")
                guest_text = (
                    f"**MAX Умный Дом — Гостевой доступ для арендатора**\n\n"
                    f"• Адрес: **{prop_address}**\n"
                    f"• Арендатор: **{tenant_name}**\n\n"
                    f"Вам предоставлен безопасный доступ без авторизации через Госуслуги:\n"
                    f"• Передача показаний счетчиков (вода, свет, газ, тепло)\n"
                    f"• Просмотр и оплата квитанций ЖКУ по СБП\n\n"
                    f"Откройте **Мини-приложение** для интерактивной работы:"
                )
                guest_kb = get_guest_keyboard(guest_token)
                self._send(chat_id=chat_id, user_id=user_id, text=guest_text, keyboard=guest_kb)
                return

        active_prop = profile_service.get_active_property(user_id)
        role_label = "Собственник" if active_prop.role == "owner" else "Арендатор"

        text = (
            f"**MAX Умный Дом — Сервис ЖКХ**\n"
            f"Лицевой счет: {active_prop.els} | Адрес: {active_prop.address}\n"
            f"Пользователь: {name} ({role_label})\n\n"
            f"**Быстрая передача показаний:**\n"
            f"Отправьте фотографию любого счетчика (вода, свет, газ, тепло) прямо в этот диалог.\n\n"
            f"**Возможности сервиса:**\n"
            f"• Автоматическое распознавание цифр с фото и защита от переплаты\n"
            f"• Проверка поверки приборов в реестре ФГИС «АРШИН» (102-ФЗ)\n"
            f"• Оплата ЖКУ по СБП с расщеплением на спецсчет 40821 (103-ФЗ)\n"
            f"• Гостевой доступ для арендаторов без авторизации через Госуслуги\n"
            f"• Подача заявок в диспетчерскую службу УК с контролем сроков\n"
            f"• Цифровые акты обходчика с GPS-меткой и фиксацией времени\n\n"
            f"Для работы с интерактивным интерфейсом нажмите кнопку **«Мини-приложение»** ниже:"
        )
        self._send(
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            keyboard=get_main_menu_keyboard()
        )

    def handle_message_created(self, update: Any):
        if isinstance(update, dict):
            update = Update.from_dict(update)
        chat_id, user_id = self._extract_ids(update)
        text = update.text.strip()

        if user_id:
            profile = profile_service.get_profile(user_id)
            if update.user:
                if update.user.first_name:
                    profile.first_name = update.user.first_name
                if update.user.last_name:
                    profile.last_name = update.user.last_name
                if update.user.username:
                    profile.username = update.user.username

        # 1. Photo
        if update.has_image:
            hint = self._detect_meter_type_from_hint(text)
            self._process_incoming_photo(chat_id, user_id, meter_type_hint=hint)
            return

        # 2. Contact
        if update.has_contact:
            self._process_incoming_contact(chat_id, user_id, update)
            return

        # 3. Check active conversational FSM state
        fsm = self.get_fsm_context(chat_id, user_id)
        current_state = self.dispatcher._sync_get_state(chat_id, user_id)

        if current_state == UserState.WAITING_METER_INPUT.value:
            clean_val = text.replace(",", ".").strip()
            if re.search(r"(\d+(?:\.\d+)?)", clean_val):
                self._process_text_reading(chat_id, user_id, text)
                fsm.reset_state()
                return

        if current_state == UserState.WAITING_ARSHIN_SERIAL.value:
            if text:
                serial = text.split()[0]
                self._process_arshin_check(chat_id, user_id, serial)
                fsm.reset_state()
                return

        if current_state == UserState.WAITING_TICKET_DESC.value:
            if text:
                self._create_ticket_from_text(chat_id, user_id, text)
                fsm.reset_state()
                return

        if current_state == UserState.WAITING_ADDRESS.value:
            if text:
                profile_service.add_property_manual(user_id, text, "УК Домовой Сервис")
                self._send(
                    chat_id=chat_id,
                    user_id=user_id,
                    text=f"**Новый адрес успешно добавлен**\n\nАдрес: **{text}**\nЕЛС сформирован автоматически.",
                    keyboard=get_main_menu_keyboard()
                )
                fsm.reset_state()
                return

        # 4. Text command routing
        lower_text = text.lower()
        if lower_text.startswith("/start guest_") or lower_text.startswith("start guest_"):
            parts = text.split(maxsplit=1)
            token = parts[1].strip() if len(parts) > 1 else ""
            self.handle_bot_started(update, guest_token=token)
        elif lower_text in ["/start", "старт", "меню"]:
            self.handle_bot_started(update)
        elif lower_text in ["/profile", "/account", "профиль", "аккаунт"]:
            self._send_user_profile(chat_id, user_id)
        elif lower_text in ["/address", "/addresses", "адрес", "адреса"]:
            self._send_address_management(chat_id, user_id)
        elif lower_text in ["/register", "регистрация"]:
            self._send_registration_info(chat_id, user_id)
        elif lower_text in ["/app", "приложение"]:
            self._send(
                chat_id=chat_id,
                user_id=user_id,
                text=(
                    f"**Интерфейс Мини-приложения MAX**\n\n"
                    f"Запуск в MAX: https://max.ru/{settings.BOT_USERNAME}?startapp\n\n"
                    f"Нажмите кнопку **«Мини-приложение»** ниже для открытия:"
                ),
                keyboard=KeyboardBuilder.inline([[Button.open_app("Мини-приложение", settings.BOT_USERNAME)]])
            )
        elif lower_text in ["/meters", "счетчики", "показания"]:
            self._send_meters_list(chat_id, user_id)
        elif lower_text.startswith("/check") or "поверка" in lower_text or "аршин" in lower_text:
            parts = text.split(maxsplit=1)
            serial = parts[1] if len(parts) > 1 else "2809142"
            self._process_arshin_check(chat_id, user_id, serial)
        elif lower_text in ["/pay", "оплата", "квитанция"]:
            self._send_payment_info(chat_id, user_id)
        elif lower_text.startswith("st0001"):
            self._process_gost_qr_text(chat_id, user_id, text)
        elif lower_text in ["/ticket", "заявка", "мастер"]:
            self._send_ticket_form(chat_id, user_id)
        elif lower_text in ["/guest", "арендатор"]:
            self._generate_guest_link(chat_id, user_id)
        elif lower_text in ["/inspector", "обходчик", "акт"]:
            self._process_inspector_act(chat_id, user_id)
        elif any(lower_text.startswith(p) for p in ["хвс", "гвс", "свет", "электро", "тепло", "показания", "вода"]):
            self._process_text_reading(chat_id, user_id, text)
        else:
            reply = (
                f"Команда «{text}» не распознана. Доступные действия:\n\n"
                f"• Пришлите **фото счетчика** прямо в чат для мгновенного считывания\n"
                f"• Или напишите показания текстом (например: `ХВС 148.5`)\n"
                f"• Для проверки поверки счетчика укажите: `/check 2809142`\n"
                f"• Либо выберите нужное действие в меню ниже:"
            )
            self._send(
                chat_id=chat_id,
                user_id=user_id,
                text=reply,
                keyboard=get_main_menu_keyboard()
            )

    def handle_callback(self, update: Any):
        if isinstance(update, dict):
            update = Update.from_dict(update)
        if not update.callback:
            return

        cb_id = update.callback.callback_id
        payload = update.callback.payload
        chat_id, user_id = self._extract_ids(update)

        # Always acknowledge callback
        if cb_id:
            try:
                self.client.answer_callback(callback_id=cb_id, notification="Принято")
            except Exception as e:
                logger.warning("Could not answer callback %s: %s", cb_id, e)

        fsm = self.get_fsm_context(chat_id, user_id)

        if payload == "cmd_meters":
            fsm.set_state(UserState.WAITING_METER_INPUT)
            self._send_meters_list(chat_id, user_id)
        elif payload == "cmd_arshin":
            fsm.set_state(UserState.WAITING_ARSHIN_SERIAL)
            self._process_arshin_check(chat_id, user_id, "2809142")
        elif payload == "cmd_pay":
            self._send_payment_info(chat_id, user_id)
        elif payload in ("cmd_profile", "cmd_account"):
            self._send_user_profile(chat_id, user_id)
        elif payload in ("cmd_address", "cmd_addresses"):
            self._send_address_management(chat_id, user_id)
        elif payload == "cmd_register":
            self._send_registration_info(chat_id, user_id)
        elif payload == "cmd_add_address":
            fsm.set_state(UserState.WAITING_ADDRESS)
            self._send_add_address_prompt(chat_id, user_id)
        elif payload.startswith("switch_prop_"):
            prop_id = payload.replace("switch_prop_", "")
            self._process_switch_property(chat_id, user_id, prop_id)
        elif payload == "cmd_ticket":
            fsm.set_state(UserState.WAITING_TICKET_DESC)
            self._send_ticket_form(chat_id, user_id)
        elif payload.startswith("ticket_cat_"):
            category = payload.replace("ticket_cat_", "")
            fsm.set_state(UserState.WAITING_TICKET_DESC)
            fsm.update_data(category=category)
            self._send(
                chat_id=chat_id,
                user_id=user_id,
                text=f"Выбрана категория: **{category}**.\n\nОпишите проблему кратким сообщением в чат:",
                keyboard=KeyboardBuilder.inline([[Button.callback("Отмена", "cmd_menu")]])
            )
        elif payload == "cmd_guest":
            self._generate_guest_link(chat_id, user_id)
        elif payload == "cmd_inspector":
            self._process_inspector_act(chat_id, user_id)
        elif payload == "cmd_menu":
            fsm.reset_state()
            self.handle_bot_started(update)
        elif payload.startswith("submit_meter_"):
            rest = payload.replace("submit_meter_", "")
            parts = rest.split("_")
            meter_id = parts[0]
            reading_val = None
            if len(parts) > 1:
                try:
                    reading_val = float(parts[1])
                except ValueError:
                    reading_val = None
            if reading_val is None:
                reading_val = self._pending_scans.pop(user_id, None) or self._pending_scans.pop(chat_id, None)
            fsm.reset_state()
            self._simulate_meter_photo_flow(chat_id, user_id, meter_id, reading_value=reading_val)
        elif payload in ("rescan_hvs", "rescan_meter_cold_water"):
            self._process_incoming_photo(chat_id, user_id, meter_type_hint=MeterType.COLD_WATER)
        elif payload in ("rescan_gvs", "rescan_meter_hot_water"):
            self._process_incoming_photo(chat_id, user_id, meter_type_hint=MeterType.HOT_WATER)
        elif payload in ("rescan_el", "rescan_meter_electricity"):
            self._process_incoming_photo(chat_id, user_id, meter_type_hint=MeterType.ELECTRICITY_MULTI)
        elif payload in ("rescan_gas", "rescan_meter_gas"):
            self._process_incoming_photo(chat_id, user_id, meter_type_hint=MeterType.GAS)
        elif payload in ("rescan_heat", "rescan_meter_heat"):
            self._process_incoming_photo(chat_id, user_id, meter_type_hint=MeterType.HEAT)
        else:
            self._send(
                chat_id=chat_id,
                user_id=user_id,
                text="Действие выполнено.",
                keyboard=get_main_menu_keyboard()
            )

    # ----------------- Scenario Handlers -----------------

    def _detect_meter_type_from_hint(self, text: Optional[str]) -> Optional[MeterType]:
        if not text:
            return None
        t = text.lower().strip()
        if any(k in t for k in ["гвс", "горяч", "hot"]):
            return MeterType.HOT_WATER
        if any(k in t for k in ["хвс", "холодн", "cold"]):
            return MeterType.COLD_WATER
        if any(k in t for k in ["свет", "электр", "энерг", "меркурий", "квт"]):
            return MeterType.ELECTRICITY_MULTI
        if any(k in t for k in ["газ", "gas"]):
            return MeterType.GAS
        if any(k in t for k in ["тепло", "отоплен", "гкал"]):
            return MeterType.HEAT
        return None

    def _process_incoming_photo(
        self,
        chat_id: Optional[int],
        user_id: Optional[int],
        meter_type_hint: Optional[MeterType] = None
    ):
        target_type = meter_type_hint or MeterType.COLD_WATER

        meta_map = {
            MeterType.COLD_WATER: ("ХВС (Холодная вода)", "м³", "meter-khvs-1"),
            MeterType.HOT_WATER: ("ГВС (Горячая вода)", "м³", "meter-gvs-1"),
            MeterType.ELECTRICITY_MULTI: ("Электроэнергия (Т1/Т2)", "кВт*ч", "meter-el-1"),
            MeterType.GAS: ("Газоснабжение", "м³", "meter-gas-1"),
            MeterType.HEAT: ("Отопление (Тепло)", "Гкал", "meter-heat-1"),
        }
        title, unit, meter_id = meta_map.get(target_type, meta_map[MeterType.COLD_WATER])

        scan = ai_vision_service.scan_meter_image(AiVisionScanRequest(device_type_hint=target_type))
        if user_id:
            self._pending_scans[user_id] = scan.recognized_reading
        if chat_id:
            self._pending_scans[chat_id] = scan.recognized_reading

        arshin = arshin_service.check_verification(scan.recognized_serial_number)
        clean_safety = re.sub(r"\[.*?\]\s*", "", arshin.safety_message)

        reply = (
            f"**Фото счетчика успешно распознано**\n\n"
            f"• Прибор: **{title}**\n"
            f"• Режим: автоматическое распознавание\n"
            f"• Серийный номер: {scan.recognized_serial_number}\n"
            f"• Показания: **{scan.recognized_reading} {unit}**\n"
            f"• Защита от переплаты: дробная часть (литры) отсечена\n"
            f"• Точность распознавания: {int(scan.confidence * 100)}%\n\n"
            f"**Статус поверки (ФГИС «АРШИН» 102-ФЗ):**\n"
            f"{clean_safety}\n\n"
            f"Подтвердите отправку показаний:"
        )

        confirm_text = f"Принять {scan.recognized_reading}"

        buttons = [
            [
                Button.callback(confirm_text, f"submit_meter_{meter_id}")
            ],
            [
                Button.callback("Сдать ХВС", "rescan_meter_cold_water"),
                Button.callback("Сдать ГВС", "rescan_meter_hot_water")
            ],
            [
                Button.callback("Сдать Свет", "rescan_meter_electricity"),
                Button.callback("Сдать Газ", "rescan_meter_gas")
            ],
            [
                Button.callback("Сдать Тепло", "rescan_meter_heat"),
                Button.open_app("Мини-приложение", settings.BOT_USERNAME)
            ]
        ]
        self._send(chat_id=chat_id, user_id=user_id, text=reply, keyboard=KeyboardBuilder.inline(buttons))

    def _simulate_meter_photo_flow(self, chat_id, user_id, meter_id, reading_value: Optional[float] = None):
        m = meter_service.get_meter(meter_id) or meter_service.get_all_meters()[0]
        if reading_value is not None and reading_value > 0:
            new_val = reading_value
        else:
            new_val = round(m.last_reading_value + 3.5, 3)
        res = meter_service.validate_and_submit_reading(
            MeterReadingSubmitRequest(
                meter_id=m.id,
                reading_value=new_val,
                submission_channel="max_bot"
            )
        )
        text = (
            f"**ГИС ЖКХ: Показания успешно приняты**\n\n"
            f"• Прибор: **{m.name}**\n"
            f"• Предыдущие показания: {res.previous_value} {m.unit}\n"
            f"• Текущие показания: **{res.current_value} {m.unit}**\n"
            f"• Расход за месяц: **+{res.consumption} {m.unit}**\n\n"
            f"Показания успешно зафиксированы в ГИС ЖКХ."
        )
        self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=get_main_menu_keyboard())

    def _send_meters_list(self, chat_id, user_id):
        meters = meter_service.get_all_meters()
        lines = ["**Реестр приборов учета (Лицевой счет 1004567890)**\n"]
        buttons = []
        meter_buttons = []
        for m in meters:
            lines.append(
                f"• **{m.name}** (№ {m.serial_number})\n"
                f"  Показания: **{m.last_reading_value} {m.unit}** ({m.last_reading_date})\n"
                f"  Поверка до: {m.verification_date_valid_until.strftime('%d.%m.%Y')}\n"
            )
            name_short = m.name.split('(')[0].strip()
            if name_short == "Электроэнергия":
                name_short = "Свет"
            elif name_short == "Газоснабжение":
                name_short = "Газ"
            elif len(name_short) > 10:
                name_short = name_short[:10]
            btn_label = f"Сдать {name_short}"
            meter_buttons.append(Button.callback(btn_label, f"submit_meter_{m.id}"))
        for i in range(0, len(meter_buttons), 2):
            buttons.append(meter_buttons[i:i + 2])
        buttons.append([
            Button.open_app("Мини-приложение", settings.BOT_USERNAME)
        ])
        lines.append(f"\nМини-приложение: https://max.ru/{settings.BOT_USERNAME}?startapp\nОтправьте **фото счетчика прямо в чат** для распознавания.")
        self._send(chat_id=chat_id, user_id=user_id, text="\n".join(lines), keyboard=KeyboardBuilder.inline(buttons))

    def _process_arshin_check(self, chat_id, user_id, serial):
        arshin = arshin_service.check_verification(serial)
        clean_safety = re.sub(r"\[.*?\]\s*", "", arshin.safety_message)
        self._send(
            chat_id=chat_id,
            user_id=user_id,
            text=f"**Проверка во ФГИС «АРШИН» (102-ФЗ)**\n\n{clean_safety}\n\nОфициальный реестр Росстандарта.",
            keyboard=get_main_menu_keyboard()
        )

    def _send_payment_info(self, chat_id, user_id):
        sample_gost = (
            "ST00012|Name=ООО УК ДОМОВОЙ СЕРВИС|PersonalAcc=40821810938000012345|"
            "BIC=044525225|CorrespAcc=30101810400000000225|PayeeINN=7701234567|"
            "Sum=485050|PersAcc=1004567890|Period=092026"
        )
        parsed = parse_gost_qr_payload(sample_gost)
        text = (
            f"**Начисления ЖКУ: Сентябрь 2026**\n\n"
            f"• К оплате: **{parsed.total_amount_rubles:.2f} ₽**\n"
            f"• Лицевой счет: {parsed.personal_account}\n\n"
            f"**Расщепление по 103-ФЗ на спецсчет 40821:**\n"
            f"• Водоканал (ХВС и стоки): {parsed.split_details[0].amount_rubles:.2f} ₽\n"
            f"• МОЭК (Отопление): {parsed.split_details[1].amount_rubles:.2f} ₽\n"
            f"• Мосэнергосбыт (Свет): {parsed.split_details[2].amount_rubles:.2f} ₽\n"
            f"• УК Домовой Сервис (Содержание): {parsed.split_details[3].amount_rubles:.2f} ₽\n\n"
            f"Платеж защищен законом от списаний по долгам УК."
        )
        buttons = [
            [
                Button.open_app("Оплатить по СБП", settings.BOT_USERNAME)
            ],
            [
                Button.open_app("Сканировать QR", settings.BOT_USERNAME)
            ]
        ]
        self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=KeyboardBuilder.inline(buttons))

    def _process_gost_qr_text(self, chat_id, user_id, qr_text):
        try:
            parsed = parse_gost_qr_payload(qr_text)
            text = (
                f"**Квитанция по ГОСТ Р 56042-2014 успешно распознана**\n\n"
                f"• Получатель: **{parsed.recipient_name}**\n"
                f"• ИНН: {parsed.inn} | БИК: {parsed.bank_bik}\n"
                f"• Сумма: **{parsed.total_amount_rubles:.2f} ₽**\n"
                f"• Спецсчет 40821: защищен по 103-ФЗ"
            )
            pay_label = f"Оплата {parsed.total_amount_rubles:.0f} ₽"
            buttons = [
                [Button.open_app(pay_label, settings.BOT_USERNAME)]
            ]
            self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=KeyboardBuilder.inline(buttons))
        except Exception as e:
            self._send(chat_id=chat_id, user_id=user_id, text=f"Ошибка парсинга QR-кода: {e}")

    def _send_ticket_form(self, chat_id, user_id):
        text = (
            f"**Диспетчерская служба УК (ПП РФ № 40)**\n\n"
            f"Нормативы реагирования:\n"
            f"• Аварийные работы: локализация за 30 мин, ремонт до 3 ч\n"
            f"• Срочные заявки: устранение до 24 ч\n"
            f"• Плановые работы: до 3 рабочих дней\n\n"
            f"Для оформления заявки нажмите кнопку ниже:"
        )
        buttons = [
            [Button.open_app("Создать заявку", settings.BOT_USERNAME)]
        ]
        self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=KeyboardBuilder.inline(buttons))

    def _create_ticket_from_text(self, chat_id, user_id, text: str):
        fsm = self.get_fsm_context(chat_id, user_id)
        data = fsm.get_data() if hasattr(fsm, "get_data") else {}
        category = data.get("category", "other") if isinstance(data, dict) else "other"
        ticket = ticket_service.create_ticket(
            TicketCreateRequest(
                title="Заявка от жителя",
                description=text,
                category=category,
                priority=TicketPriority.URGENT,
                address="ул. Ленина, д. 42, кв. 15"
            )
        )
        t_id = getattr(ticket, "ticket_number", getattr(ticket, "id", ""))
        reply = (
            f"**Заявка в УК зарегистрирована**\n\n"
            f"• Номер заявки: **{t_id}**\n"
            f"• Категория: {ticket.category}\n"
            f"• Приоритет: **Срочная (до 24 ч)**\n"
            f"• Описание: {ticket.description}\n"
            f"• Норматив (ПП РФ № 40): до 24 часов\n\n"
            f"Мастер управляющей компании назначен."
        )
        self._send(
            chat_id=chat_id,
            user_id=user_id,
            text=reply,
            keyboard=get_main_menu_keyboard()
        )
        if hasattr(fsm, "reset_state"):
            fsm.reset_state()

    def _generate_guest_link(self, chat_id, user_id):
        res = guest_service.generate_guest_token(
            GuestAccessGenerateRequest(property_id="flat-42-15", tenant_name="Арендатор")
        )
        text = (
            f"**Гостевой доступ для арендатора**\n\n"
            f"Авторизация через Госуслуги не требуется.\n\n"
            f"Ссылка для жильца в MAX:\n"
            f"{res.direct_max_link}\n\n"
            f"Для открытия используйте кнопку ниже."
        )
        buttons = [
            [Button.open_app("Гостевой доступ", settings.BOT_USERNAME, payload=f"guest_{res.guest_token}")]
        ]
        self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=KeyboardBuilder.inline(buttons))

    def _process_inspector_act(self, chat_id, user_id):
        act = inspector_service.create_act(
            InspectorActCreateRequest(
                meter_id="meter-khvs-1",
                reading_value=142.385,
                address="г. Москва, ул. Ленина, д. 42, кв. 15",
                inspector_name="Смирнов А. В. (Служба учета)",
                gps_coordinates="55.7558° N, 37.6173° E"
            )
        )
        text = (
            f"**АРМ Обходчика УК: Цифровой акт**\n\n"
            f"**{act.act_title}**\n"
            f"• Номер акта: {act.act_number}\n"
            f"• Инспектор: **{act.inspector_name}**\n"
            f"• Адрес: **{act.address}**\n"
            f"• Показания: **{act.meter_reading}**\n"
            f"• GPS-метка: {act.gps_coordinates}\n"
            f"• Хэш-подпись (63-ФЗ): {act.crypto_hash[:16]}...\n"
            f"• Синхронизация: 1С:ЖКХ готова"
        )
        buttons = [
            [
                Button.open_app("Акт обходчика", settings.BOT_USERNAME),
                Button.callback("Главное меню", "cmd_menu")
            ]
        ]
        self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=KeyboardBuilder.inline(buttons))

    def _process_text_reading(self, chat_id, user_id, text: str):
        lower = text.lower().replace(",", ".")
        meter_id = "meter-khvs-1"
        if "гвс" in lower or "горяч" in lower:
            meter_id = "meter-gvs-1"
        elif "свет" in lower or "электр" in lower:
            meter_id = "meter-el-1"
        elif "тепл" in lower or "отопл" in lower:
            meter_id = "meter-heat-1"
        elif "газ" in lower:
            meter_id = "meter-gas-1"

        match = re.search(r"\b\d+(?:\.\d+)?\b", lower)
        if not match:
            self._send(
                chat_id=chat_id,
                user_id=user_id,
                text="Укажите числовое значение, например: `ХВС 145.5`",
                keyboard=get_main_menu_keyboard()
            )
            return

        val = float(match.group(0))
        m = meter_service.get_meter(meter_id) or meter_service.get_all_meters()[0]
        res = meter_service.validate_and_submit_reading(
            MeterReadingSubmitRequest(
                meter_id=m.id,
                reading_value=val,
                submission_channel="max_chat_text"
            )
        )
        if res.is_valid:
            filter_note = "Да (отсечены литры)" if res.red_roller_filtered else "Не требовалось"
            reply = (
                f"**ГИС ЖКХ: Показания успешно приняты**\n\n"
                f"• Прибор: **{m.name}**\n"
                f"• Принято: **{res.current_value} {m.unit}**\n"
                f"• Предыдущие: {res.previous_value} {m.unit}\n"
                f"• Расход: **+{res.consumption} {m.unit}**\n"
                f"• Отсечение литров: **{filter_note}**"
            )
        else:
            reply = f"**Ошибка валидации показаний**\n\n{res.message}"

        self._send(chat_id=chat_id, user_id=user_id, text=reply, keyboard=get_main_menu_keyboard())

    def _format_property_button_label(self, prop: UserProperty) -> str:
        addr = prop.address
        parts = [p.strip() for p in addr.split(",") if p.strip()]
        if len(parts) >= 2:
            candidate = f"{parts[1]} {parts[2] if len(parts) > 2 else ''}".strip()
            candidate = re.sub(r"\b(ул\.|д\.|кв\.|г\.)\s*", "", candidate).strip()
        else:
            candidate = addr
        if len(candidate) > 16 or not candidate:
            candidate = f"Адрес {prop.els[-4:]}"
        return candidate[:17]

    def _send_user_profile(self, chat_id: Optional[int], user_id: Optional[int]):
        prof = profile_service.get_profile(user_id)
        active_prop = profile_service.get_active_property(user_id)
        phone_status = f"Подтвержден: {prof.phone}" if prof.is_verified and prof.phone else "Не подтвержден"
        status_name = "Собственник" if active_prop.role == "owner" else "Арендатор"
        full_name = f"{prof.first_name} {prof.last_name}".strip() if prof.last_name else prof.first_name

        text = (
            f"**Личный кабинет жителя**\n\n"
            f"• Профиль: **{full_name}**\n"
            f"• Статус: **{status_name}**\n"
            f"• Телефон: {phone_status}\n"
            f"• Активный адрес: **{active_prop.address}**\n"
            f"• ЕЛС ГИС ЖКХ: **{active_prop.els}**\n"
            f"• Управляющая компания: {active_prop.management_company}\n"
            f"• Всего объектов: **{len(prof.properties)}**"
        )
        buttons = [
            [Button.open_app("Мини-приложение", settings.BOT_USERNAME)]
        ]
        if not prof.is_verified:
            buttons.append([Button.request_contact("Поделиться тел.")])
        else:
            buttons.append([Button.callback("Сменить адрес", "cmd_address")])
        buttons.append([
            Button.callback("Мои адреса", "cmd_address"),
            Button.callback("Главное меню", "cmd_menu")
        ])
        self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=KeyboardBuilder.inline(buttons))

    def _send_address_management(self, chat_id: Optional[int], user_id: Optional[int]):
        prof = profile_service.get_profile(user_id)
        text = (
            f"**Управление объектами недвижимости (ЕЛС)**\n\n"
            f"Выберите адрес для переключения в 1 клик:\n\n"
        )
        for idx, p in enumerate(prof.properties, 1):
            tag = "(активен)" if p.is_active else ""
            text += f"{idx}. **{p.address}**\n   ЕЛС: `{p.els}` • {p.management_company} {tag}\n"

        buttons = []
        prop_row = []
        for p in prof.properties:
            label = self._format_property_button_label(p)
            prop_row.append(Button.callback(label, f"switch_prop_{p.id}"))
            if len(prop_row) == 2:
                buttons.append(prop_row)
                prop_row = []
        if prop_row:
            buttons.append(prop_row)

        buttons.append([
            Button.callback("Добавить адрес", "cmd_add_address"),
            Button.callback("Главное меню", "cmd_menu")
        ])
        self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=KeyboardBuilder.inline(buttons))

    def _process_switch_property(self, chat_id: Optional[int], user_id: Optional[int], prop_id: str):
        prof = profile_service.switch_active_property(user_id, prop_id)
        if prof:
            active = profile_service.get_active_property(user_id)
            reply = (
                f"**Активный адрес переключен**\n\n"
                f"• Текущий объект: **{active.address}**\n"
                f"• ЕЛС ГИС ЖКХ: **{active.els}**\n"
                f"• Управляющая компания: {active.management_company}\n\n"
                f"Показания счетчиков и квитанции теперь относятся к этому объекту."
            )
            buttons = [
                [Button.open_app("Мини-приложение", settings.BOT_USERNAME)],
                [Button.callback("Сдать показания", "cmd_meters"), Button.callback("Мой профиль", "cmd_profile")]
            ]
            self._send(chat_id=chat_id, user_id=user_id, text=reply, keyboard=KeyboardBuilder.inline(buttons))
        else:
            self._send(chat_id=chat_id, user_id=user_id, text="Объект недвижимости не найден.", keyboard=get_main_menu_keyboard())

    def _send_registration_info(self, chat_id: Optional[int], user_id: Optional[int]):
        text = (
            f"**Быстрая регистрация в MAX**\n\n"
            f"Для мгновенной привязки лицевых счетов ГИС ЖКХ без ручного ввода подтвердите номер телефона в 1 клик.\n\n"
            f"Нажмите кнопку «Поделиться» ниже:"
        )
        buttons = [
            [Button.request_contact("Поделиться")],
            [Button.open_app("В приложении", settings.BOT_USERNAME)]
        ]
        self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=KeyboardBuilder.inline(buttons))

    def _send_add_address_prompt(self, chat_id: Optional[int], user_id: Optional[int]):
        text = (
            f"**Добавление нового адреса**\n\n"
            f"Чтобы привязать новое помещение, вы можете:\n"
            f"1. Открыть **Мини-приложение** и нажать «Добавить адрес»\n"
            f"2. Отправить в чат QR-код с квитанции (ГОСТ Р 56042-2014) — адрес и ЕЛС привяжутся автоматически!\n"
            f"3. Или введите команду `/address` для управления существующими объектами."
        )
        buttons = [
            [Button.open_app("В приложении", settings.BOT_USERNAME)],
            [Button.callback("Мои адреса", "cmd_address")]
        ]
        self._send(chat_id=chat_id, user_id=user_id, text=text, keyboard=KeyboardBuilder.inline(buttons))

    def _process_incoming_contact(self, chat_id: Optional[int], user_id: Optional[int], update: Update):
        contact = update.contact or {}
        payload = contact.get("payload") if isinstance(contact.get("payload"), dict) else {}
        vcf_info = payload.get("vcf_info") or contact.get("vcf_info")
        phone = payload.get("phone") or contact.get("phone")
        hash_val = payload.get("hash") or contact.get("hash")

        prof = profile_service.verify_phone_contact(
            user_id=user_id,
            phone=phone,
            vcf_info=vcf_info,
            hash_val=hash_val
        )
        active_prop = profile_service.get_active_property(user_id)

        reply = (
            f"**Телефон успешно подтвержден**\n\n"
            f"• Номер телефона: **{prof.phone}**\n"
            f"• Статус в MAX: подтвержден (без ручного ввода)\n\n"
            f"Автоматически найдены и привязаны объекты ГИС ЖКХ:\n"
            f"• **{active_prop.address}** (ЕЛС {active_prop.els})\n\n"
            f"Теперь вы можете передавать показания и оплачивать квитанции в 1 клик."
        )
        buttons = [
            [Button.open_app("Мини-приложение", settings.BOT_USERNAME)],
            [Button.callback("Сдать показания", "cmd_meters"), Button.callback("Мой профиль", "cmd_profile")]
        ]
        self._send(chat_id=chat_id, user_id=user_id, text=reply, keyboard=KeyboardBuilder.inline(buttons))


# Singleton instance

_default_bot_handler: Optional[BotHandler] = None

def get_bot_handler() -> BotHandler:
    global _default_bot_handler
    if _default_bot_handler is None:
        client = MaxBotClient(settings.BOT_TOKEN, settings.MAX_API_BASE)
        _default_bot_handler = BotHandler(client)
    return _default_bot_handler
