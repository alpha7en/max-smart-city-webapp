"""
UserProfileService: Management of user profiles and multiple linked properties (ЕЛС / адреса).
Supports:
- One-click phone verification via MAX Bot request_contact and WebApp Bridge (HMAC-SHA256).
- Multiple linked properties (primary apartment, dacha/country house, rental property).
- 1-click active property switching.
- Adding property by address, ELS (10-digit), or scanning GOST R 56042-2014 QR bill.
- Automatic integration with GIS ZHKH / ESIA identity concepts.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
import re
import hmac
import hashlib
import logging

from app.config import settings
from app.models.schemas import (
    UserProfile,
    UserProperty,
    ProfileSwitchPropertyRequest,
    ProfileAddPropertyRequest,
    ProfileVerifyContactRequest
)
from app.services.gost_qr import parse_gost_qr_payload

logger = logging.getLogger("max_profile_service")

DEFAULT_USER_ID = 100456

def _create_initial_properties() -> List[UserProperty]:
    return [
        UserProperty(
            id="prop-flat-42-15",
            address="г. Москва, ул. Ленина, д. 42, кв. 15",
            els="1004567890",
            management_company="ООО УК Столица-Сервис",
            is_active=True,
            role="owner",
            linked_at=datetime.now()
        ),
        UserProperty(
            id="prop-dacha-8",
            address="Московская обл., д. Барвиха, д. 8",
            els="2008891024",
            management_company="ТСЖ Рублево-Сервис",
            is_active=False,
            role="tenant",
            linked_at=datetime.now()
        )
    ]

def normalize_phone_number(raw_phone: Optional[str]) -> str:
    """Normalizes Russian phone numbers to standard format: +7 (XXX) XXX-XX-XX"""
    if not raw_phone or not isinstance(raw_phone, str):
        return ""
    digits = re.sub(r"\D", "", raw_phone)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    elif digits.startswith("7") and len(digits) == 11:
        pass
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return raw_phone.strip()

    return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"

def parse_vcf_phone(vcf_text: Optional[str]) -> Optional[str]:
    """Extracts telephone number from VCARD vcf_info payload"""
    if not vcf_text or not isinstance(vcf_text, str):
        return None
    match = re.search(r"TEL[^:]*:([+\d\s\(\)\-]+)", vcf_text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None

class UserProfileService:
    def __init__(self):
        self._profiles: Dict[int, UserProfile] = {}
        self.reset()

    def reset(self):
        """Resets in-memory profiles to pristine initial demo state for test isolation."""
        self._profiles.clear()
        self._profiles[DEFAULT_USER_ID] = UserProfile(
            user_id=DEFAULT_USER_ID,
            phone="+7 (999) 123-45-67",
            first_name="Иван",
            last_name="Иванов",
            username="ivan_ivanov",
            is_verified=True,
            active_property_id="prop-flat-42-15",
            properties=_create_initial_properties()
        )

    def get_profile(self, user_id: Optional[int] = None) -> UserProfile:
        uid = user_id or DEFAULT_USER_ID
        if uid not in self._profiles:
            # Initialize new profile with default demo properties
            props = _create_initial_properties()
            self._profiles[uid] = UserProfile(
                user_id=uid,
                phone=None,
                first_name="Иван",
                last_name="Иванов",
                username=None,
                is_verified=False,
                active_property_id=props[0].id,
                properties=props
            )
        return self._profiles[uid]

    def get_active_property(self, user_id: Optional[int] = None) -> UserProperty:
        profile = self.get_profile(user_id)
        for prop in profile.properties:
            if prop.is_active:
                return prop
        return profile.properties[0]

    def switch_active_property(self, user_id: Optional[int], property_id: str) -> Optional[UserProfile]:
        profile = self.get_profile(user_id)
        target_id = (property_id or "").strip()
        target = None
        for prop in profile.properties:
            if prop.id == target_id:
                target = prop
                break

        if not target:
            logger.warning("Property %s not found for user %s", property_id, user_id)
            return None

        for prop in profile.properties:
            prop.is_active = (prop.id == target_id)

        profile.active_property_id = target_id
        return profile

    def add_property(
        self,
        user_id: Optional[int],
        address: str,
        els: str,
        management_company: Optional[str] = "ООО УК Столица-Сервис",
        role: Optional[str] = "resident",
        gost_qr_payload: Optional[str] = None
    ) -> UserProfile:
        profile = self.get_profile(user_id)
        eff_address = (address or "").strip()
        eff_els = (els or "").strip()
        eff_mc = (management_company or "ООО УК Столица-Сервис").strip()
        raw_role = (role or "owner").strip().lower()
        eff_role = "tenant" if raw_role in ["tenant", "guest", "арендатор"] else "owner"

        # Auto-extract from GOST QR if provided
        if gost_qr_payload:
            try:
                parsed_qr = parse_gost_qr_payload(gost_qr_payload)
                if parsed_qr.personal_account:
                    eff_els = parsed_qr.personal_account
                if parsed_qr.recipient_name:
                    eff_mc = parsed_qr.recipient_name
                if not eff_address:
                    eff_address = f"Объект по квитанции {eff_mc}"
            except Exception as e:
                logger.warning("Could not auto-extract from GOST QR: %s", e)

        # Check if property with this ELS already exists
        existing = next((p for p in profile.properties if p.els == eff_els and eff_els), None)
        if existing:
            for p in profile.properties:
                p.is_active = False
            existing.is_active = True
            if eff_address:
                existing.address = eff_address
            if eff_mc:
                existing.management_company = eff_mc
            profile.active_property_id = existing.id
            return profile

        new_id = f"prop-{int(datetime.now().timestamp())}-{len(profile.properties) + 1}"
        new_prop = UserProperty(
            id=new_id,
            address=eff_address,
            els=eff_els,
            management_company=eff_mc,
            is_active=True,
            role=eff_role,
            linked_at=datetime.now()
        )

        # Make previous properties inactive and activate new property
        for p in profile.properties:
            p.is_active = False

        profile.properties.append(new_prop)
        profile.active_property_id = new_id
        return profile

    def verify_phone_contact(
        self,
        user_id: Optional[int],
        phone: Optional[str] = None,
        vcf_info: Optional[str] = None,
        hash_val: Optional[str] = None,
        auth_date: Optional[int] = None
    ) -> UserProfile:
        profile = self.get_profile(user_id)

        extracted_phone = phone
        if vcf_info:
            parsed = parse_vcf_phone(vcf_info)
            if parsed:
                extracted_phone = parsed

        if not extracted_phone:
            extracted_phone = "+7 (999) 123-45-67"

        formatted_phone = normalize_phone_number(extracted_phone)

        # Cryptographic HMAC-SHA256 signature verification
        if hash_val and settings.BOT_TOKEN:
            candidates = []
            if vcf_info:
                candidates.append(vcf_info.strip())
            if auth_date:
                candidates.append(f"authDate={auth_date}\nphone={extracted_phone}")
                candidates.append(f"auth_date={auth_date}\nphone={extracted_phone}")
                if phone:
                    candidates.append(f"authDate={auth_date}\nphone={phone}")
                    candidates.append(f"auth_date={auth_date}\nphone={phone}")
            if not candidates and extracted_phone:
                candidates.append(f"phone={extracted_phone}")

            is_valid_hmac = False
            for cand in candidates:
                expected_hash = hmac.new(
                    settings.BOT_TOKEN.encode("utf-8"),
                    cand.encode("utf-8"),
                    hashlib.sha256
                ).hexdigest()
                if hmac.compare_digest(hash_val.lower(), expected_hash.lower()):
                    is_valid_hmac = True
                    break

            if not is_valid_hmac:
                raise ValueError("Недействительная криптографическая подпись контакта (HMAC-SHA256)")
        elif not settings.DEV_MODE:
            # In strict production mode, contact verification requires a cryptographic signature
            raise ValueError("Отсутствует обязательная криптографическая подпись контакта (hash)")

        profile.phone = formatted_phone
        profile.is_verified = True
        return profile

profile_service = UserProfileService()
