"""
User service alias module.
Exports UserProfileService and profile_service for universal backward and forward API compatibility.
"""

from app.services.profile_service import (
    profile_service,
    UserProfileService,
    normalize_phone_number,
    parse_vcf_phone,
    DEFAULT_USER_ID,
)

# Alias singleton
user_service = profile_service


def add_property_manual(user_id: int, address: str, management_company: str = "ООО УК Столица-Сервис", **kwargs):
    """Module-level delegation to user_service.add_property_manual."""
    return user_service.add_property_manual(user_id, address, management_company=management_company, **kwargs)


__all__ = [
    "profile_service",
    "user_service",
    "UserProfileService",
    "add_property_manual",
    "normalize_phone_number",
    "parse_vcf_phone",
    "DEFAULT_USER_ID",
]
