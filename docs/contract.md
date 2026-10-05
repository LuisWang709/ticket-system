# 团队契约 (Team Contract)

版本:v1 · 维护人:成员 D · 最后更新:2026-10-05

这份文档是四个人并行开发的约定。**谁要改契约,先改这份文档,在群里通知全组,再改代码。** 全组确认后,在下面"确认记录"里签名。

## 1. 目录归属

| 路径 | 负责人 | 说明 |
| --- | --- | --- |
| `services/inventory/` | A | 库存服务:三种扣库存方式、清扫任务 |
| `scripts/reconcile.sh` | A | 对账脚本(D 负责接入 CI) |
| `services/order/` | B | 订单服务、状态机、saga、outbox 发布、`resilience.py` |
| `services/payment/` | B | 模拟支付 |
| `services/risk/` | C | AI 风控 |
| `services/notification/` | C | 通知 |
| `services/catalog/` | D | 活动目录 |
| `docker-compose.yml`、`gateway/`、`prometheus/`、`grafana/`、`toxiproxy/`、`rabbitmq/`、`loadtest/`、`stubs/`、`.github/`、`scripts/reset.sh`、`scripts/bench_modes.sh`、`scripts/chaos.sh`、`docs/runbook.md`、`docs/contract.md` | D | 基础设施与整合 |
| `docs/adr/` | 各 ADR 负责人 | 一个 ADR 一个文件 |
| `docs/ai-log/` | 每个人 | 每人一个文件 `A.md` `B.md` `C.md` `D.md`,交作业前合并 |

### 共享文件(两份必须完全一样,CI 会检查)

| 文件 | 以谁的为准 | 谁复制 |
| --- | --- | --- |
| `services/inventory/outbox.py` 与 `services/order/outbox.py` | B | A |
| `services/risk/rules.py` 与 `services/order/rules.py` | C | B |

## 2. 端口、服务名与数据库

服务之间互相访问一律用"服务名:容器内端口",例如 `http://inventory:8000`。

| 服务 | 容器内端口 | 对本机开放 | 数据库 |
| --- | --- | --- | --- |
| catalog | 8000 | 8001 | `catalog` |
| inventory | 8000 | 8002 | `inventory`(另用 Redis) |
| order | 8000 | 8003 | `orders` |
| payment | 8000 | 8004 | `payments` |
| risk | 8000 | 8005 | 无(模型文件) |
| notification | 8000 | 8006 | `notifications` |
| gateway (Nginx) | 80 | 8080 | 无 |
| Grafana / Prometheus | 3000 / 9090 | 3000 / 9090 | 无 |
| RabbitMQ 管理界面 / 指标 | 15672 / 15692 | 15672 / 15692 | 无 |
| Toxiproxy | 8474 | 8474 | 无 |
| Redis | 6379 | 不对外 | 无 |

Toxiproxy 代理:`toxiproxy:9001` → `payment:8000`,`toxiproxy:9002` → `risk:8000`。

## 3. 环境变量

| 服务 | 变量 | 默认值 / 含义 |
| --- | --- | --- |
| 所有用数据库的服务 | `DATABASE_URL` | `postgresql://postgres:postgres@postgres:5432/<库名>` |
| inventory、order、notification | `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` |
| inventory | `HOLD_SECONDS` | 预留有效秒数。演示 30,真实 600 |
| inventory | `INVENTORY_MODE` | `atomic` / `rowlock` / `redis`,默认 `atomic` |
| inventory | `REDIS_URL` | `redis://redis:6379/0` |
| order | `INVENTORY_URL` | 默认 `http://inventory:8000` |
| order | `PAYMENT_URL` | 默认 `http://toxiproxy:9001` |
| order | `RISK_URL` | 默认 `http://toxiproxy:9002` |
| payment | `DELAY_MS`、`FAIL_RATE` | 启动初值,运行中可用 `/admin/config` 改 |
| risk | `MODEL_VERSION` | `v1-logreg` |
| risk | `TOKEN_BUDGET_PER_MIN` | `60000` |
| risk | `DELAY_MS` | 演示用,让 AI 变慢,默认 0 |

## 4. HTTP 接口

所有服务都有 `GET /health` 和 `GET /metrics`。下面只列业务接口。请求体里没写的字段都有默认值。

### catalog

| 接口 | 响应 |
| --- | --- |
| `GET /events` | 活动列表 |
| `GET /events/{id}` | 活动 + 票档;不存在返回 404 |

### order

| 接口 | 请求 | 成功响应 | 其他状态码 |
| --- | --- | --- | --- |
| `POST /orders`(请求头必须有 `Idempotency-Key`) | `user_id`、`tier_id`、`qty`、`amount_cents`、`clicks_last_1s`、`requests_last_10s`、`account_age_days`、`ms_page_to_click`、`accounts_on_device` | 200:`order_id`、`status`、`reason`、`reservation_id` | 422 请求头缺失 |
| `GET /orders/{id}` | 无 | 同上 | 无 |

订单状态:`NEW` → `PENDING` → `CONFIRMED` / `CANCELLED`;另有 `REJECTED`(风控拦截或售罄)。终态不可再变。

### inventory

| 接口 | 请求 | 成功响应 | 其他状态码 |
| --- | --- | --- | --- |
| `POST /reservations` | `order_id`、`tier_id`、`qty` | 200:`reservation_id`、`status` | 409 售罄 |
| `POST /reservations/{id}/confirm` | 无 | 200:`status=CONFIRMED` | 409 已过期或已释放;404 |
| `POST /reservations/{id}/release` | 无 | 200:`status` | 404 |
| `GET /stock` | 无 | 每个票档的 `total`、`reserved`、`sold`、`available` | 无 |
| `GET /admin/mode` | 无 | `mode`、`modes` | 无 |
| `POST /admin/mode` | `mode` | 当前方式 | 422 不认识的方式;503 Redis 不可达 |
| `POST /admin/reset` | `total`(1 号票档总量) | 清空预留与发件箱,库存归零重来 | 无 |
| `GET /admin/redis-check` | 无 | 每个票档的 `database_available`、`redis_counter`、`in_sync` | 无 |

预留与确认对同一 `order_id` 幂等。

### payment

| 接口 | 请求 | 成功响应 |
| --- | --- | --- |
| `POST /payments` | `order_id`、`amount_cents` | 200:`status` = `SUCCEEDED` 或 `FAILED` |
| `POST /payments/{order_id}/void` | 无 | `status=VOIDED` |
| `POST /payments/{order_id}/refund` | 无 | `status` = `REFUNDED`(扣款已成功)或 `VOIDED`(扣款未到达) |
| `GET /admin/config`、`POST /admin/config` | `delay_ms`、`fail_rate` | 当前配置 |

### risk

| 接口 | 请求 | 成功响应 | 其他状态码 |
| --- | --- | --- | --- |
| `POST /score` | `user_id`、`clicks_last_1s`、`requests_last_10s`、`account_age_days`、`ms_page_to_click`、`accounts_on_device` | 200:`score`、`decision`(`allow`/`deny`)、`model_version`、`source`(`ai`/`rules`) | 429 成本预算用完 |
| `GET /admin/delay`、`POST /admin/delay` | `ms` | 当前延迟 | 无 |

硬规则:`clicks_last_1s >= 10` 直接判定为机器人(分数 1.0)。阈值:分数 ≥ 0.8 为 `deny`。

### notification

| 接口 | 响应 |
| --- | --- |
| `GET /notifications` | 最近 50 条通知 |

## 5. 事件(RabbitMQ)

- 交换机:`events`,类型 `topic`,持久化
- 消息体为 JSON;消息属性 `message_id` = `"<服务名>-<发件箱编号>"`,例如 `order-17`
- 投递语义:至少一次。**所有消费者必须幂等。**

| 路由键 | 发布者 | 内容 | 订阅者(队列名) |
| --- | --- | --- | --- |
| `order.confirmed`、`order.cancelled`、`order.rejected` | order | `order_id`、`user_id`、`status` | notification(`notification.orders`,绑定 `order.*`) |
| `reservation.expired` | inventory | `order_id`、`reservation_id` | order(`order.reservation_expired`) |

## 6. 指标

D 的仪表盘依赖这些名字和标签,**不能随便改**。

| 指标 | 标签 | 类型 | 提供者 |
| --- | --- | --- | --- |
| `http_requests_total`、`http_request_duration_highr_seconds` | 自动生成 | Counter、Histogram | 所有服务(`Instrumentator`) |
| `inventory_reservations_total` | `result`(ok、sold_out)、`mode` | Counter | A |
| `inventory_reserve_seconds` | `mode` | Histogram | A |
| `inventory_active_reservations` | 无 | Gauge | A |
| `inventory_redis_fallback_total` | 无 | Counter | A |
| `orders_total` | `status` | Counter | B |
| `circuit_breaker_state` | `target`(0 合闸、1 半开、2 跳闸) | Gauge | B |
| `outbox_pending` | 无 | Gauge | A、B(`outbox.py`) |
| `risk_fallback_total` | 无 | Counter | B |
| `payments_total` | `result` | Counter | B |
| `risk_inferences_total` | `decision`、`model_version` | Counter | C |
| `risk_simulated_tokens_total` | 无 | Counter | C |
| `notification_messages_total` | `result`(ok、duplicate) | Counter | C |
| `rabbitmq_queue_messages_ready` | 无 | Gauge | RabbitMQ 自带插件 |

## 7. Git 流程

| 约定 | 做法 |
| --- | --- |
| 分支 | `main` 永远保持"一条命令能启动"。每人在自己的分支工作,如 `feat/inventory-modes` |
| 合并 | 用 Pull Request 合并到 `main`,至少一位队友看过。不要直接 push 到 `main` |
| CI | 红了先不合并 |
| 提交 | 小步多次,信息写清楚,如 `inventory: add rowlock mode` |
| 本地开发 | 只启动自己需要的服务:`docker compose up -d --build <服务名>`;别人没好就用 `stubs/` 里的桩 |
| 改契约 | 先改本文件,群里通知,再改代码 |

## 8. 确认记录

| 成员 | 确认日期 |
| --- | --- |
| A | |
| B | |
| C | |
| D | |
