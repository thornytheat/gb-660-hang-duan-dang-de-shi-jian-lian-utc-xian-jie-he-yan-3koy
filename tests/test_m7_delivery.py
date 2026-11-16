import json
from hashlib import sha256
from threading import Thread

import pytest
from fastapi.testclient import TestClient

import app.modules.m7_delivery.store as store
from app.main import app
from app.modules.m7_delivery import service

LEG_A = {"flight_no": "MU5101", "flight_date": "2026-11-16", "origin": "PVG", "destination": "JFK", "departure_utc": "2026-11-16T01:00:00Z", "arrival_utc": "2026-11-16T15:30:00Z"}
LEG_B = {"flight_no": "UA100", "flight_date": "2026-11-17", "origin": "JFK", "destination": "LHR", "departure_utc": "2026-11-17T13:00:00Z", "arrival_utc": "2026-11-17T19:50:00Z"}
LEG_C = {"flight_no": "UA200", "flight_date": "2026-11-18", "origin": "LHR", "destination": "SFO", "departure_utc": "2026-11-18T10:00:00Z", "arrival_utc": "2026-11-18T13:00:00Z"}
LEG_OVERLAP = {"flight_no": "UA300", "flight_date": "2026-11-16", "origin": "JFK", "destination": "LHR", "departure_utc": "2026-11-16T14:00:00Z", "arrival_utc": "2026-11-16T20:00:00Z"}
KEY_A = "MU5101|2026-11-16|PVG|JFK"
KEY_B = "UA100|2026-11-17|JFK|LHR"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATABASE_PATH", tmp_path / "delivery.sqlite3")
    return TestClient(app)


def build(client, delivery_no, legs, expected=None, chain_id="CH-1"):
    payload = {"delivery_no": delivery_no, "chain_id": chain_id, "legs": legs}
    if expected is not None:
        payload["expected"] = expected
    return client.post("/api/delivery/packages", json=payload)


def test_contract_activation_switches_version(client):
    assert client.get("/api/delivery/contracts").json()["active_version"] == "LEG-1"
    response = client.post("/api/delivery/contracts/activate", json={"version": "CHAIN-2"})
    assert response.status_code == 200 and response.json()["active_version"] == "CHAIN-2"
    assert client.get("/api/delivery/contracts").json()["active_version"] == "CHAIN-2"


def test_unknown_contract_rejected(client):
    response = client.post("/api/delivery/contracts/activate", json={"version": "CHAIN-9"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "UNKNOWN_CONTRACT"


def test_build_pins_active_contract_version(client):
    first = build(client, "DLV-1", [LEG_A, LEG_B]).json()
    assert first["contract_version"] == "LEG-1" and first["status"] == "BUILT"
    client.post("/api/delivery/contracts/activate", json={"version": "CHAIN-2"})
    second = build(client, "DLV-2", [LEG_A, LEG_B]).json()
    assert second["contract_version"] == "CHAIN-2"
    assert client.get("/api/delivery/packages/DLV-1").json()["contract_version"] == "LEG-1"


def test_build_idempotent_same_content(client):
    first = build(client, "DLV-1", [LEG_A, LEG_B]).json()
    second = build(client, "DLV-1", [LEG_A, LEG_B]).json()
    assert first["created"] and not second["created"]
    assert first["content_sha256"] == second["content_sha256"] and store.package_count() == 1


def test_same_number_different_content_rejected(client):
    build(client, "DLV-1", [LEG_A, LEG_B])
    conflict = build(client, "DLV-1", [LEG_A])
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "DELIVERY_CONTENT_CONFLICT"
    sign = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1", "content_sha256": "0" * 64})
    assert sign.status_code == 409 and sign.json()["detail"]["code"] == "DELIVERY_CONTENT_CONFLICT"


def test_legacy_contract_signs_despite_gap(client):
    build(client, "DLV-1", [LEG_A, LEG_C])
    signed = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"}).json()
    assert signed["status"] == "SIGNED" and signed["contract_version"] == "LEG-1"


def test_new_contract_rejects_missing_segment(client):
    client.post("/api/delivery/contracts/activate", json={"version": "CHAIN-2"})
    build(client, "DLV-1", [LEG_A], expected=[KEY_A, KEY_B])
    rejected = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"})
    assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "CHAIN_INCOMPLETE"
    issues = rejected.json()["detail"]["issues"]
    assert {"reason_code": "MISSING_SEGMENT", "segment": KEY_B} in issues
    readback = client.get("/api/delivery/packages/DLV-1").json()
    assert readback["status"] == "REJECTED" and readback["missing"] == issues
    again = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"})
    assert again.status_code == 422 and again.json()["detail"]["issues"] == issues


def test_station_gap_reported_as_missing_segment(client):
    client.post("/api/delivery/contracts/activate", json={"version": "CHAIN-2"})
    build(client, "DLV-1", [LEG_A, LEG_C])
    issues = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"}).json()["detail"]["issues"]
    gaps = [issue for issue in issues if issue["reason_code"] == "MISSING_SEGMENT"]
    assert any(issue["segment"] == "JFK-LHR" for issue in gaps)


def test_utc_time_conflict_rejected(client):
    client.post("/api/delivery/contracts/activate", json={"version": "CHAIN-2"})
    build(client, "DLV-1", [LEG_A, LEG_OVERLAP])
    issues = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"}).json()["detail"]["issues"]
    assert any(issue["reason_code"] == "CHAIN_TIME_CONFLICT" for issue in issues)


def test_sign_retry_does_not_duplicate(client):
    client.post("/api/delivery/contracts/activate", json={"version": "CHAIN-2"})
    build(client, "DLV-1", [LEG_A, LEG_B], expected=[KEY_A, KEY_B])
    first = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"}).json()
    second = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"}).json()
    assert not first["recovered"] and second["recovered"]
    assert first["receipt_sha256"] == second["receipt_sha256"] and first["signed_at"] == second["signed_at"]
    assert store.receipt_count() == 1


def test_interrupted_signing_recovers_by_delivery_no(client):
    client.post("/api/delivery/contracts/activate", json={"version": "CHAIN-2"})
    build(client, "DLV-1", [LEG_A, LEG_B], expected=[KEY_A, KEY_B])
    original = client.get("/api/delivery/packages/DLV-1").json()
    assert original["status"] == "BUILT"
    client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"})
    recovered = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"}).json()
    readback = client.get("/api/delivery/packages/DLV-1").json()
    assert readback["status"] == "SIGNED" and readback["receipt_sha256"] == recovered["receipt_sha256"]


def test_retransmit_is_byte_identical(client):
    client.post("/api/delivery/contracts/activate", json={"version": "CHAIN-2"})
    built = build(client, "DLV-1", [LEG_A, LEG_B], expected=[KEY_A, KEY_B]).json()
    signed = client.post("/api/delivery/sign", json={"delivery_no": "DLV-1"}).json()
    content = client.get("/api/delivery/packages/DLV-1/content")
    receipt = client.get("/api/delivery/packages/DLV-1/receipt")
    assert sha256(content.content).hexdigest() == built["content_sha256"]
    assert sha256(receipt.content).hexdigest() == signed["receipt_sha256"]
    assert json.loads(receipt.content)["receipt_no"] == "RCPT-DLV-1"
    assert client.get("/api/delivery/packages/DLV-1/receipt").content == receipt.content


def test_unsigned_receipt_not_available(client):
    build(client, "DLV-1", [LEG_A])
    response = client.get("/api/delivery/packages/DLV-1/receipt")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "DELIVERY_NOT_SIGNED"
    assert client.get("/api/delivery/packages/DLV-X").status_code == 404


def test_local_time_legs_converted_to_utc(client):
    built = build(client, "DLV-1", [{"flight_no": "mu5101", "flight_date": "2026-11-16", "origin": "pvg", "destination": "jfk", "departure_local": "2026-11-16T09:00", "arrival_local": "2026-11-16T10:30"}]).json()
    content = json.loads(client.get("/api/delivery/packages/DLV-1/content").content)
    assert content["legs"][0]["departure_utc"] == "2026-11-16T01:00:00Z"
    assert content["legs"][0]["flight_no"] == "MU5101" and built["status"] == "BUILT"


def test_demo_flow_is_idempotent(client):
    first = client.post("/api/delivery/demo").json()
    second = client.post("/api/delivery/demo").json()
    assert first["status"] == "SIGNED" and first["contract_version"] == "CHAIN-2"
    assert first["receipt_sha256"] == second["receipt_sha256"] and second["recovered"]
    assert store.receipt_count() == 1


def test_contract_switch_and_build_share_one_version(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATABASE_PATH", tmp_path / "delivery.sqlite3")

    def switch(n):
        for i in range(6):
            service.activate_contract("CHAIN-2" if (n + i) % 2 else "LEG-1")

    def make(n):
        for i in range(6):
            service.build_package({"delivery_no": f"DLV-T{n}-{i}", "chain_id": "CH", "legs": [LEG_A, LEG_B]})

    threads = [Thread(target=switch, args=(n,)) for n in range(3)] + [Thread(target=make, args=(n,)) for n in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for n in range(3):
        for i in range(6):
            delivery_no = f"DLV-T{n}-{i}"
            info = service.read_package(delivery_no)
            embedded = json.loads(service.package_content(delivery_no))["contract_version"]
            assert info["contract_version"] == embedded
