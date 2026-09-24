"""Состояния диалога."""
from __future__ import annotations

from enum import StrEnum


class S(StrEnum):
    IDLE = "idle"
    # Регистрация (не истекает)
    REG_NAME = "reg_name"
    REG_PHONE = "reg_phone"
    REG_ADDRESS = "reg_address"
    REG_ADDRESS_PICK = "reg_address_pick"
    REG_FLAT = "reg_flat"
    REG_CONFIRM = "reg_confirm"
    # Подача показаний (TTL 30 мин)
    SUB_AWAIT_PHOTO = "sub_await_photo"
    SUB_PICK_METER = "sub_pick_meter"
    SUB_NEW_TYPE = "sub_new_type"
    SUB_NEW_TARIFF = "sub_new_tariff"
    SUB_NEW_ADDRESS = "sub_new_address"
    SUB_ADDR_INPUT = "sub_addr_input"
    SUB_ADDR_PICK = "sub_addr_pick"
    SUB_ADDR_FLAT = "sub_addr_flat"
    SUB_REVIEW = "sub_review"
    SUB_SERIAL_MISMATCH = "sub_serial_mismatch"
    SUB_PLAUSIBILITY = "sub_plausibility"
    SUB_MANUAL = "sub_manual"
    SUB_REPLACE_CONFIRM = "sub_replace_confirm"
    SUB_VERIF_DATE = "sub_verif_date"
    # Профиль (TTL 30 мин)
    PROFILE_PHONE = "profile_phone"
    PROFILE_ADDR_INPUT = "profile_addr_input"
    PROFILE_ADDR_PICK = "profile_addr_pick"
    PROFILE_ADDR_FLAT = "profile_addr_flat"
    PROFILE_DELETE_CONFIRM = "profile_delete_confirm"

    @property
    def is_reg(self) -> bool:
        return self.value.startswith("reg_")

    @property
    def is_sub(self) -> bool:
        return self.value.startswith("sub_")

    @property
    def is_profile(self) -> bool:
        return self.value.startswith("profile_")

    @property
    def is_scenario(self) -> bool:
        """Подача или профиль — отменяемые сценарии с TTL."""
        return self.is_sub or self.is_profile
