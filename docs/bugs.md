# 缺陷记录

## B1 — P1：同一设备可产生多个未结束告警

**复现**：注册设备；fall 08:00:00、lying_floor 08:00:30 触发告警；继续 fall 09:00:00、lying_floor 09:00:30。第二次帧仍返回新告警。

**期望**：R8 要求同设备最多存在一个 open/acknowledged 告警；第二次不应新增未结束告警。

**实际**：实现没有检查现存告警，每次满足条件都会创建新告警。

**自动化用例**：`test_fall_alert_ack_resolve_and_recurrence` 已覆盖 resolved 后再次跌倒，并断言返回新的 open 告警。该断言预计失败：resolve 接口没有将设备 `armed` 重置为 `true`，后续 fall 帧被忽略。

## B2 — P1：置信度恰为 0.80 时未进入疑似状态

**复现**：注册后上报 `target=person, posture=fall, confidence=0.80`。

**期望**：R4 明确 `confidence ≥ 0.80`，设备进入疑似跌倒。

**实际**：实现判断 `confidence > 0.80`，返回成功但 `fall_suspected=false`。

**自动化用例**：`test_confidence_boundary_and_recovery`。

## B3 — P2：告警 message 与需求不符

**复现**：按标准流程触发 bathroom 告警。

**期望**：`Possible fall detected in bathroom`。

**实际**：返回 `Possible fall detcted in bathroom`（detected 拼错）。

**自动化用例**：`test_happy_path_alert_ack_resolve_and_recurrence`。

## B4 — P1：未注册设备上报帧导致 500

**复现**：直接 POST `/devices/unknown/frames`。

**期望**：异常处理章节规定 404。

**实际**：直接索引 `devices[device_id]` 触发 KeyError，FastAPI 返回 500。

**自动化用例**：`test_missing_resources_and_unknown_device_frame`。
