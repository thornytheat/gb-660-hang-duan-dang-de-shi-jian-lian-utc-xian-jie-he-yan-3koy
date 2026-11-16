import json
from app.errors import DomainError
from app.modules.m1_legs.keys import flight_key
from . import store
from .builder import build_content, canonical_bytes, digest, normalize_expected, normalize_leg, reason_message
from .contracts import CONTRACTS
from .validate import acceptance_issues

def _delivery_no(payload: dict) -> str:
    delivery_no = str(payload.get("delivery_no") or "").strip()
    if not delivery_no:
        raise DomainError("EMPTY_VALUE", "delivery_no 不能为空")
    if len(delivery_no) > 64:
        raise DomainError("BAD_FORMAT", "delivery_no 超长")
    return delivery_no

def activate_contract(version: str) -> dict:
    version = str(version or "").strip().upper()
    if version not in CONTRACTS:
        raise DomainError("UNKNOWN_CONTRACT", f"{reason_message('UNKNOWN_CONTRACT')}: {version}")
    store.activate_contract(version)
    return {"active_version": version, "name": CONTRACTS[version]["name"]}

def contract_info() -> dict:
    return {"active_version": store.active_contract(), "contracts": sorted(CONTRACTS)}

def _readback(package: dict, receipt: dict | None = None, created: bool | None = None) -> dict:
    data = {"delivery_no": package["delivery_no"], "chain_id": package["chain_id"], "contract_version": package["contract_version"], "status": package["status"], "missing": package["missing"], "content_sha256": package["content_sha256"], "receipt_sha256": receipt["receipt_sha256"] if receipt else None, "signed_at": package["signed_at"], "created_at": package["created_at"]}
    if created is not None:
        data["created"] = created
    return data

def build_package(payload: dict) -> dict:
    delivery_no = _delivery_no(payload)
    chain_id = str(payload.get("chain_id") or "").strip()
    if not chain_id:
        raise DomainError("EMPTY_VALUE", "chain_id 不能为空")
    raw_legs = payload.get("legs")
    if not isinstance(raw_legs, list) or not raw_legs:
        raise DomainError("EMPTY_VALUE", "legs 不能为空")
    legs = [normalize_leg(item) for item in raw_legs]
    if payload.get("expected") is None:
        expected = sorted({flight_key(leg) for leg in legs})
    else:
        expected = normalize_expected(payload["expected"])
    with store.immediate() as db:
        version = store.active_version_tx(db)
        content = build_content(delivery_no, chain_id, version, legs, expected)
        content_sha256 = digest(content)
        existing = store.get_package_tx(db, delivery_no)
        if existing:
            if existing["content_sha256"] != content_sha256:
                raise DomainError("DELIVERY_CONTENT_CONFLICT", reason_message("DELIVERY_CONTENT_CONFLICT"), 409)
            return _readback(existing, store.get_receipt_tx(db, delivery_no), created=False)
        record = {"delivery_no": delivery_no, "chain_id": chain_id, "contract_version": version, "content": content, "content_sha256": content_sha256, "status": "BUILT", "missing": [], "created_at": store.utc_now(), "signed_at": None}
        store.insert_package_tx(db, record)
        return _readback(record, created=True)

def _sign_response(package: dict, receipt: dict, recovered: bool) -> dict:
    return {"delivery_no": package["delivery_no"], "status": "SIGNED", "contract_version": package["contract_version"], "content_sha256": package["content_sha256"], "receipt_sha256": receipt["receipt_sha256"], "receipt": json.loads(receipt["receipt"]), "signed_at": receipt["signed_at"], "recovered": recovered}

def sign_package(payload: dict) -> dict:
    delivery_no = _delivery_no(payload)
    expect_sha = payload.get("content_sha256")
    rejection = None
    with store.immediate() as db:
        package = store.get_package_tx(db, delivery_no)
        if not package:
            raise DomainError("DELIVERY_NOT_FOUND", f"{reason_message('DELIVERY_NOT_FOUND')}: {delivery_no}", 404)
        if expect_sha and expect_sha != package["content_sha256"]:
            raise DomainError("DELIVERY_CONTENT_CONFLICT", reason_message("DELIVERY_CONTENT_CONFLICT"), 409)
        receipt = store.get_receipt_tx(db, delivery_no)
        if receipt:
            return _sign_response(package, receipt, recovered=True)
        if package["status"] == "REJECTED":
            rejection = package["missing"]
        else:
            content = json.loads(package["content"])
            issues = acceptance_issues(package["contract_version"], content["legs"], content["expected"])
            if issues:
                store.mark_rejected_tx(db, delivery_no, issues)
                rejection = issues
        if not rejection:
            signed_at = store.utc_now()
            receipt_bytes = canonical_bytes({"content_sha256": package["content_sha256"], "contract_version": package["contract_version"], "delivery_no": delivery_no, "receipt_no": f"RCPT-{delivery_no}", "signed_at": signed_at})
            receipt = {"delivery_no": delivery_no, "receipt": receipt_bytes, "receipt_sha256": digest(receipt_bytes), "signed_at": signed_at}
            store.record_receipt_tx(db, delivery_no, receipt_bytes, receipt["receipt_sha256"], signed_at)
            return _sign_response(package, receipt, recovered=False)
    raise DomainError("CHAIN_INCOMPLETE", reason_message("CHAIN_INCOMPLETE"), 422, extra={"delivery_no": delivery_no, "status": "REJECTED", "issues": rejection})

def read_package(delivery_no: str) -> dict:
    package = store.get_package(delivery_no)
    if not package:
        raise DomainError("DELIVERY_NOT_FOUND", f"{reason_message('DELIVERY_NOT_FOUND')}: {delivery_no}", 404)
    return _readback(package, store.get_receipt(delivery_no))

def package_content(delivery_no: str) -> bytes:
    package = store.get_package(delivery_no)
    if not package:
        raise DomainError("DELIVERY_NOT_FOUND", f"{reason_message('DELIVERY_NOT_FOUND')}: {delivery_no}", 404)
    return package["content"]

def package_receipt(delivery_no: str) -> bytes:
    if not store.get_package(delivery_no):
        raise DomainError("DELIVERY_NOT_FOUND", f"{reason_message('DELIVERY_NOT_FOUND')}: {delivery_no}", 404)
    receipt = store.get_receipt(delivery_no)
    if not receipt:
        raise DomainError("DELIVERY_NOT_SIGNED", f"{reason_message('DELIVERY_NOT_SIGNED')}: {delivery_no}", 409)
    return receipt["receipt"]

DEMO_LEGS = [
    {"flight_no": "MU5101", "flight_date": "2026-11-16", "origin": "PVG", "destination": "JFK", "departure_local": "2026-11-16T09:00", "arrival_local": "2026-11-16T10:30"},
    {"flight_no": "UA100", "flight_date": "2026-11-17", "origin": "JFK", "destination": "LHR", "departure_local": "2026-11-17T08:00", "arrival_local": "2026-11-17T19:50"},
]
DEMO_EXPECTED = ["MU5101|2026-11-16|PVG|JFK", "UA100|2026-11-17|JFK|LHR"]

def demo() -> dict:
    activate_contract("CHAIN-2")
    package = build_package({"delivery_no": "DLV-DEMO-20261116", "chain_id": "CH-DEMO-PVG-LHR", "legs": DEMO_LEGS, "expected": DEMO_EXPECTED})
    signed = sign_package({"delivery_no": package["delivery_no"]})
    return {"active_version": "CHAIN-2", "delivery_no": package["delivery_no"], "contract_version": package["contract_version"], "status": signed["status"], "receipt_sha256": signed["receipt_sha256"], "missing": [], "created": package["created"], "recovered": signed["recovered"]}
