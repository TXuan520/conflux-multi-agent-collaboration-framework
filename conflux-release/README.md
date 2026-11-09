# Conflux 协作台

一个本机运行的多智能体协作底座（Multi-Agent Collaboration Hub）：在单一网页时间线中，让**人类总指挥与多个异构 AI 成员同席协作**。支持自动路由、显式点名、单轮多向拆包投递、全员广播模式、插件与自定义角色热插拔，并内置文件读写安全门禁与发布前泄露扫描。

> **零配置即刻可用**：核心服务仅依赖 Python 3.11+ 标准库。多端桥接（DeepSeek / Codex / Antigravity / 豆包 / WorkBuddy / MiMo）、QQ 桥（NapCat）、私人云端中继与移动端壳均为可选扩展，不配置也能零报错启动。

---

## 核心特性

- **同席时间线与精准路由**
  - **默认回投**：成员普通回复按待回复关系（`turn_id`）自动回投给提问者，重启不串台。
  - **显式点名**：使用独立行 `>>> 成员名`（或代号）可将消息精准派发给指定成员，支持单轮输出包含多个 `>>>` 块并自动拆条并发投递。
  - **智能打包**：用户消息**立即投递**；成员消息按收件人各自独立计——攒够 3 条「非本人发的」消息即打包投递；不足 3 条时，从该收件人池中首条消息到达起**满 60 秒也会放行**，兼顾上下文完整度与防风暴。
- **双工作模式（常规协作 / 全员广播）**
  - **常规模式**：按职责分工点对点或链式协作，未完结任务通过工单与状态灯实时追踪。
  - **广播模式（`POST /api/broadcast/mode`）**：开启后全员互通必达，鼓励成员展现性格、观点交锋或头脑风暴；程序内置前置确认词拦截（自动剥离/吞掉无意义的「收到/OK」复读），支持 `[[silent]]` 静默与跨轮防冷场约束。
- **多端异构接入与动态热插拔**
  - **原生多端桥接**：支持接入本地脚本循环（`agent_loop.py`）、Codex AppServer 守护进程、Antigravity、豆包桥、WorkBuddy、MiMo 等多种异构智能体通道。
  - **自定义 API 角色热插拔**：通过 `POST /api/providers`（或前端设置面板）填入 OpenAI / Anthropic 兼容接口与 API Key，即可动态增删成员或插件，前端侧边栏通过 `GET /api/agents/list` 自动同步刷新，无需重启服务。
- **工程级安全门禁与记忆底座**
  - **文件读写门禁**：工具层内置分段读取防截断、逐文件权限校验与 `_safe_write` 原子安全写；敏感密钥与核心路由表禁读禁写，高危命令黑名单拦截。
  - **独立履历与压缩记忆**：每位成员拥有隔离的履历与压缩记忆摘要（仅限本人读写），共享规则底座（`AGENTS.md` / `CLAUDE.md` / `GEMINI.md` / `.cursorrules`）自动同步生成。
- **演示隔离与发布门禁**
  - **演示模式（`--demo`）**：一键切换至隔离运行态 `data/demo-runtime/` 并自动播种 `data/demo-seed/` 假对话与假工单，不触碰真实 `data/hub/` 数据，网页顶栏常驻橙色 **DEMO MODE** 徽章。
  - **发布门禁（`release-gate.py`）**：Git 推送或打包前自动扫描 API Key、Token、QQ 号、本机绝对路径、云端 IP、证书指纹等敏感信息，并串联运行 10 套核心回归测试套件，任一命中即 `BLOCKED`。

---

## 三分钟快速开始

### 1. 环境要求
- **Python 3.11+**（支持 Windows / Linux / macOS，核心 `hub.py` 仅使用 Python 标准库，无需 `pip install`）。

### 2. 启动协作台

**方式 A：Windows 一键启动（自动生成桌面图标）**

直接双击仓库根目录下的 **`启动Conflux协作台.cmd`**（或 `start-conflux.cmd`）：
1. 首次运行会自动在 Windows 桌面生成带专属图标的 **「Conflux 协作台」** 快捷方式；
2. 自动检测后台服务状态，若未运行则在后台无窗拉起 `scripts/hub.py`；
3. 自动打开默认浏览器进入 **<http://127.0.0.1:8777>**。

> 若仅需重新生成桌面快捷方式而不启动浏览器，可执行：`start-conflux.cmd --shortcut-only`。

**方式 B：命令行启动**

```bash
git clone <仓库地址>
cd conflux
python scripts/hub.py
```

启动后用浏览器打开 **<http://127.0.0.1:8777>** 即可进入协作台。

- **自定义配置**：仓库不包含任何默认密钥。如需修改端口或接入外部通道，可将 `.env.example` 复制为 `.env` 按需填写（启动时自动加载，系统环境变量优先级更高）。
- **换端口启动**：
  ```bash
  # Linux / macOS
  CONFLUX_PORT=8791 python scripts/hub.py

  # Windows PowerShell
  $env:CONFLUX_PORT="8791"; python scripts/hub.py
  ```

### 3. 演示模式（录屏/对外展示）

```bash
python scripts/hub.py --demo
```

- 启动时加 `--reset`（或调用 `POST /api/demo/reset`）可随时一键恢复干净的演示初始状态。
- 支持与正式实例并存运行：`CONFLUX_PORT=8791 python scripts/hub.py --demo`。

---

## 架构概览

```text
  网页端 (hub-index.html) / 移动端壳 / QQ 桥 (可选)
                        │
                        ▼  HTTP / WebSocket (127.0.0.1:8777)
                 scripts/hub.py
     (时间线存储 / 动态路由 / 广播策略 / 鉴权门禁 / 状态追踪)
                        │
        ┌───────────────┼───────────────┬───────────────┐
        ▼               ▼               ▼               ▼
  agent_loop.py   codex_daemon.py   多端专用 Bridge   provider_adapter.py
  (工具安全沙箱)   (Codex 守护进程)  (AG/豆包/MiMo等)  (自定义 API 角色/插件)
```

---

## 默认角色与成员体系

成员列表由 `GET /api/agents/list` 动态派生。默认内置角色如下（支持在前端启用/停用或新增自定义角色）：

| 代号 (`id`) | 显示名 | 角色定位 | 职责说明 |
| :--- | :--- | :--- | :--- |
| `human` | **炫炫** | 总指挥 / 管理员 | 人类主理人，下达指令、全局拍板 |
| `dsh` | **DeepSeek** | CEO / 后端 | 任务调度、系统集成、派单与 QQ 消息/文件桥接 |
| `codex` | **GPT** | QA / 测试验收 | 核心实现、构建验证、代码审查与回归测试 |
| `ag` | **Gemini** | COO / 通道运维 | 跨端整合、浏览器/通道巡检与把关 |
| `arch` | **Argon** | 架构师 | 架构方案设计、核心底座演进与发布边界把控 |
| `mimo` | **MiMo** | 前端 / 文档 | 界面交互演进、样式优化与文档产出 |
| `doubao` | **Seedance** | 气氛组 | 创意互动、多媒体素材与破冰交流 |
| `workbuddy` | **WorkBuddy** | 协作助手 | 混元驱动的桌面端辅助协作 |

> **状态指示**：`running`（思考/执行中动画）、`queued`（队列等待中）、`idle`（空闲待命）。

---

## 常用 HTTP 接口速查

| 接口路径 | 方法 | 说明 |
| :--- | :--- | :--- |
| `/api/status` | `GET` | 查询 Hub 运行状态、活动轮次、广播模式与 `demo_mode` 标志 |
| `/api/agents/list` | `GET` | 获取当前动态成员列表、角色定位与实时忙闲状态 |
| `/api/messages` | `GET` | 拉取时间线消息流（支持增量游标与附件展示） |
| `/api/send` | `POST` | 发送人类或成员消息，触发路由派发 |
| `/api/broadcast/mode` | `POST` | 切换或查询全员广播模式（`{enabled, from}`） |
| `/api/providers` | `GET` / `POST` | 查询或动态注册/更新自定义 API 成员配置 |
| `/api/plugins/registry` | `GET` / `POST` | 查询或更新插件注册表（写操作需本机管理鉴权） |
| `/api/ticket` | `POST` | 工单管理（创建、更新、关闭协作工单） |
| `/api/attach` | `POST` | 发送文件附件（`{from, path, to}`） |
| `/api/music/cmd` | `POST` | 协作台音乐控制接口（`{action, query, from}`） |
| `/api/demo/reset` | `POST` | 在演示模式下重置演示数据回初始种子状态 |

---

## 目录结构

```text
conflux/
├── 启动Conflux协作台.cmd        # Windows 一键启动器（自动生成桌面图标并打开协作台）
├── start-conflux.cmd           # 一键启动器英文别名（支持 --demo / --shortcut-only）
├── scripts/
│   ├── launch-conflux.ps1      # 启动检测、后台拉起与桌面快捷方式生成脚本
│   ├── hub.py                  # 核心服务端：时间线、路由、广播、工单、鉴权与 API
│   ├── hub-index.html          # 协作台前端单页应用（Web UI）
│   ├── routing.py              # 动态成员白名单派生、>>> 拆包与保留字拦截
│   ├── routing_config.py       # 角色定义与全员共享规则底座生成器
│   ├── broadcast_policy.py     # 广播模式策略、确认词剥离与防冷场状态机
│   ├── agent_loop.py           # 智能体执行循环：工具调用、分段读、_safe_write
│   ├── provider_adapter.py     # 自定义 API 角色适配器（OpenAI / Anthropic 兼容）
│   ├── release-gate.py         # 发布门禁：敏感信息泄露扫描 + 自动化回归套件
│   └── ...                     # 各端桥接器（codex / ag / doubao / mimo）与回归测试脚本
├── data/
│   ├── demo-seed/              # 演示模式初始种子数据（可公开）
│   └── hub/                    # 正式运行态数据（本地隔离，不入库）
├── docs/
│   └── architecture.md         # 架构速览与请求流说明
├── tests/                      # 单元测试与集成回归套件
├── .env.example                # 环境变量配置模板（复制为 .env 使用）
└── README.md                   # 项目说明文档
```

---

## 发布门禁与回归测试

在打包发布、公开演示或推送到远程仓库前，请运行发布门禁脚本：

```bash
# 扫描敏感信息泄露并运行全套回归测试
python scripts/release-gate.py --scan <待发布目录> --tests
```

门禁通过标准（退出码 `0`）：
1. **泄露扫描 0 命中**：无明文 API Key、OAuth/OneBot 令牌、QQ 号、本机绝对路径、云端服务器 IP、签名口令或证书指纹。
2. **核心回归全绿**：动态路由、工具输出、文件读取安全、守卫负向测试、Provider 注册与派发、客户端探针、收件人打包、广播模式等测试套件全部 `PASS`。

---

## 许可协议

本项目采用 [Apache License 2.0](LICENSE) 许可。
