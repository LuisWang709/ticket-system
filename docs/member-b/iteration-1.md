# 成员 B：第一版——订单与模拟支付基础

这是一个真实的第一阶段实现，尚未完成完整购票流程。本说明用于理解、演示和安排后续工作；不是老师要求的个人 ADR 或个人评价。

## 这一版做到了什么

| 功能 | 当前行为 |
| --- | --- |
| 创建订单 `POST /orders` | 保存订单后调用 A 的 `POST /reservations`；预留成功为 `PENDING`，库存拒绝为 `REJECTED` |
| 查询订单 `GET /orders/{id}` | 从 PostgreSQL 查询，重启程序后仍保留 |
| 重复请求 | 同一个 UUID 格式的 `Idempotency-Key` 和相同内容返回同一订单；改内容返回 409 |
| 库存暂时不可用 | 返回 503，保留 `NEW` 和相同订单 ID；恢复后可用相同 key 和内容重试 |
| 模拟支付 `POST /payments` | 保存 `SUCCEEDED` 或 `FAILED`，不接真实支付渠道；同一订单重复请求返回原结果 |
| 支付设置 `GET/POST /admin/config` | `delay_ms` 控制延迟，`fail_rate` 为 0 时成功、为 1 时失败 |
| 健康和指标 | 两个服务都有 `/health`、`/metrics`，以及订单或支付业务计数 |

订单初始状态 `NEW` 表示正在处理或等待重新尝试；`PENDING` 表示曾成功预留库存。本版的 `PENDING` **不会自动随库存到期而改变**，不能当作始终持有有效门票的保证。

当前流程：

```text
POST /orders → 保存 NEW → 调用 A 的库存接口 → PENDING / REJECTED
GET /orders/{id} → 查询已保存的订单

POST /payments → 单独模拟支付 → SUCCEEDED / FAILED
```

本版没有连接这两条流程。即使支付接口返回成功，订单也仍为 `PENDING`，库存也没有确认售出。

## 文件怎么找

- `services/order/app/main.py`：订单接口、数据表、库存调用和重复请求处理。
- `services/payment/app/main.py`：模拟支付、支付记录、延迟和失败率设置。
- `pytest.ini`：让 `pytest` 和 `python -m pytest` 行为一致（把仓库根目录放进导入路径）。
- 各服务的 `Dockerfile`、`requirements.txt`：打包环境和依赖。
- `compose.member-b.yaml`：独立启动本版演示，使用现有 A 库存代码及新的独立数据库卷。
- `tests/member_b/test_v1.py`：真实 PostgreSQL 测试、可控库存响应测试、真实库存联调测试。
- `docs/ai-log/Dingyao_Xue.md`：本部分的 AI 使用记录。

## 启动（在本项目文件夹的终端中）

先打开 Docker Desktop，等它启动完成。随后运行：

```bash
docker compose -p ticket-b-v1 -f compose.member-b.yaml up -d --build --wait
```

这份配置可以单独运行，无需同时指定小组的 `docker-compose.yaml`。它创建独立的 `ticket-b-v1` 容器和数据卷；不需要在本机安装 Python 或 PostgreSQL。

| 页面 | 地址 |
| --- | --- |
| 订单接口文档 | http://localhost:18003/docs |
| 模拟支付接口文档 | http://localhost:18004/docs |
| A 的库存接口文档 | http://localhost:18002/docs |

本机演示端口使用 18002/18003/18004，避免与旧项目的 8002/8003/8004 冲突。容器内端口仍是 8000，订单使用 `http://inventory:8000`。之后组长可在主 Compose 中按团队要求接入 8003/8004。

查看运行状态：

```bash
docker compose -p ticket-b-v1 -f compose.member-b.yaml ps
```

## 按顺序手动演示

1. 打开订单文档，展开 `POST /orders`，点击 **Try it out**。
2. 在 `idempotency-key` 输入一个新的 UUID。首次可用 `cf789263-4d38-4fca-ab2f-2f65a2bc3c6d`；之后每次新订单用新的 UUID。Mac 终端的 `uuidgen` 可以生成。
3. 请求内容输入下方 JSON，点击 **Execute**。

```json
{
  "user_id": "student-demo",
  "tier_id": "1",
  "qty": 1,
  "amount_cents": 25000
}
```

`tier_id` 是字符串，所以 `"1"` 带引号。金额单位是分，25000 表示 250.00。风控字段可暂时不填：本版接受这些字段但还没有执行风控。

4. 正常应返回 HTTP 200，内容包含 `order_id`、`status: PENDING`、`reason: null` 和 `reservation_id`。记下 `order_id`。
5. 内容和 key 都不改，再点击 Execute；应返回相同订单，不再预留一次库存。
6. 展开 `GET /orders/{order_id}`，输入刚才的 ID，查询应返回相同状态。
7. 打开支付文档，在 `POST /payments` 中使用刚才的订单 ID：

```json
{
  "order_id": "把这里替换为刚才返回的订单ID",
  "amount_cents": 25000
}
```

8. 默认返回 `{"status":"SUCCEEDED"}`。回到订单页面查询，它依然是 `PENDING`，这是本版明确的功能边界。
9. 要演示失败，先调用支付 `POST /admin/config`，输入 `{"fail_rate":1,"delay_ms":0}`。再用**新的订单 ID**调用支付，会得到 `FAILED`。旧 ID 会返回之前保存的结果，不会重新随机。
10. 演示后用 `{"fail_rate":0,"delay_ms":0}` 恢复默认设置。

其他验证：不填 Idempotency-Key 返回 422；同 key 改数量返回 409；用新 key 请求极大数量（如 1000000）会得到状态 `REJECTED`；查询不存在的订单返回 404。

## 自动测试

在项目目录运行：

```bash
docker compose -p ticket-b-v1 -f compose.member-b.yaml --profile test run --build --rm tests
```

测试会新建一个唯一命名的 PostgreSQL 测试数据库，结束时删除该测试库。真实接口联调会在独立演示库中新增一张订单、一条模拟支付记录并预留一张票。反复测试会消耗演示库存；本版没有自动清扫，不能无限重复而不补充演示数据。

测试覆盖：

- 创建、查询、重复创建、内容冲突、缺少 header、非法数量、未知订单。
- 库存拒绝、超时后保留订单 ID、异常响应不误判为成功。
- 同一 key 的并发请求只调用一次库存。
- 支付成功/失败、金额冲突、重复结果、配置校验、并发重复支付。
- 重建应用后仍可读取 PostgreSQL 中的订单和支付结果。
- 真实订单容器调用 A 的库存服务；独立支付成功或失败都不会在本版自动更新订单。

## 尚未实现（后续真实迭代）

1. 第二版：订单自动调用支付、确认库存，成功转为 `CONFIRMED`；失败补偿释放库存；支付支持 void/refund。
2. 第三版：超时取消、失败重试、Outbox 与 RabbitMQ 事件、通知和断路器，补充故障场景测试。
3. 团队整合：接 C 的风控和规则回退、接 D 的 Toxiproxy 与主 Compose、完善仪表盘和端到端测试。

其中每一步的最终范围由小组确认。

其他已知限制：

- 当前没有 Outbox、通知、风险判断或认证；支付管理设置只保存在当前单进程内存，重启恢复环境变量初值。
- A 的库存实现存在并发/事务与消息可靠性方面的待验证问题。本版 B 的去重测试不能代替 A 的库存验收。
- 库存超时后原请求可能已经成功；本版只保留 NEW 并允许相同 key 重试，没有后台恢复、查询预留或自动补偿机制。
- 预留结果只检查返回格式，不验证活动票价。演示请求的金额由调用者提供。
- 测试运行结果及局限见 `verification.md`。目前仅作为课程第一阶段成果，不能宣称整套项目完成。

## 停止演示

```bash
docker compose -p ticket-b-v1 -f compose.member-b.yaml down
```

这会停止本版容器，保留本版数据库卷和记录。
