from flask import Blueprint, request, jsonify, Response
from app.pipeline import run
from app.errors import DomainError
from app import identity
import csv, io, json
from app.normalize import serial

bp = Blueprint("m8", __name__)

@bp.route("/api/m8", methods=["GET", "POST"])
def endpoint():
    payload = request.get_json(silent=True) if request.method == "POST" else {}
    if not isinstance(payload, dict):
        raise DomainError("BODY", "请求必须是对象")
    raw = payload.get("text")
    if raw is not None and not isinstance(raw, str):
        raise DomainError("TEXT", "数据必须为文本")
    return jsonify(run(raw, payload.get("params"), stop=8, persist=False))

@bp.get("/api/identity/links")
def list_links():
    return jsonify({"active": identity.active_links(), "all": identity.all_links()})

@bp.post("/api/identity/links")
def add_link():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise DomainError("BODY", "请求必须是对象")
    link, created = identity.submit_link(payload.get("er_patient"), payload.get("ip_patient"),
                                         payload.get("run_id"), payload.get("note"))
    return jsonify({"link": link, "created": created}), (201 if created else 200)

@bp.post("/api/identity/links/<link_id>/unlink")
def remove_link(link_id):
    payload = request.get_json(silent=True) or {}
    return jsonify({"link": identity.unlink(link_id, payload.get("note", ""))})

@bp.get("/api/identity/evidence.csv")
def identity_evidence():
    """身份接回证据：接回占床段（含依据 link_id）与 M8 隔离行。"""
    payload = request.args
    result = run(payload.get("text"), None, stop=8, persist=False)
    stream = io.StringIO(newline=""); writer = csv.writer(stream)
    writer.writerow(["批次", "主键/人", "原因码", "证据"])
    run_id = result["manifest"]["run_id"]
    for row in result.get("identity_intervals", []):
        writer.writerow([run_id, row.get("person", ""), "M8-OCCUPY", json.dumps(serial(row), ensure_ascii=False)])
    for row in result.get("identity_quarantine", []):
        writer.writerow([run_id, row["key"], "M8-" + row["code"], json.dumps(row, ensure_ascii=False)])
    return Response("﻿" + stream.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=identity_evidence.csv"})
