"""
MAX WebApp HMAC-SHA256 initData cryptographic validation module.
Implements the MAX / Telegram Mini Apps cryptographic standard:
- Secret key: HMAC-SHA256(key=b"WebAppData", data=BOT_TOKEN)
- Data check string: alphabetically sorted 'key=value' pairs joined by '\n' (excluding 'hash')
- Signature check: HMAC-SHA256(key=secret_key, data=data_check_string) == hash
- Provides FastAPI dependencies: verify_max_init_data, verify_max_init_data_strict
- Provides developer utilities: validate_init_data, generate_init_data
"""

import os
import json
import time
import hmac
import hashlib
import urllib.parse
from typing import Optional, Dict, Any
from fastapi import Header, Query, HTTPException, status
from app.config import settings

def calculate_init_data_hash(data_dict: Dict[str, Any], bot_token: str) -> str:
    """
    Computes HMAC-SHA256 signature for data_dict according to MAX WebApp spec.
    """
    clean_dict = {k: v for k, v in data_dict.items() if k != "hash"}
    sorted_pairs = sorted(clean_dict.items(), key=lambda x: x[0])
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_pairs)

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return calculated_hash

def generate_init_data(data_dict: Dict[str, Any], bot_token: str) -> str:
    """
    Utility to generate a signed initData query string for testing or mocking.
    """
    calc_hash = calculate_init_data_hash(data_dict, bot_token)
    clean_dict = {k: v for k, v in data_dict.items() if k != "hash"}
    query_str = urllib.parse.urlencode(clean_dict)
    return f"{query_str}&hash={calc_hash}"

def validate_init_data(
    init_data_raw: str,
    bot_token: Optional[str] = None,
    max_age_seconds: Optional[int] = None
) -> Dict[str, Any]:
    """
    Validates the raw initData query string.
    Raises ValueError if invalid, returns parsed dictionary with 'user_data' if valid.
    """
    token = bot_token or settings.BOT_TOKEN
    if not token:
        raise ValueError("BOT_TOKEN is not configured")

    if not init_data_raw or not isinstance(init_data_raw, str):
        raise ValueError("Missing or invalid initData string")

    parsed = urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True)
    if not parsed:
        raise ValueError("Empty initData query string")

    data = dict(parsed)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise ValueError("initData does not contain 'hash'")

    expected_hash = calculate_init_data_hash(data, token)

    if not hmac.compare_digest(expected_hash.lower(), received_hash.lower()):
        raise ValueError("Invalid HMAC-SHA256 signature")

    if max_age_seconds is not None and "auth_date" in data:
        try:
            auth_date = int(data["auth_date"])
            if time.time() - auth_date > max_age_seconds:
                raise ValueError("initData has expired (auth_date too old)")
        except ValueError:
            raise

    if "user" in data:
        try:
            data["user_data"] = json.loads(data["user"])
        except Exception:
            data["user_data"] = {}

    return data

async def verify_max_init_data(
    x_init_data: Optional[str] = Header(None, alias="X-Init-Data"),
    authorization: Optional[str] = Header(None),
    init_data_query: Optional[str] = Query(None, alias="initData"),
    init_data_alt: Optional[str] = Query(None, alias="init_data")
) -> Dict[str, Any]:
    """
    FastAPI dependency that validates WebApp initData.
    Supports:
    - Header: X-Init-Data: ...
    - Header: Authorization: tma <initData> (or Bearer <initData>)
    - Query parameter: ?initData=... or ?init_data=...
    - Fallback: in DEV_MODE, allows requests without initData, returning a mock dev resident user.
    """
    raw_init_data = x_init_data or init_data_query or init_data_alt
    if not raw_init_data and authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() in ("tma", "bearer"):
            raw_init_data = parts[1]

    if raw_init_data:
        try:
            return validate_init_data(raw_init_data)
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid initData signature: {err}"
            )

    if settings.DEV_MODE:
        user_id = int(settings.BOT_ID) if settings.BOT_ID.isdigit() else 423938205
        return {
            "user": json.dumps({
                "id": user_id,
                "first_name": "Иван",
                "last_name": "Иванов",
                "username": "ivan_ivanov",
                "role": "resident"
            }),
            "user_data": {
                "id": user_id,
                "first_name": "Иван",
                "last_name": "Иванов",
                "username": "ivan_ivanov",
                "role": "resident"
            },
            "auth_date": str(int(time.time())),
            "is_dev_bypass": True
        }

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing X-Init-Data authentication header"
    )

async def verify_max_init_data_strict(
    x_init_data: Optional[str] = Header(None, alias="X-Init-Data"),
    authorization: Optional[str] = Header(None),
    init_data_query: Optional[str] = Query(None, alias="initData"),
) -> Dict[str, Any]:
    """
    Strict FastAPI dependency for endpoints requiring cryptographic proof without dev bypass.
    """
    raw_init_data = x_init_data or init_data_query
    if not raw_init_data and authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() in ("tma", "bearer"):
            raw_init_data = parts[1]

    if not raw_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required X-Init-Data header or initData query parameter"
        )

    try:
        return validate_init_data(raw_init_data)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid initData signature: {err}"
        )
