"""
Keyboard builder functions for MAX Chat Bot.
Adheres strictly to MAX UI guidelines:
- All button labels <= 18 characters
- Zero Unicode emojis
- High-tech, minimal Finnish/Braun typographic style
"""

from typing import Dict, Any, List, Optional
from max_bot_sdk.keyboards import Button, KeyboardBuilder
from app.config import settings


def get_main_menu_keyboard() -> Dict[str, Any]:
    """
    Main menu keyboard for resident.
    All labels strictly <= 18 characters.
    """
    return KeyboardBuilder.inline([
        [
            Button.open_app("Мини-приложение", settings.BOT_USERNAME)
        ],
        [
            Button.callback("Сдать показания", "cmd_meters"),
            Button.callback("Проверка АРШИН", "cmd_arshin")
        ],
        [
            Button.callback("Оплата ЖКУ", "cmd_pay"),
            Button.callback("Арендатор", "cmd_guest")
        ],
        [
            Button.callback("Мой профиль", "cmd_profile"),
            Button.callback("Мои адреса", "cmd_address")
        ]
    ])


def get_guest_keyboard(guest_token: Optional[str] = None) -> Dict[str, Any]:
    """Guest keyboard for tenant access."""
    payload = f"guest_{guest_token}" if guest_token else ""
    return KeyboardBuilder.inline([
        [Button.open_app("Мини-приложение", settings.BOT_USERNAME, payload=payload)],
        [Button.callback("Сдать показания", "cmd_meters"), Button.callback("Оплата ЖКУ", "cmd_pay")]
    ])


def get_meters_keyboard(meters: Optional[List[Any]] = None) -> Dict[str, Any]:
    """
    Keyboard with meter submission buttons.
    Ensures button text is <= 18 characters.
    """
    buttons: List[List[Dict[str, Any]]] = []
    if meters:
        meter_buttons = []
        for m in meters:
            raw_name = getattr(m, "name", "Счетчик")
            name_short = raw_name.split('(')[0].strip()
            if name_short == "Электроэнергия":
                name_short = "Свет"
            elif name_short == "Газоснабжение":
                name_short = "Газ"
            elif len(name_short) > 10:
                name_short = name_short[:10]
            btn_label = f"Сдать {name_short}"
            meter_id = getattr(m, "id", "m")
            meter_buttons.append(Button.callback(btn_label, f"submit_meter_{meter_id}"))

        for i in range(0, len(meter_buttons), 2):
            buttons.append(meter_buttons[i:i + 2])
    else:
        buttons.append([
            Button.callback("Сдать ХВС", "rescan_meter_cold_water"),
            Button.callback("Сдать ГВС", "rescan_meter_hot_water")
        ])
        buttons.append([
            Button.callback("Сдать Свет", "rescan_meter_electricity"),
            Button.callback("Сдать Газ", "rescan_meter_gas")
        ])

    buttons.append([
        Button.open_app("Мини-приложение", settings.BOT_USERNAME)
    ])
    return KeyboardBuilder.inline(buttons)


def get_meter_confirmation_keyboard(meter_id: str, value: float) -> Dict[str, Any]:
    """Keyboard for confirming meter reading with rescan options."""
    val_str = str(value)
    if len(val_str) > 8:
        val_str = val_str[:8]
    confirm_text = f"Принять {val_str}"
    return KeyboardBuilder.inline([
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
    ])


def get_services_keyboard() -> Dict[str, Any]:
    """Keyboard for communal services and actions."""
    return KeyboardBuilder.inline([
        [
            Button.callback("Сдать показания", "cmd_meters"),
            Button.callback("Проверка АРШИН", "cmd_arshin")
        ],
        [
            Button.callback("Оплата ЖКУ", "cmd_pay"),
            Button.callback("Создать заявку", "cmd_ticket")
        ],
        [
            Button.callback("Главное меню", "cmd_menu")
        ]
    ])


def get_help_keyboard() -> Dict[str, Any]:
    """Keyboard for help and documentation."""
    return KeyboardBuilder.inline([
        [Button.open_app("Мини-приложение", settings.BOT_USERNAME)],
        [Button.callback("Сдать показания", "cmd_meters"), Button.callback("Главное меню", "cmd_menu")]
    ])


def get_payment_keyboard(amount_rubles: Optional[float] = None) -> Dict[str, Any]:
    """Payment keyboard."""
    if amount_rubles is not None:
        pay_label = f"Оплата {amount_rubles:.0f} ₽"
        return KeyboardBuilder.inline([
            [Button.open_app(pay_label, settings.BOT_USERNAME)],
            [Button.callback("Главное меню", "cmd_menu")]
        ])
    return KeyboardBuilder.inline([
        [Button.open_app("Оплатить по СБП", settings.BOT_USERNAME)],
        [Button.open_app("Сканировать QR", settings.BOT_USERNAME)],
        [Button.callback("Главное меню", "cmd_menu")]
    ])


def get_ticket_categories_keyboard() -> Dict[str, Any]:
    """Keyboard for selecting emergency ticket category."""
    return KeyboardBuilder.inline([
        [
            Button.callback("Водоснабжение", "ticket_cat_water"),
            Button.callback("Электрика", "ticket_cat_electric")
        ],
        [
            Button.callback("Отопление", "ticket_cat_heat"),
            Button.callback("Лифт / Подъезд", "ticket_cat_elevator")
        ],
        [
            Button.callback("Отмена", "cmd_menu")
        ]
    ])


def get_inspector_keyboard() -> Dict[str, Any]:
    """Keyboard for UK inspector workstation."""
    return KeyboardBuilder.inline([
        [
            Button.open_app("Акт обходчика", settings.BOT_USERNAME),
            Button.callback("Главное меню", "cmd_menu")
        ]
    ])


def get_address_switch_keyboard(properties: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Keyboard for switching and managing registered properties."""
    buttons: List[List[Dict[str, Any]]] = []
    if properties:
        prop_row = []
        for p in properties:
            els = getattr(p, "els", "")
            address = getattr(p, "address", "")
            is_active = getattr(p, "is_active", False)
            if "Дача" in address or "КП" in address:
                short_name = "Дача"
            elif "Ленина" in address:
                short_name = "Ленина 42"
            else:
                short_name = els[-6:] if len(els) >= 6 else (address[:10] if address else "Объект")

            tag = " *" if is_active else ""
            label = f"{short_name}{tag}"
            if len(label) > 18:
                label = label[:18]
            prop_id = getattr(p, "id", "")
            prop_row.append(Button.callback(label, f"switch_prop_{prop_id}"))
            if len(prop_row) == 2:
                buttons.append(prop_row)
                prop_row = []
        if prop_row:
            buttons.append(prop_row)

    buttons.append([
        Button.callback("Добавить адрес", "cmd_add_address"),
        Button.callback("Главное меню", "cmd_menu")
    ])
    return KeyboardBuilder.inline(buttons)
