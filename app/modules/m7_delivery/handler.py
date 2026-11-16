from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import HTMLResponse
from app.web import page
from . import service

router = APIRouter()
ROWS = [{"key":"DLV-20261116-01","status":"已签收","value":"CHAIN-2"},{"key":"DLV-20261116-02","status":"拒签·缺段","value":"JFK-LHR"},{"key":"DLV-20261115-09","status":"重传","value":"逐字节"}]

@router.get("/delivery", response_class=HTMLResponse)
def view(request: Request):
    return page(request, "m7.html", "航链交付", "/api/delivery/demo", ROWS)

@router.get("/api/delivery/contracts")
def contracts():
    return service.contract_info()

@router.post("/api/delivery/contracts/activate")
def activate(payload: dict = Body(...)):
    return service.activate_contract(payload.get("version"))

@router.post("/api/delivery/packages")
def build(payload: dict = Body(...)):
    return service.build_package(payload)

@router.post("/api/delivery/sign")
def sign(payload: dict = Body(...)):
    return service.sign_package(payload)

@router.get("/api/delivery/packages/{delivery_no}")
def read(delivery_no: str):
    return service.read_package(delivery_no)

@router.get("/api/delivery/packages/{delivery_no}/content")
def content(delivery_no: str):
    return Response(content=service.package_content(delivery_no), media_type="application/json")

@router.get("/api/delivery/packages/{delivery_no}/receipt")
def receipt(delivery_no: str):
    return Response(content=service.package_receipt(delivery_no), media_type="application/json")

@router.post("/api/delivery/demo")
def demo():
    return service.demo()
