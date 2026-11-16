from datetime import datetime
from app.modules.m1_legs.keys import flight_key
from .contracts import CONTRACTS

def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def check_leg(leg: dict) -> list[dict]:
    minutes = int((_moment(leg["arrival_utc"]) - _moment(leg["departure_utc"])).total_seconds() // 60)
    if minutes <= 0 or minutes > 18 * 60:
        return [{"reason_code": "INVALID_FLIGHT_DURATION", "segment": flight_key(leg)}]
    return []

def check_chain(legs: list[dict], expected: list[str]) -> list[dict]:
    if not legs:
        return [{"reason_code": "EMPTY_CHAIN"}]
    issues = []
    present = {flight_key(leg) for leg in legs}
    for key in expected:
        if key not in present:
            issues.append({"reason_code": "MISSING_SEGMENT", "segment": key})
    for prev, nxt in zip(legs, legs[1:]):
        if prev["destination"] != nxt["origin"]:
            issues.append({"reason_code": "MISSING_SEGMENT", "segment": f"{prev['destination']}-{nxt['origin']}", "after": flight_key(prev), "before": flight_key(nxt)})
        if _moment(prev["arrival_utc"]) > _moment(nxt["departure_utc"]):
            issues.append({"reason_code": "CHAIN_TIME_CONFLICT", "after": flight_key(prev), "before": flight_key(nxt)})
    return issues

def acceptance_issues(contract_version: str, legs: list[dict], expected: list[str]) -> list[dict]:
    issues = [issue for leg in legs for issue in check_leg(leg)]
    if CONTRACTS[contract_version]["chain_level"]:
        issues += check_chain(legs, expected)
    return issues
