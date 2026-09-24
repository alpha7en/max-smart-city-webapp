"""
UserProfileService: Management of user profiles and multiple linked properties (ЕЛС / адреса).
Supports:
- One-click phone verification via MAX Bot request_contact and WebApp Bridge (HMAC-SHA256).
- Multiple linked properties (primary apartment, dacha/country house, rental property).
- 1-click active property switching.
- Adding property by address, ELS (10-digit), or scanning GOST R 56042-2014 QR bill.
- Automatic integration with GIS ZHKH / ESIA identity concepts.
- Persistent SQLite storage via app.db.database.Database with 100% backward compatibility.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
import re
import hmac
import hashlib
import logging

from app.config import settings
from app.db.database import Database, get_db, REFERENCE_USER_ID, REFERENCE_PROPERTIES
from app.models.schemas import (
    UserProfile,
    UserProperty,
    ProfileSwitchPropertyRequest,
    ProfileAddPropertyRequest,
    ProfileVerifyContactRequest
)
from app.services.gost_qr import parse_gost_qr_payload

logger = logging.getLogger("max_profile_service")

DEFAULT_USER_ID = REFERENCE_USER_ID

def _create_initial_properties() -> List[UserProperty]:
    return [
        UserProperty(
            id="prop-flat-42-15",
            address="г. Москва, ул. Ленина, д. 42, кв. 15",
            els="1004567890",
            management_company="ООО УК Столица-Сервис",
            is_active=True,
            role="owner",
            linked_at=datetime.fromisoformat("2026-08-20T10:00:00")
        ),
        UserProperty(
            id="prop-dacha-8",
            address="Московская обл., д. Барвиха, д. 8",
            els="2008891024",
            management_company="ТСЖ Рублево-Сервис",
            is_active=False,
            role="tenant",
            linked_at=datetime.fromisoformat("2026-08-20T10:00:00")
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
    def __init__(self, db: Optional[Database] = None):
        self._db = db or get_db()
        self._profiles: Dict[int, UserProfile] = {}
        self.reset()

    @property
    def db(self) -> Database:
        return self._db

    def reset(self):
        """Resets profiles to pristine initial state for test isolation."""
        self._profiles.clear()
        self._db.seed_reference_data(force=True)

    def get_profile(self, user_id: Optional[int] = None) -> UserProfile:
        uid = user_id or DEFAULT_USER_ID

        # Query user record from SQLite
        user_row = self._db.execute_one("SELECT * FROM users WHERE user_id = ?", (uid,))
        now_iso = datetime.now().isoformat()

        if user_row is None:
            # Lazy provision new user in SQLite
            is_verified = 1 if uid == DEFAULT_USER_ID else 0
            active_property_id = "prop-flat-42-15"
            self._db.execute_write(
                """INSERT OR REPLACE INTO users (
                    user_id, first_name, last_name, username, phone, is_verified, active_property_id, created_at, updated_at
                ) VALUES (?, 'Иван', 'Иванов', NULL, NULL, ?, ?, ?, ?);""",
                (uid, is_verified, active_property_id, now_iso, now_iso)
            )
            user_row = self._db.execute_one("SELECT * FROM users WHERE user_id = ?", (uid,))

        active_prop_id = user_row["active_property_id"] or "prop-flat-42-15"

        # Query user-specific properties
        user_props = self._db.execute_query(
            "SELECT * FROM properties WHERE user_id = ? ORDER BY linked_at ASC", (uid,)
        )

        seen_ids = set()
        properties_list: List[UserProperty] = []

        # Add user's explicit properties
        for p in user_props:
            pid = p["id"]
            seen_ids.add(pid)
            try:
                l_dt = datetime.fromisoformat(p["linked_at"])
            except Exception:
                l_dt = datetime.now()
            properties_list.append(UserProperty(
                id=pid,
                address=p["address"],
                els=p["els"],
                management_company=p["management_company"],
                is_active=(pid == active_prop_id),
                role=p["role"],
                linked_at=l_dt
            ))

        # Always include demo baseline properties if not already present
        if uid != DEFAULT_USER_ID or not user_props:
            ref_props = self._db.execute_query(
                "SELECT * FROM properties WHERE user_id = ? ORDER BY linked_at ASC", (DEFAULT_USER_ID,)
            )
            if not ref_props:
                # Fallback to pristine in-memory definition
                for p_obj in _create_initial_properties():
                    if p_obj.id not in seen_ids:
                        seen_ids.add(p_obj.id)
                        properties_list.append(UserProperty(
                            id=p_obj.id,
                            address=p_obj.address,
                            els=p_obj.els,
                            management_company=p_obj.management_company,
                            is_active=(p_obj.id == active_prop_id),
                            role=p_obj.role,
                            linked_at=p_obj.linked_at
                        ))
            else:
                for p in ref_props:
                    pid = p["id"]
                    if pid not in seen_ids:
                        seen_ids.add(pid)
                        try:
                            l_dt = datetime.fromisoformat(p["linked_at"])
                        except Exception:
                            l_dt = datetime.now()
                        properties_list.append(UserProperty(
                            id=pid,
                            address=p["address"],
                            els=p["els"],
                            management_company=p["management_company"],
                            is_active=(pid == active_prop_id),
                            role=p["role"],
                            linked_at=l_dt
                        ))

        # Ensure at least one property is active
        has_active = any(p.is_active for p in properties_list)
        if not has_active and properties_list:
            properties_list[0].is_active = True
            active_prop_id = properties_list[0].id
            self._db.execute_write(
                "UPDATE users SET active_property_id = ?, updated_at = ? WHERE user_id = ?",
                (active_prop_id, now_iso, uid)
            )

        prof = UserProfile(
            user_id=uid,
            phone=user_row["phone"],
            first_name=user_row["first_name"] or "Иван",
            last_name=user_row["last_name"] or "Иванов",
            username=user_row["username"],
            is_verified=bool(user_row["is_verified"]),
            active_property_id=active_prop_id,
            properties=properties_list
        )
        self._profiles[uid] = prof
        return prof

    def get_active_property(self, user_id: Optional[int] = None) -> UserProperty:
        profile = self.get_profile(user_id)
        for prop in profile.properties:
            if prop.is_active:
                return prop
        return profile.properties[0]

    def switch_active_property(self, user_id: Optional[int], property_id: str) -> Optional[UserProfile]:
        uid = user_id or DEFAULT_USER_ID
        profile = self.get_profile(uid)
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
        now_iso = datetime.now().isoformat()

        # Update SQLite persistence
        self._db.execute_write(
            "UPDATE users SET active_property_id = ?, updated_at = ? WHERE user_id = ?",
            (target_id, now_iso, uid)
        )
        self._db.execute_write(
            "UPDATE properties SET is_active = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE user_id = ?",
            (target_id, uid)
        )

        self._profiles[uid] = profile
        return profile

    def switch_property(self, user_id: Optional[int], property_id: str) -> Optional[UserProfile]:
        """Dispatch alias for switch_active_property."""
        return self.switch_active_property(user_id, property_id)

    def add_property(
        self,
        user_id: Optional[int],
        address: Any = None,
        els: Optional[str] = None,
        management_company: Optional[str] = "ООО УК Столица-Сервис",
        role: Optional[str] = "resident",
        gost_qr_payload: Optional[str] = None,
        property_data: Optional[Dict[str, Any]] = None
    ) -> UserProfile:
        uid = user_id or DEFAULT_USER_ID
        profile = self.get_profile(uid)

        # Support property_data dict if passed as 2nd parameter or keyword arg
        if isinstance(address, dict):
            property_data = address
            address = property_data.get("address")
            els = property_data.get("els")
            management_company = property_data.get("management_company", management_company)
            role = property_data.get("role", role)
            gost_qr_payload = property_data.get("gost_qr_payload", gost_qr_payload)
        elif property_data and isinstance(property_data, dict):
            address = property_data.get("address", address)
            els = property_data.get("els", els)
            management_company = property_data.get("management_company", management_company)
            role = property_data.get("role", role)
            gost_qr_payload = property_data.get("gost_qr_payload", gost_qr_payload)

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

            now_iso = datetime.now().isoformat()
            self._db.execute_write(
                "UPDATE users SET active_property_id = ?, updated_at = ? WHERE user_id = ?",
                (existing.id, now_iso, uid)
            )
            self._db.execute_write(
                "UPDATE properties SET address = ?, management_company = ?, is_active = 1 WHERE id = ? AND user_id = ?",
                (eff_address or existing.address, eff_mc or existing.management_company, existing.id, uid)
            )
            self._profiles[uid] = profile
            return profile

        new_id = f"prop-{int(datetime.now().timestamp())}-{len(profile.properties) + 1}"
        now_iso = datetime.now().isoformat()
        now_dt = datetime.now()

        # Insert new property into SQLite
        self._db.execute_write(
            """INSERT INTO properties (
                id, user_id, address, els, management_company, is_active, role, linked_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?);""",
            (new_id, uid, eff_address, eff_els, eff_mc, eff_role, now_iso)
        )

        # Set new property active in users table
        self._db.execute_write(
            "UPDATE users SET active_property_id = ?, updated_at = ? WHERE user_id = ?",
            (new_id, now_iso, uid)
        )

        # Update previous properties in SQLite to inactive for this user
        self._db.execute_write(
            "UPDATE properties SET is_active = 0 WHERE user_id = ? AND id != ?",
            (uid, new_id)
        )

        new_prop = UserProperty(
            id=new_id,
            address=eff_address,
            els=eff_els,
            management_company=eff_mc,
            is_active=True,
            role=eff_role,
            linked_at=now_dt
        )

        # Make previous properties inactive and activate new property in model
        for p in profile.properties:
            p.is_active = False

        profile.properties.append(new_prop)
        profile.active_property_id = new_id
        self._profiles[uid] = profile
        return profile

    def add_property_manual(
        self,
        user_id: Optional[int],
        address: str,
        management_company: Optional[str] = "ООО УК Столица-Сервис",
        role: Optional[str] = "resident",
        els: Optional[str] = None,
        **kwargs
    ) -> UserProperty:
        """
        Adds a property manually by address/MC, delegating to add_property,
        persisting to SQLite, and returning the newly created UserProperty.
        """
        prof = self.add_property(
            user_id=user_id,
            address=address,
            els=els,
            management_company=management_company,
            role=role,
            **kwargs
        )
        for p in prof.properties:
            if p.id == prof.active_property_id:
                return p
        return prof.properties[-1]

    def verify_phone_contact(
        self,
        user_id: Optional[int],
        phone: Optional[str] = None,
        vcf_info: Optional[str] = None,
        hash_val: Optional[str] = None,
        auth_date: Optional[int] = None
    ) -> UserProfile:
        uid = user_id or DEFAULT_USER_ID
        profile = self.get_profile(uid)

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

        # Persist to SQLite
        now_iso = datetime.now().isoformat()
        self._db.execute_write(
            "UPDATE users SET phone = ?, is_verified = 1, updated_at = ? WHERE user_id = ?",
            (formatted_phone, now_iso, uid)
        )
        self._profiles[uid] = profile
        return profile

profile_service = UserProfileService()
