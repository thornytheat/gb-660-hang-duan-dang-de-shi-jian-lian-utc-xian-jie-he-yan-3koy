import json
from datetime import date, datetime, timezone
from hashlib import sha256
from app.errors import DomainError
from app.modules.m1_legs.keys import flight_key, normalize_flight_no
from app.modules.m3_localtime.resolve import utc_iso
from app.modules.m4_itinerary.builder import build_leg
from app.reasons import REASONS as BASE_REASONS

DELIVERY_REASONS = {
    "EMPTY_CHAIN": "航链没有航段",
    "MISSING_SEGMENT": "航链缺少航段",
    "CHAIN_TIME_CONFLICT": "航链 UTC 时刻不连续",
    "CHAIN_INCOMPLETE": "航链不齐备或 UTC 不连续，拒签",
    "DELIVERY_CONTENT_CONFLICT": "同一交付号内容不一致",
    "DELIVERY_NOT_FOUND": "交付号不存在",
    "DELIVERY_NOT_SIGNED": "交付包尚未签收",
    "UNKNOWN_CONTRACT": "契约版本不存在",
    "INVALID_FLIGHT_DURATION": "到达必须晚于起飞且不超过 18 小时",
    "BAD_FLIGHT_NO": "航班号格式不正确",
}

def reason_message(code: str) -> str:
    return DELIVERY_REASONS.get(code) or BASE_REASONS.get(code) or code

def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def digest(content: bytes) -> str:
    return sha256(content).hexdigest()

def _parse_utc(value: str | None, field: str) -> datetime:
    if not value:
        raise DomainError("EMPTY_VALUE", f"航段缺少 {field}")
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise DomainError("BAD_FORMAT", f"{field} 不是合法 ISO 时刻")
    if moment.tzinfo is None:
        raise DomainError("BAD_FORMAT", f"{field} 缺少时区偏移")
    return moment.astimezone(timezone.utc)

def _check_duration(departure: datetime, arrival: datetime) -> None:
    minutes = int((arrival - departure).total_seconds() // 60)
    if minutes <= 0 or minutes > 18 * 60:
        raise DomainError("INVALID_FLIGHT_DURATION", reason_message("INVALID_FLIGHT_DURATION"))

def _fold(row: dict, field: str) -> int | None:
    value = row.get(field)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        raise DomainError("BAD_FORMAT", f"{field} 必须是整数")

def _identity(raw: dict) -> tuple[str, str, str, str]:
    row = {str(key).strip(): ("" if value is None else str(value).strip()) for key, value in raw.items()}
    for field in ("flight_no", "flight_date", "origin", "destination"):
        if not row.get(field):
            raise DomainError("EMPTY_VALUE", f"航段缺少 {field}")
    try:
        flight_no = normalize_flight_no(row["flight_no"])
    except ValueError:
        raise DomainError("BAD_FLIGHT_NO", reason_message("BAD_FLIGHT_NO"))
    try:
        date.fromisoformat(row["flight_date"])
    except ValueError:
        raise DomainError("BAD_FORMAT", f"flight_date 格式不正确: {row['flight_date']}")
    return flight_no, row["flight_date"], row["origin"].upper(), row["destination"].upper()

def normalize_leg(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise DomainError("BAD_FORMAT", "航段必须是对象")
    flight_no, flight_date, origin, destination = _identity(raw)
    row = {str(key).strip(): ("" if value is None else str(value).strip()) for key, value in raw.items()}
    if row.get("departure_utc") or row.get("arrival_utc"):
        departure = _parse_utc(row.get("departure_utc"), "departure_utc")
        arrival = _parse_utc(row.get("arrival_utc"), "arrival_utc")
        _check_duration(departure, arrival)
        return {"flight_no": flight_no, "flight_date": flight_date, "origin": origin, "destination": destination, "departure_utc": utc_iso(departure), "arrival_utc": utc_iso(arrival)}
    for field in ("departure_local", "arrival_local"):
        if not row.get(field):
            raise DomainError("EMPTY_VALUE", f"航段缺少 {field}")
    try:
        built = build_leg({"flight_no": flight_no, "origin": origin, "destination": destination, "departure_local": row["departure_local"], "arrival_local": row["arrival_local"], "departure_fold": _fold(row, "departure_fold"), "arrival_fold": _fold(row, "arrival_fold")})
    except ValueError as exc:
        code = str(exc) or "BAD_FORMAT"
        raise DomainError(code, reason_message(code)) from exc
    return {"flight_no": flight_no, "flight_date": flight_date, "origin": origin, "destination": destination, "departure_utc": utc_iso(datetime.fromisoformat(built["departure_utc"])), "arrival_utc": utc_iso(datetime.fromisoformat(built["arrival_utc"]))}

def normalize_expected(items: list) -> list[str]:
    keys = set()
    for item in items:
        if isinstance(item, dict):
            flight_no, flight_date, origin, destination = _identity(item)
            keys.add(flight_key({"flight_no": flight_no, "flight_date": flight_date, "origin": origin, "destination": destination}))
            continue
        parts = [part.strip() for part in str(item).strip().split("|")]
        if len(parts) != 4 or any(not part for part in parts):
            raise DomainError("BAD_FORMAT", f"expected 键格式不正确: {item}")
        _valid_key_date(parts[1])
        keys.add(flight_key({"flight_no": parts[0], "flight_date": parts[1], "origin": parts[2].upper(), "destination": parts[3].upper()}))
    return sorted(keys)

def _valid_key_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        raise DomainError("BAD_FORMAT", f"expected 键日期格式不正确: {value}")

def build_content(delivery_no: str, chain_id: str, contract_version: str, legs: list[dict], expected: list[str]) -> bytes:
    ordered = sorted(legs, key=lambda leg: (leg["departure_utc"], leg["flight_no"]))
    return canonical_bytes({"chain_id": chain_id, "contract_version": contract_version, "delivery_no": delivery_no, "expected": sorted(expected), "legs": ordered})
