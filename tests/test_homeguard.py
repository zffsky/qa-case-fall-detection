"""Black-box API tests for the locally running HomeGuard service."""
import os
import uuid

import pytest
import requests


BASE_URL = os.getenv("HOMEGUARD_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = 5


@pytest.fixture(autouse=True)
def clean_service():
    # 中文：每个测试前后调用测试重置接口，保证用例隔离并支持重复运行。
    # English: Reset the service before and after each test to ensure isolation and repeatability.
    response = requests.post(f"{BASE_URL}/_test/reset", timeout=TIMEOUT)
    response.raise_for_status()
    yield
    response = requests.post(f"{BASE_URL}/_test/reset", timeout=TIMEOUT)
    response.raise_for_status()


@pytest.fixture
def device_id():
    # 中文：生成本次测试专用的设备编号，避免设备编号冲突。
    # English: Generate a device ID dedicated to this test to avoid ID collisions.
    return f"qa-{uuid.uuid4().hex[:12]}"


def register(device_id, room="bathroom"):
    # 中文：通过设备注册接口创建测试设备，并返回原始 HTTP 响应供断言使用。
    # English: Register a test device through the API and return the raw HTTP response for assertions.
    return requests.post(
        f"{BASE_URL}/devices",
        json={"device_id": device_id, "resident_id": "R-QA", "room": room, "firmware_version": "1.0"},
        timeout=TIMEOUT,
    )


def send_frame(device_id, posture, timestamp, confidence=0.9, target="person"):
    # 中文：构造并提交姿态帧；参数可覆盖目标、姿态、置信度和设备时间戳。
    # English: Build and submit a posture frame; target, posture, confidence, and device timestamp are configurable.
    return requests.post(
        f"{BASE_URL}/devices/{device_id}/frames",
        json={"target": target, "posture": posture, "confidence": confidence, "timestamp": timestamp},
        timeout=TIMEOUT,
    )


def test_health_and_device_registration(device_id):
    # 中文：验证健康检查、设备默认状态，以及重复注册时的冲突响应。
    # English: Verify the health check, default device state, and duplicate-registration conflict.
    health = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    response = register(device_id)
    assert response.status_code == 201
    assert response.json()["monitoring"] is True
    assert response.json()["care_mode"] is False
    assert response.json()["fall_suspected"] is False
    assert register(device_id).status_code == 409


def test_fall_alert_ack_resolve_and_recurrence(device_id):
    # 中文：验证跌倒到一级告警、确认、处理完成，以及处理后再次跌倒可重新告警的完整业务流程。
    # English: Verify the full fall-to-level-1 alert, acknowledgment, resolution, and post-resolution recurrence flow.
    assert register(device_id).status_code == 201
    assert send_frame(device_id, "fall", "2026-10-01T08:00:00").json()["alert"] is None
    assert send_frame(device_id, "lying_floor", "2026-10-01T08:00:29").json()["alert"] is None
    alert = send_frame(device_id, "lying_floor", "2026-10-01T08:00:30").json()["alert"]
    assert alert["level"] == 1
    assert alert["status"] == "open"
    assert alert["message"] == "Possible fall detected in bathroom"

    alert_id = alert["alert_id"]
    ack = requests.post(f"{BASE_URL}/alerts/{alert_id}/ack", timeout=TIMEOUT)
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"
    resolved = requests.post(f"{BASE_URL}/alerts/{alert_id}/resolve", timeout=TIMEOUT)
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    send_frame(device_id, "fall", "2026-10-01T09:00:00")
    second = send_frame(device_id, "lying_floor", "2026-10-01T09:00:30").json()["alert"]
    assert second["alert_id"] != alert_id
    assert second["status"] == "open"


def test_confidence_boundary_and_recovery(device_id):
    # 中文：验证置信度 0.80 的包含边界会进入疑似状态，恢复姿态会清除疑似状态。
    # English: Verify that the inclusive 0.80 confidence boundary starts suspicion and a recovery posture clears it.
    register(device_id)
    send_frame(device_id, "fall", "2026-10-01T08:00:00", confidence=0.80)
    state = requests.get(f"{BASE_URL}/devices/{device_id}", timeout=TIMEOUT).json()
    assert state["fall_suspected"] is True

    # 中文：补充验证低于阈值的 0.79 不应启动疑似状态。
    # English: Also verify that confidence 0.79, below the threshold, does not start suspicion.
    assert register(f"{device_id}-low").status_code == 201
    send_frame(f"{device_id}-low", "fall", "2026-10-01T08:00:00", confidence=0.79)
    assert requests.get(f"{BASE_URL}/devices/{device_id}-low", timeout=TIMEOUT).json()["fall_suspected"] is False

    send_frame(device_id, "standing", "2026-10-01T08:00:10")
    assert requests.get(f"{BASE_URL}/devices/{device_id}", timeout=TIMEOUT).json()["fall_suspected"] is False


def test_confidence_above_threshold_triggers_alert(device_id):
    # 中文：验证置信度高于 0.80 时进入疑似状态，并在倒地满 30 秒后触发一级告警。
    # English: Verify confidence above 0.80 starts suspicion and triggers a level-1 alert after 30 seconds on the floor.
    assert register(device_id).status_code == 201
    fall = send_frame(device_id, "fall", "2026-10-01T08:00:00", confidence=0.81)
    assert fall.status_code == 201
    assert fall.json()["alert"] is None
    assert requests.get(f"{BASE_URL}/devices/{device_id}", timeout=TIMEOUT).json()["fall_suspected"] is True

    before_threshold = send_frame(device_id, "lying_floor", "2026-10-01T08:00:29")
    assert before_threshold.json()["alert"] is None
    at_threshold = send_frame(device_id, "lying_floor", "2026-10-01T08:00:30")
    assert at_threshold.json()["alert"]["level"] == 1


def test_pet_frame_does_not_clear_suspected_fall(device_id):
    # 中文：验证宠物上报的恢复姿态不会清除人的疑似跌倒状态。
    # English: Verify that a pet recovery posture does not clear a person's suspected-fall state.
    register(device_id)
    send_frame(device_id, "fall", "2026-10-01T08:00:00")
    send_frame(device_id, "standing", "2026-10-01T08:00:10", target="pet")
    state = requests.get(f"{BASE_URL}/devices/{device_id}", timeout=TIMEOUT).json()
    assert state["fall_suspected"] is True


def test_care_mode_suppresses_detection_and_clears_pending(device_id):
    # 中文：验证开启照护模式会清除当前疑似状态并暂停判定，关闭后恢复监测。
    # English: Verify that care mode clears pending suspicion and suppresses detection until monitoring resumes.
    register(device_id)
    send_frame(device_id, "fall", "2026-10-01T08:00:00")
    enabled = requests.post(f"{BASE_URL}/devices/{device_id}/care-mode", json={"enabled": True}, timeout=TIMEOUT)
    assert enabled.status_code == 200
    assert enabled.json()["fall_suspected"] is False

    suppressed = send_frame(device_id, "fall", "2026-10-01T08:01:00").json()
    assert suppressed == {"accepted": True, "monitoring": False, "alert": None}
    disabled = requests.post(f"{BASE_URL}/devices/{device_id}/care-mode", json={"enabled": False}, timeout=TIMEOUT)
    assert disabled.status_code == 200
    assert disabled.json()["monitoring"] is True

    # 中文：关闭照护模式后重新上报完整有效序列，应可再次触发告警。
    # English: After care mode is disabled, a new valid sequence should trigger an alert again.
    send_frame(device_id, "fall", "2026-10-01T08:02:00")
    resumed = send_frame(device_id, "lying_floor", "2026-10-01T08:02:30").json()
    assert resumed["alert"]["status"] == "open"


def test_escalation_and_ack_state_conflict(device_id):
    # 中文：验证未确认告警经过 120 秒升级为二级，并检查重复确认返回冲突。
    # English: Verify escalation to level 2 after 120 unacknowledged seconds and conflict on duplicate acknowledgment.
    register(device_id)
    send_frame(device_id, "fall", "2026-10-01T08:00:00")
    alert = send_frame(device_id, "lying_floor", "2026-10-01T08:00:30").json()["alert"]
    alert_id = alert["alert_id"]
    # 中文：升级前 119 秒仍应保持一级告警。
    # English: The alert should remain at level 1 after 119 seconds, before escalation is due.
    send_frame(device_id, "absent", "2026-10-01T08:02:29")
    before_due = requests.get(f"{BASE_URL}/devices/{device_id}/alerts", params={"status": "open"}, timeout=TIMEOUT).json()[0]
    assert before_due["level"] == 1

    # 中文：恰好到达 120 秒边界时升级为二级。
    # English: The alert should escalate to level 2 exactly at the 120-second boundary.
    send_frame(device_id, "absent", "2026-10-01T08:02:30")
    listed = requests.get(f"{BASE_URL}/devices/{device_id}/alerts", params={"status": "open"}, timeout=TIMEOUT)
    assert listed.status_code == 200
    assert listed.json()[0]["level"] == 2
    assert listed.json()[0]["escalated_at"] is not None
    assert requests.post(f"{BASE_URL}/alerts/{alert_id}/ack", timeout=TIMEOUT).status_code == 200
    assert requests.post(f"{BASE_URL}/alerts/{alert_id}/ack", timeout=TIMEOUT).status_code == 409

    # 中文：已确认状态的告警再次确认应冲突；已确认告警仍允许直接处理完成。
    # English: Acknowledging an already acknowledged alert should conflict, while resolving it remains allowed.
    assert requests.post(f"{BASE_URL}/alerts/{alert_id}/resolve", timeout=TIMEOUT).json()["status"] == "resolved"
    assert requests.post(f"{BASE_URL}/alerts/{alert_id}/ack", timeout=TIMEOUT).status_code == 409


def test_invalid_inputs_and_missing_resources(device_id):
    # 中文：验证非法房间、非法姿态帧字段和不存在的设备/告警返回预期错误码。
    # English: Verify expected error responses for an invalid room, invalid frame fields, and missing devices or alerts.
    invalid_device = requests.post(
        f"{BASE_URL}/devices",
        json={"device_id": device_id, "resident_id": "R-QA", "room": "garage", "firmware_version": "1.0"},
        timeout=TIMEOUT,
    )
    assert invalid_device.status_code == 422

    assert requests.get(f"{BASE_URL}/devices/missing", timeout=TIMEOUT).status_code == 404
    assert send_frame("missing", "fall", "2026-10-01T08:00:00").status_code == 404
    assert requests.post(f"{BASE_URL}/alerts/missing/ack", timeout=TIMEOUT).status_code == 404

    assert register(device_id).status_code == 201
    invalid_frame = requests.post(
        f"{BASE_URL}/devices/{device_id}/frames",
        json={"target": "person", "posture": "fall", "confidence": 1.1, "timestamp": "bad"},
        timeout=TIMEOUT,
    )
    assert invalid_frame.status_code == 422


def test_missing_required_device_fields_are_rejected(device_id):
    # 中文：验证注册设备时缺少必填字段会返回 422。
    # English: Verify that device registration without required fields returns 422.
    response = requests.post(
        f"{BASE_URL}/devices",
        json={"device_id": device_id, "resident_id": "R-QA", "room": "bedroom"},
        timeout=TIMEOUT,
    )
    assert response.status_code == 422


def test_invalid_posture_and_frame_missing_fields_are_rejected(device_id):
    # 中文：验证非法姿态和缺少必填字段的帧请求均返回 422。
    # English: Verify that frames with an invalid posture or missing required fields return 422.
    assert register(device_id).status_code == 201
    invalid_posture = send_frame(device_id, "crawling", "2026-10-01T08:00:00")
    assert invalid_posture.status_code == 422
    missing_field = requests.post(
        f"{BASE_URL}/devices/{device_id}/frames",
        json={"target": "person", "posture": "fall", "timestamp": "2026-10-01T08:00:00"},
        timeout=TIMEOUT,
    )
    assert missing_field.status_code == 422


def test_multiple_devices_keep_suspicion_and_alerts_isolated(device_id):
    # 中文：验证两台设备的疑似状态和告警数据互不串扰。
    # English: Verify that suspected-fall state and alerts remain isolated between two devices.
    second_device = f"{device_id}-second"
    assert register(device_id).status_code == 201
    assert register(second_device, room="bedroom").status_code == 201
    send_frame(device_id, "fall", "2026-10-01T08:00:00")
    send_frame(second_device, "fall", "2026-10-01T08:00:00")
    first_alert = send_frame(device_id, "lying_floor", "2026-10-01T08:00:30").json()["alert"]
    assert first_alert["room"] == "bathroom"
    assert requests.get(f"{BASE_URL}/devices/{device_id}", timeout=TIMEOUT).json()["fall_suspected"] is False
    assert requests.get(f"{BASE_URL}/devices/{second_device}", timeout=TIMEOUT).json()["fall_suspected"] is True
    assert requests.get(f"{BASE_URL}/devices/{second_device}/alerts", timeout=TIMEOUT).json() == []


def test_resolve_open_alert_and_reject_second_resolve(device_id):
    # 中文：验证 open 告警可以直接处理完成，已处理告警不能重复处理。
    # English: Verify that an open alert can be resolved directly and a resolved alert cannot be resolved again.
    register(device_id)
    send_frame(device_id, "fall", "2026-10-01T08:00:00")
    alert = send_frame(device_id, "lying_floor", "2026-10-01T08:00:30").json()["alert"]
    url = f"{BASE_URL}/alerts/{alert['alert_id']}/resolve"
    assert requests.post(url, timeout=TIMEOUT).json()["status"] == "resolved"
    assert requests.post(url, timeout=TIMEOUT).status_code == 409
