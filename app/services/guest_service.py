"""
Tenant / Landlord Guest Access Service.
Solves the barrier where official apps (Gosuslugi Dom) require strict ESIA identity authorization.
Allows apartment owners to issue secure one-click guest links in MAX for tenants
to submit meter readings and pay utility bills without exposing personal owner documents.
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import secrets
from app.models.schemas import GuestAccessGenerateRequest, GuestAccessResponse

class GuestAccessService:
    def __init__(self):
        self._active_tokens: Dict[str, Dict] = {}

    def generate_guest_token(self, req: GuestAccessGenerateRequest) -> GuestAccessResponse:
        token = f"guest_{secrets.token_urlsafe(16)}"
        expires_at = datetime.now() + timedelta(days=req.duration_days)
        
        self._active_tokens[token] = {
            "property_id": req.property_id,
            "tenant_name": req.tenant_name,
            "tenant_phone": req.tenant_phone,
            "expires_at": expires_at,
            "actions": ["submit_meter_readings", "view_bills", "pay_split_sbp"]
        }

        # Deep link format in MAX
        max_link = f"https://max.ru/t226_hakaton_max_bot?startapp={token}"

        return GuestAccessResponse(
            guest_token=token,
            direct_max_link=max_link,
            property_address="г. Москва, ул. Ленина, д. 42, кв. 15",
            expires_at=expires_at,
            allowed_actions=["submit_meter_readings", "view_bills", "pay_split_sbp"]
        )

    def validate_guest_token(self, token: str) -> Optional[Dict]:
        data = self._active_tokens.get(token)
        if not data:
            return None
        if datetime.now() > data["expires_at"]:
            return None
        return data

guest_service = GuestAccessService()
