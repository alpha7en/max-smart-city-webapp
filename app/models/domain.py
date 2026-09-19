"""
Domain entities and enumeration models for MAX Smart City housing ecosystem.
Complies with:
- PP RF No. 40 (26.01.2026) on UK-resident communications via MAX
- 209-FZ (GIS ZHKH) ELS & IZHKU standards
- 102-FZ (FGIS Arshin metrology verification)
- 103-FZ & 59-FZ (Split payments to Account 40821)
- GOST R 56042-2014 (QR code standard for consumer payments)
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime, date
from pydantic import BaseModel, Field

class MeterType(str, Enum):
    COLD_WATER = "cold_water"      # ХВС
    HOT_WATER = "hot_water"        # ГВС
    ELECTRICITY_SINGLE = "el_single" # Электроэнергия (однотарифный)
    ELECTRICITY_MULTI = "el_multi"   # Электроэнергия (многотарифный T1, T2, T3)
    GAS = "gas"                    # Газ
    HEAT = "heat"                  # Отопление

class TariffZone(str, Enum):
    T1_DAY = "T1"     # Пик / День (07:00 - 23:00)
    T2_NIGHT = "T2"   # Ночь (23:00 - 07:00)
    T3_HALF = "T3"    # Полупик

class VerificationStatus(str, Enum):
    VERIFIED = "verified"          # Действительна
    EXPIRING_SOON = "expiring"     # Истекает в течение 60 дней
    EXPIRED = "expired"            # Просрочена (требуется поверка)
    FRAUD_ALERT = "fraud_alert"    # Выявлена мошенническая угроза (поверка не требуется)

class TicketPriority(str, Enum):
    EMERGENCY = "emergency"        # Аварийная (локализация 30 мин по ПП РФ № 40)
    URGENT = "urgent"              # Срочная (устранение до 24 ч)
    PLANNED = "planned"            # Плановая (до 3 рабочих дней)

class TicketStatus(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CONFIRMED_BY_RESIDENT = "confirmed"

class UserRole(str, Enum):
    RESIDENT = "resident"          # Собственник помещения
    TENANT = "tenant"              # Арендатор (гостевой доступ)
    INSPECTOR_UK = "inspector_uk"  # Контролер / обходчик УК
    DISPATCHER_UK = "dispatcher_uk" # Диспетчер аварийной службы
    ADMIN = "admin"
