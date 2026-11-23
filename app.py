"""
HomeGuard Fall Alert Service (simulated)

A simplified backend for radar-based fall detection in the home.
Wall-mounted radar devices report posture frames for the person in the room;
the service decides when to confirm a fall and how to raise, escalate and
close alerts.

Run:
    pip install -r requirements.txt
    python app.py

API docs (Swagger UI): http://127.0.0.1:8000/docs
"""

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="HomeGuard Fall Alert Service", version="1.0.0")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FALL_CONFIDENCE_THRESHOLD = 0.80
CONFIRM_SECONDS = 30
ESCALATION_SECONDS = 120
RECOVERY_POSTURES = {"standing", "sitting", "lying_bed"}

Room = Literal["bathroom", "bedroom", "living_room"]
Posture = Literal["standing", "sitting", "lying_bed", "lying_floor", "fall", "absent"]
Target = Literal["person", "pet"]

# ---------------------------------------------------------------------------
# In-memory storage
# ---------------------------------------------------------------------------

devices: dict = {}   # device_id -> device record
alerts: dict = {}    # alert_id -> alert record


def _reset_storage():
    devices.clear()
    alerts.clear()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DeviceIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    resident_id: str = Field(min_length=1, max_length=64)
    room: Room
    firmware_version: str = Field(min_length=1, max_length=32)


class CareModeIn(BaseModel):
    enabled: bool


class FrameIn(BaseModel):
    target: Target
    posture: Posture
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_device_or_404(device_id: str) -> dict:
    d = devices.get(device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="device not found")
    return d


def _is_monitoring(d: dict) -> bool:
    return not d["care_mode"]


def _alerts_for(device_id: str, statuses: set):
    return [
        a for a in alerts.values()
        if a["device_id"] == device_id and a["status"] in statuses
    ]


def _escalate_if_due(device_id: str, now: datetime):
    for a in _alerts_for(device_id, {"open"}):
        if a["level"] == 1:
            elapsed = (now - a["created_at"]).total_seconds()
            if elapsed >= ESCALATION_SECONDS:
                a["level"] = 2
                a["escalated_at"] = now


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _serialize_device(d: dict) -> dict:
    return {
        "device_id": d["device_id"],
        "resident_id": d["resident_id"],
        "room": d["room"],
        "firmware_version": d["firmware_version"],
        "care_mode": d["care_mode"],
        "monitoring": _is_monitoring(d),
        "fall_suspected": d["pending_since"] is not None,
    }


def _serialize_alert(a: dict) -> dict:
    return {
        "alert_id": a["alert_id"],
        "device_id": a["device_id"],
        "resident_id": a["resident_id"],
        "room": a["room"],
        "level": a["level"],
        "status": a["status"],
        "message": a["message"],
        "created_at": _iso(a["created_at"]),
        "escalated_at": _iso(a["escalated_at"]),
        "acknowledged_at": _iso(a["acknowledged_at"]),
        "resolved_at": _iso(a["resolved_at"]),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/devices", status_code=201)
def register_device(body: DeviceIn):
    if body.device_id in devices:
        raise HTTPException(status_code=409, detail="device already registered")

    devices[body.device_id] = {
        "device_id": body.device_id,
        "resident_id": body.resident_id,
        "room": body.room,
        "firmware_version": body.firmware_version,
        "care_mode": False,
        "pending_since": None,
        "armed": True,
        "registered_at": datetime.now(timezone.utc),
    }
    return _serialize_device(devices[body.device_id])


@app.get("/devices/{device_id}")
def get_device(device_id: str):
    return _serialize_device(_get_device_or_404(device_id))


@app.post("/devices/{device_id}/care-mode")
def set_care_mode(device_id: str, body: CareModeIn):
    d = _get_device_or_404(device_id)

    if body.enabled and not d["care_mode"]:
        d["pending_since"] = None
    if not body.enabled and d["care_mode"]:
        d["pending_since"] = None
        d["armed"] = True

    d["care_mode"] = body.enabled
    return _serialize_device(d)


@app.post("/devices/{device_id}/frames", status_code=201)
def post_frame(device_id: str, body: FrameIn):
    d = devices[device_id]

    if not _is_monitoring(d):
        return {"accepted": True, "monitoring": False, "alert": None}

    _escalate_if_due(device_id, body.timestamp)

    if body.target != "person":
        return {"accepted": True, "monitoring": True, "alert": None}

    alert_raised = None

    if d["pending_since"] is None:
        if (
            body.posture == "fall"
            and body.confidence > FALL_CONFIDENCE_THRESHOLD
            and d["armed"]
        ):
            d["pending_since"] = body.timestamp
    else:
        if body.posture in RECOVERY_POSTURES:
            d["pending_since"] = None
        elif body.posture == "lying_floor":
            elapsed = (body.timestamp - d["pending_since"]).total_seconds()
            if elapsed >= CONFIRM_SECONDS:
                alert_id = str(uuid.uuid4())
                alerts[alert_id] = {
                    "alert_id": alert_id,
                    "device_id": device_id,
                    "resident_id": d["resident_id"],
                    "room": d["room"],
                    "level": 1,
                    "status": "open",
                    "message": f"Possible fall detcted in {d['room']}",
                    "created_at": body.timestamp,
                    "escalated_at": None,
                    "acknowledged_at": None,
                    "resolved_at": None,
                }
                d["pending_since"] = None
                d["armed"] = False
                alert_raised = _serialize_alert(alerts[alert_id])

    return {"accepted": True, "monitoring": True, "alert": alert_raised}


@app.get("/devices/{device_id}/alerts")
def list_alerts(
    device_id: str,
    status: Optional[Literal["open", "acknowledged", "resolved"]] = None,
):
    _get_device_or_404(device_id)
    statuses = {status} if status else {"open", "acknowledged", "resolved"}
    result = [_serialize_alert(a) for a in _alerts_for(device_id, statuses)]
    result.sort(key=lambda x: x["created_at"])
    return result


@app.post("/alerts/{alert_id}/ack")
def acknowledge_alert(alert_id: str):
    a = alerts.get(alert_id)
    if a is None:
        raise HTTPException(status_code=404, detail="alert not found")
    if a["status"] != "open":
        raise HTTPException(status_code=409, detail=f"alert is {a['status']}")

    a["status"] = "acknowledged"
    a["acknowledged_at"] = datetime.now(timezone.utc)
    return _serialize_alert(a)


@app.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str):
    a = alerts.get(alert_id)
    if a is None:
        raise HTTPException(status_code=404, detail="alert not found")
    if a["status"] == "resolved":
        raise HTTPException(status_code=409, detail="alert is resolved")

    a["status"] = "resolved"
    a["resolved_at"] = datetime.now(timezone.utc)
    return _serialize_alert(a)


@app.post("/_test/reset")
def reset():
    """Test helper: clears all in-memory data."""
    _reset_storage()
    return {"reset": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
