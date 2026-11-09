# Conflux 架构速览

面向发布口径的一页说明：桌面多智能体协作底座怎么跑、边界在哪。运维细节见仓库根目录 `QQBridgeOps.md`。

## 组成

```
网页单页 (scripts/hub-index.html)
        │  HTTP 127.0.0.1:<CONFLUX_PORT>
        ▼
hub.py  ── 时间线 /api/messages、发送 /api/send、状态 /api/status
   │       成员列表 /api/agents/list、注册 /api/providers
   │       演示重置 /api/demo/reset、门禁 release-gate.py
   │
   ├── 数据目录 data/hub/（消息、工单、额度、活动心跳）
   ├── 可选：QQ 桥（NapCat 反向 WS，令牌走 CONFLUX_NAPCAT_TOKEN）
   └── 可选：云端中继（出站外连，CONFLUX_RELAY_HOST）
```

## 关键约定

- **零配置启动**：NapCat、中继、四端客户端全部是可选扩展，环境变量缺失只降级不退出（`confluxconfig.optional_*`）。
- **环境变量优先**：仓库根 `.env`（模板 `.env.example`）启动时读取，只补没设过的变量。
- **演示隔离**：`--demo` / `CONFLUX_DEMO=1` 把运行态整体切到 `data/demo-runtime/`，种子来自 `data/demo-seed/`，真实 `data/hub/` 不读不写；`--reset` 或 `POST /api/demo/reset` 恢复干净初态。
- **动态成员**：成员与角色一律以 `GET /api/agents/list` 为准，前端不写死名单，新注册角色自动出现在侧边栏。
- **发布门禁**：`python scripts/release-gate.py --scan <目录> --tests` 扫密钥、QQ 号、本机路径、云端地址、证书指纹，命中即 BLOCKED，可连跑回归。

## 请求流（一条消息）

1. 网页 `POST /api/send` 或 QQ 桥入站 → hub 落盘 `messages.jsonl`。
2. 路由按点名/角色决定派给哪个成员循环（Codex / DSH / 自定义 API 角色）。
3. 成员回包再进同一条时间线，前端按 `GET /api/agents/list` 的状态字段渲染忙碌/排队/空闲。
4. 涉及文件读写的动作过安全门禁，发布前过泄露扫描。
