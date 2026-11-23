# HomeGuard 跌倒检测告警服务 — 需求文档 v1.0

> 本文档为笔试专用的简化虚构场景，不代表任何真实产品的规格。

## 0. 阅读说明

**完成这道题不需要任何硬件、App 或后台账号。**

本仓库中的 `app.py` 就是被测服务，在你本地运行。真实产品中由雷达设备和照护人员 App 发出的请求，在这里都由你通过 HTTP 接口直接发送（第 2 节有对应关系）。所有业务规则以本文档为准。

## 1. 背景

毫米波雷达设备安装在老人家中的墙面上（浴室、卧室、客厅），不需要摄像头，也不需要老人佩戴任何设备，即可识别房间内人员的姿态。老人跌倒后能否被及时发现，直接关系到救治结果。

本服务是告警判定后端：接收雷达设备上报的姿态数据，判断是否发生跌倒，并决定何时触发、升级、关闭告警。

## 2. 系统组成与模拟方式

真实系统由三部分组成，本题中全部用接口调用来模拟：

| 真实世界 | 做什么 | 本题中如何模拟 |
|---|---|---|
| 雷达设备 | 安装时注册；之后持续上报房间内人员的姿态 | 调用 `POST /devices` 注册；调用 `POST /devices/{device_id}/frames` 上报姿态帧。**你可以自由构造任意姿态序列和时间戳** |
| 照护人员 App | 接收告警、确认告警、标记处理完成、开关照护模式 | 调用 `GET /devices/{device_id}/alerts`、`POST /alerts/{alert_id}/ack`、`POST /alerts/{alert_id}/resolve`、`POST /devices/{device_id}/care-mode` |
| 设备鸣响 / 推送 / 短信 | 告警的实际通知动作 | **本服务不真正发出通知**，只记录告警级别：`level=1` 表示已鸣响并推送 App，`level=2` 表示已短信通知紧急联系人 |

关于时间：服务中所有时长判断（30 秒确认、120 秒升级）都依据姿态帧中的 `timestamp`。因此你**不需要真的等待**，构造不同时间戳的帧即可模拟时间流逝。

## 3. 业务流程

1. 设备安装后向服务**注册**，绑定到一位老人和一个房间。
2. 设备持续上报**姿态帧**（站立、坐、床上躺卧、地面躺卧、跌倒动作等）。
3. 检测到跌倒动作后，系统进入**疑似跌倒**状态，观察老人是否倒地不起。
4. 确认跌倒后触发**一级告警**（设备本地鸣响 + 推送照护人员 App）。
5. 一级告警发出后 120 秒内未被确认，升级为**二级告警**（短信通知紧急联系人）。
6. 老人跌倒后如果能自行起身，系统自动取消告警。
7. 照护人员到场后**确认**告警，处理完毕后标记为**已处理**。
8. 照护人员在房间内协助老人时（如协助洗浴），可以开启**照护模式**，暂停监测以避免误报。

## 4. 业务规则

### 4.1 监测状态

| 规则 | 说明 |
|---|---|
| R1 监测条件 | 照护模式关闭时处于监测状态 |
| R2 默认状态 | 设备注册后默认照护模式关闭（即监测开启） |
| R3 照护模式 | 照护模式开启期间，姿态帧正常接收，但不做跌倒判定；开启时清除当前的疑似跌倒状态 |

### 4.2 跌倒判定

| 规则 | 说明 |
|---|---|
| R4 疑似跌倒 | 收到 `target=person`、`posture=fall` 且 `confidence ≥ 0.80` 的帧，进入疑似跌倒状态，并以该帧时间作为跌倒起始时间 |
| R5 宠物过滤 | `target=pet` 的帧不参与任何判定 |
| R6 确认跌倒 | 疑似跌倒状态下，收到 `posture=lying_floor` 的帧，且距跌倒起始时间 **≥ 30 秒**，确认跌倒并触发一级告警 |
| R7 自行恢复 | 告警触发前，若收到 `standing` / `sitting` / `lying_bed` 帧，视为自行恢复，取消疑似跌倒状态 |

### 4.3 告警生命周期

| 规则 | 说明 |
|---|---|
| R8 唯一性 | 同一设备同一时间最多存在一个未结束的告警（状态为 `open` 或 `acknowledged`） |
| R9 升级 | 一级告警发出 120 秒内未被确认，升级为二级告警 |
| R10 确认 | 照护人员确认后，告警状态由 `open` 变为 `acknowledged` |
| R11 处理完成 | 照护人员处理完毕后，告警状态变为 `resolved`（`open` 或 `acknowledged` 均可直接标记） |
| R12 再次跌倒 | 告警处理完成（`resolved`）后，老人如再次跌倒，应重新触发告警 |
| R13 告警文案 | `Possible fall detected in {room}` |

### 4.4 状态说明

跌倒判定（每台设备独立）：

```
  监测中 ──(fall 帧，置信度 ≥ 0.80)──▶ 疑似跌倒
    ▲                                   │
    │                                   ├─(standing / sitting / lying_bed)──▶ 回到监测中
    │                                   │
    └──────(触发一级告警)◀──(lying_floor 帧，距起始 ≥ 30 秒)
```

告警状态：

```
  open (level 1) ──(120 秒未确认)──▶ open (level 2)
     │                                  │
     ├──(确认)──▶ acknowledged ──(处理完成)──▶ resolved
     │                                  │
     └──────────────(处理完成)───────────┴──────────▶ resolved
```

## 5. 数据定义

### 5.1 枚举值

| 字段 | 取值 |
|---|---|
| `room` 房间 | `bathroom` 浴室、`bedroom` 卧室、`living_room` 客厅 |
| `posture` 姿态 | `standing` 站立、`sitting` 坐、`lying_bed` 床上躺卧、`lying_floor` 地面躺卧、`fall` 跌倒动作、`absent` 无人 |
| `target` 目标 | `person` 人、`pet` 宠物 |
| 告警 `status` | `open` 待处理、`acknowledged` 已确认、`resolved` 已处理 |
| 告警 `level` | `1` 一级（鸣响 + App 推送）、`2` 二级（短信通知紧急联系人） |

### 5.2 姿态帧（设备上报）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `target` | string | 是 | 识别出的目标类型 |
| `posture` | string | 是 | 姿态 |
| `confidence` | number | 是 | 识别置信度，范围 0–1 |
| `timestamp` | string | 是 | 设备本地时间，ISO 8601 格式，如 `2026-10-01T08:00:00` |

### 5.3 设备对象（接口返回）

| 字段 | 说明 |
|---|---|
| `device_id` | 设备编号 |
| `resident_id` | 绑定的老人编号 |
| `room` | 安装房间 |
| `firmware_version` | 固件版本 |
| `care_mode` | 照护模式是否开启 |
| `monitoring` | 当前是否处于监测状态 |
| `fall_suspected` | 当前是否处于疑似跌倒状态 |

### 5.4 告警对象（接口返回）

| 字段 | 说明 |
|---|---|
| `alert_id` | 告警编号 |
| `device_id` / `resident_id` / `room` | 来源设备、老人、房间 |
| `level` | 告警级别 |
| `status` | 告警状态 |
| `message` | 告警文案 |
| `created_at` | 告警触发时间 |
| `escalated_at` | 升级时间，未升级为 `null` |
| `acknowledged_at` | 确认时间，未确认为 `null` |
| `resolved_at` | 处理完成时间，未处理为 `null` |

## 6. 接口

服务启动后访问 http://127.0.0.1:8000/docs 可以查看完整的接口定义并**在浏览器中直接调试**，不需要命令行工具。

| 方法 | 路径 | 说明 | 成功状态码 |
|---|---|---|---|
| GET | `/health` | 健康检查 | 200 |
| POST | `/devices` | 注册设备 | 201 |
| GET | `/devices/{device_id}` | 查询设备状态 | 200 |
| POST | `/devices/{device_id}/care-mode` | 开启 / 关闭照护模式，请求体 `{"enabled": true}` | 200 |
| POST | `/devices/{device_id}/frames` | 上报一帧姿态数据 | 201 |
| GET | `/devices/{device_id}/alerts` | 查询告警列表，可用 `?status=open` 过滤 | 200 |
| POST | `/alerts/{alert_id}/ack` | 确认告警 | 200 |
| POST | `/alerts/{alert_id}/resolve` | 标记告警已处理 | 200 |
| POST | `/_test/reset` | 测试辅助：清空所有数据 | 200 |

上报姿态帧的返回体：

| 字段 | 说明 |
|---|---|
| `accepted` | 是否接收 |
| `monitoring` | 接收时设备是否处于监测状态 |
| `alert` | 如果这一帧触发了告警，返回告警对象；否则为 `null` |

## 7. 完整流程示例

下面是一次正常的"跌倒 → 告警 → 确认 → 处理完成"流程，可以照着在命令行或 `/docs` 页面中操作，熟悉服务的行为。

**① 注册设备**

```bash
curl -X POST http://127.0.0.1:8000/devices \
  -H "Content-Type: application/json" \
  -d '{"device_id":"hg-001","resident_id":"R1001","room":"bathroom","firmware_version":"1.4.2"}'
```

返回 201：

```json
{"device_id":"hg-001","resident_id":"R1001","room":"bathroom","firmware_version":"1.4.2",
 "care_mode":false,"monitoring":true,"fall_suspected":false}
```

**② 08:00:00 设备检测到跌倒动作**

```bash
curl -X POST http://127.0.0.1:8000/devices/hg-001/frames \
  -H "Content-Type: application/json" \
  -d '{"target":"person","posture":"fall","confidence":0.92,"timestamp":"2026-10-01T08:00:00"}'
```

返回 201，`{"accepted":true,"monitoring":true,"alert":null}`。此时查询 `GET /devices/hg-001`，`fall_suspected` 为 `true`。

**③ 08:00:10 老人仍躺在地上（未满 30 秒，不告警）**

```bash
curl -X POST http://127.0.0.1:8000/devices/hg-001/frames \
  -H "Content-Type: application/json" \
  -d '{"target":"person","posture":"lying_floor","confidence":0.95,"timestamp":"2026-10-01T08:00:10"}'
```

返回 201，`alert` 为 `null`。

**④ 08:00:30 老人仍躺在地上（满 30 秒，触发一级告警）**

```bash
curl -X POST http://127.0.0.1:8000/devices/hg-001/frames \
  -H "Content-Type: application/json" \
  -d '{"target":"person","posture":"lying_floor","confidence":0.95,"timestamp":"2026-10-01T08:00:30"}'
```

返回 201：

```json
{"accepted":true,"monitoring":true,
 "alert":{"alert_id":"<告警编号>","device_id":"hg-001","resident_id":"R1001","room":"bathroom",
          "level":1,"status":"open","message":"Possible fall detected in bathroom",
          "created_at":"2026-10-01T08:00:30","escalated_at":null,
          "acknowledged_at":null,"resolved_at":null}}
```

**⑤ 照护人员确认告警**

```bash
curl -X POST http://127.0.0.1:8000/alerts/<告警编号>/ack
```

返回 200，`status` 变为 `acknowledged`，`acknowledged_at` 有值。

**⑥ 照护人员处理完毕**

```bash
curl -X POST http://127.0.0.1:8000/alerts/<告警编号>/resolve
```

返回 200，`status` 变为 `resolved`，`resolved_at` 有值。

> Windows 命令行中单引号可能不生效，建议使用 `/docs` 页面、Postman，或直接在测试代码中调用。

## 8. 异常处理

| 场景 | 期望 |
|---|---|
| 设备重复注册 | 返回 409 |
| 未注册设备上报数据 | 返回 404 |
| 设备不存在 | 返回 404 |
| 告警不存在 | 返回 404 |
| 参数不合法（房间、姿态不存在，置信度超出 0–1，缺少必填字段等） | 返回 422 |
| 确认一个非 `open` 状态的告警 | 返回 409 |
| 处理一个已 `resolved` 的告警 | 返回 409 |

## 9. 非功能说明

- 数据保存在内存中，服务重启后清空；也可以调用 `POST /_test/reset` 随时清空。
- 本版本不涉及鉴权。
- 本题只关注功能与业务逻辑，不需要做性能测试。
