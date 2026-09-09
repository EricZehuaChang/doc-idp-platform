# Doc IDP Platform

<p align="center">
  <a href="./README.md">English</a> · <a href="./README.zh-CN.md"><b>简体中文</b></a>
</p>

> LLM 驱动的智能文档处理（IDP）平台：**样本即模型** —— 用 1–2 份样本 + 自然语言规则，就能上线一类文档的结构化抽取技能。

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)](https://vuejs.org/)
[![CI](https://github.com/EricZehuaChang/doc-idp-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/EricZehuaChang/doc-idp-platform/actions/workflows/ci.yml)

## 目录

- [概述](#概述)
- [核心能力](#核心能力)
- [架构](#架构)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置](#配置)
- [测试](#测试)
- [项目结构](#项目结构)
- [API](#api)
- [数据库迁移](#数据库迁移)
- [文档](#文档)
- [开发与 CI](#开发与-ci)
- [许可证](#许可证)

## 概述

Doc IDP Platform 是一个私有化可交付的智能文档处理平台，覆盖「上传 → 解析 → 抽取 → 校验 → 人审 → 确认」完整工作流，并对接认证、多租户、计费、通知等企业级边界。

核心设计理念：

- **样本即模型**：`SkillPackage` DSL 把「字段规则 + 少量样本」编译为抽取提示词，无需训练、无需标注平台。
- **解析 / 抽取两段式**：解析器产出统一文档中间表示（UDR），抽取管线在其上运行，互不耦合。
- **置信度 0–3 分级**：不采信模型自报的置信度，由平台根据证据独立判定，驱动人审与打码决策。
- **租户一等公民**：`tenant_id` 贯穿全表，standard 档在 PostgreSQL 上启用行级安全（RLS）。
- **影子计费**：append-only 账本从第一天记账，支撑云市场按量结算。

## 核心能力

| 域 | 能力 |
|---|---|
| 文档解析 | markitdown（Office）、pdfplumber（电子 PDF）、glm-ocr-cloud、ofd、opendataloader、多模态 vlm-*，按文件类型自动路由 |
| 技能引擎 | `SkillPackage` 版本管理、模板库、提示词编译、`probe` / `draft-*` 试抽 |
| 结构化抽取 | 规则 / 一致性双通道校验，`$value/$confidence/$bbox/$pages/...` 结果契约 |
| 视觉检测 | 印章 / 签名检测（PP-DocLayoutV3 + 红章 HSV 兜底 + YOLOS），`/detect` 纯计算零落库 |
| 审单台 | 双屏校验、bbox 高亮、锁定 / 分配 / 修正 / 确认 / 驳回闭环 |
| 多租户 | 应用层中间件 + PostgreSQL RLS 双重隔离 |
| 认证 | JWT 会话、API Key、OIDC、邀请 / 激活 / 找回密码 |
| 计费 | 影子 / 实扣双模、预冻结-实扣、套餐 entitlements、独立额度 |
| 数据 | 数据柜（cabinet）、用量统计、CSV 导出 |
| 集成 | Webhook、SMTP 邮件通知 |

## 架构

模块化单体 + 可选 Celery worker，按部署档位切换基础设施：

| 档位 | 数据库 | 队列 | 适用 |
|---|---|---|---|
| `lite` | SQLite | 进程内 runner | 开发、演示、单机交付 |
| `standard` | PostgreSQL 16（RLS） | Redis 7 + Celery | 多租户生产 |
| `air_gapped` | 同上（离线） | 同上 | 内网 / 无外网交付 |

依赖方向（强制，由 `tests/test_architecture.py` 用 AST 检查保障）：

```text
parsers ──► skillengine ──► extraction/pipeline(纯函数) ──► tasks/runner ──► api/routes
 (UDR 协议)   (SkillPackage DSL)      (UDR + Skill → result)     (幂等三阶段)     (FastAPI)
```

架构原则、自研 / 外购边界、禁做清单与受保护资产详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 技术栈

- **后端**：Python 3.11+、FastAPI、SQLAlchemy 2（async）、Pydantic v2、Alembic、Celery + Redis、aiosqlite / asyncpg
- **前端**：Vue 3、TypeScript、Vite、Vue Router、TanStack Query、pdf.js
- **基础设施**：PostgreSQL 16、Redis 7、Docker Compose（开发）

## 快速开始

### 前置要求

- Python **3.11+**
- Node.js **18+**（前端构建）
- Java **11+**（opendataloader 解析器需要；缺失时自动降级为 pdfplumber）
- Docker（可选，standard 档开发基础设施）

### 安装与启动后端

```bash
git clone https://github.com/EricZehuaChang/doc-idp-platform.git
cd doc-idp-platform

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 基础 + 解析器；如需 PostgreSQL / 视觉检测再补 pg、vision extra
pip install -e ".[dev,parsers]"      # 完整: pip install -e ".[dev,parsers,pg,vision]"

cp .env.example .env                 # 按需填入模型 Key（见 [配置](#配置)）
python run_dev.py                    # 注入机器级密钥并启动 uvicorn :8200
```

启动后：

- OpenAPI 文档：<http://127.0.0.1:8200/docs>
- 健康探针：`GET /healthz`（存活）、`GET /readyz`（依赖就绪）

> `run_dev.py` 从机器级 `env/secrets.yaml` 注入模型 Key（BYOK，密钥不入 git）；也可直接用 `uvicorn --factory app.main:create_app --port 8200` 并自行导出环境变量。

### 前端

```bash
cd frontend
npm install
npm run build            # 产物 → app/webdist，由后端托管（私有化交付模式）
# 或开发热更新: npm run dev   # Vite :5180，代理 /api → :8200
```

### standard 档（PostgreSQL + Redis + Celery）

```bash
docker compose -f docker-compose.dev.yml up -d    # 起 PG16 + Redis7（仅绑 127.0.0.1）

export IDP_DEPLOY_TIER=standard
export IDP_QUEUE_BACKEND=celery
export IDP_DATABASE_URL=postgresql+asyncpg://idp:idp_dev_pw@127.0.0.1:5432/idp
celery -A app.tasks.celery_app worker -Q orchestrate,parse_cpu,parse_gpu,extract   # Windows 开发加 -P threads
python run_dev.py
```

## 配置

所有应用配置经环境变量（前缀 `IDP_`）或 `.env` 注入；系统级配置（模型通道 / 解析器 / 检测器）走 config-as-code：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `IDP_DATABASE_URL` | `sqlite+aiosqlite:///./data/idp.db` | 数据库连接串；standard 用 `postgresql+asyncpg://...` |
| `IDP_DEPLOY_TIER` | `lite` | `lite` / `standard` / `air_gapped` |
| `IDP_QUEUE_BACKEND` | `inprocess` | `inprocess`（lite）/ `celery`（standard） |
| `IDP_REDIS_URL` | `redis://127.0.0.1:6379/0` | Celery broker/backend |
| `IDP_AUTH_MODE` | `off` | `on` 后 `/api` 需 Bearer JWT 或 API Key |
| `IDP_ADMIN_EMAIL` / `IDP_ADMIN_PASSWORD` | — | 启动时仅在用户表为空且设了密码才引导管理员 |
| `IDP_DEFAULT_TENANT` | `default` | 缺省租户 |
| `IDP_DATA_DIR` | 仓库根下 `data/` | 数据目录（blob 与派生文件） |
| `IDP_MULTI_DOC_SPLIT` | `auto` | 多文档拆分：`auto` / `off` |
| `IDP_PUBLIC_URL` | — | 邀请 / 找回邮件里的链接基址（公网部署必设） |

模型密钥（BYOK，密钥只在环境变量，永不入库、不入 git）：

- `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` / `IRUIDONG_API_KEY`
- 离线档：`LOCAL_VLLM_BASE_URL` 指向自建 vLLM

> ⚠️ **`IDP_SECRET_KEY` 禁止写入 env**：env 优先于平台持久化的密钥，一旦设置会把 SMTP 密码 / OIDC secret / 租户 BYOK 全部变成解不开的密文（见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §5）。

模型通道、解析器、检测器分别在 [`configs/providers.yaml`](configs/providers.yaml)、[`configs/parsers.yaml`](configs/parsers.yaml)、[`configs/detectors.yaml`](configs/detectors.yaml) 中声明。

## 测试

```bash
pytest -q                       # 全量单测（mock LLM，不烧 token），187 项
pytest tests/test_architecture.py   # 架构约束 AST 检查
python tests/canary_real.py      # 真实金丝雀：真 PDF + 真实模型通道
python tests/acceptance_samples.py  # 验收样本集（会消耗 token）
```

- 迁移测试需 PostgreSQL：`docker compose -f docker-compose.dev.yml up -d` 后跑 `tests/test_migrations.py`。
- 无 Java 时 opendataloader 的真实解析用例会自动 skip（CI 里用 Temurin 17 补齐）。

## 项目结构

```
doc-idp-platform/
├─ app/
│  ├─ main.py            # app 工厂 + /healthz /readyz
│  ├─ config.py          # 配置（env + providers/parsers/detectors.yaml）
│  ├─ db.py  models.py   # 引擎/session + ORM（全表带 tenant_id）
│  ├─ tenancy.py         # 租户中间件
│  ├─ parsers/           # 解析插件（UDR 协议）
│  ├─ skillengine/       # SkillPackage DSL + 提示词编译器
│  ├─ extraction/        # 抽取管线（纯函数）+ 置信度 0–3 + 双通道校验
│  ├─ detectors/         # 印章/签名视觉检测
│  ├─ tasks/             # 任务执行（inprocess runner / Celery）
│  ├─ billing/           # 影子计费 ledger + 计费引擎
│  ├─ auth/              # JWT / OIDC / API Key / 邀请激活
│  ├─ notify/  integrations/   # 邮件 / Webhook
│  ├─ storage/           # 存储键缝（LocalStorage）
│  ├─ plugins/           # 插件注册表
│  ├─ api/               # FastAPI 路由
│  └─ webdist/           # 前端构建产物（后端托管）
├─ frontend/             # Vue 3 + TS + Vite
├─ configs/              # providers.yaml / parsers.yaml / detectors.yaml
├─ alembic/              # schema 迁移（唯一 schema 演进路径）
├─ docs/                 # ARCHITECTURE.md / ALEMBIC.md
├─ tests/                # pytest 套件（含架构 AST 检查）
├─ docker-compose.dev.yml  # 开发用 PG16 + Redis7
└─ pyproject.toml
```

## API

前缀 `/api/v1`，完整契约见 OpenAPI（启动后访问 `/docs`）。

| 路由组 | 主要端点 | 说明 |
|---|---|---|
| `process` | `POST /process`、`GET /status/{id}` | 提交处理 / 查询任务状态 |
| `skills` | `GET/POST /skills`、`/skills/{code}/versions`、`/skills/probe` | 技能 CRUD、版本、试抽 |
| `detect` / `locate` | `POST /detect`、`POST /locate` | 印章签名检测 / 名单定位打码 |
| `review` | `/review/queue`、`/{file_id}/confirm` 等 | 审单台 |
| `data` | `/data/cabinet/{code}`、`/data/stats/*` | 数据柜 / 统计 |
| `auth` | `/auth/login`、`/auth/invite`、`/auth/oidc/*` | 认证 |
| `billing` | `/billing/account`、`/billing/ledger`、`/billing/topup` | 账户 / 账本 / 充值 |
| `settings` | `/settings/smtp`、`/settings/api-keys`、`/settings/custom-providers` | 系统配置 |
| `hooks` | `POST/GET/DELETE /hooks` | Webhook |

## 数据库迁移

Schema 的唯一演进路径是 Alembic（`init_db` 执行 `upgrade head`，已移除 `create_all` / 运行时补列）。改完 `models.py` 后：

```bash
IDP_DATABASE_URL=sqlite+aiosqlite:////tmp/idp_gen.db alembic revision --autogenerate -m "<说明>"
# 人工审阅生成的版本文件后：
alembic upgrade head
```

既有库（历史 `create_all` 建的）一次性采纳需先 `stamp head`，详见 [`docs/ALEMBIC.md`](docs/ALEMBIC.md)。

## 文档

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 架构原则、依赖方向、自研/外购边界、禁做清单、受保护资产（权威）
- [`docs/ALEMBIC.md`](docs/ALEMBIC.md) — 数据库迁移与既有库采纳流程

## 开发与 CI

- 提交规范：`feat` / `fix` / `chore` / `docs` / `test` 等 conventional commit，一个 schema 变更对应一个 revision 且同 PR。
- Lint：`ruff check app tests`（规则集钉在 `pyproject.toml`）。
- 前端类型检查与构建：`npm run build`（`vue-tsc -b && vite build`）。

CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）在 Python 3.11 / 3.12 双版本上运行，起 PG16 + Redis7 service 容器（迁移 / RLS 真实执行），安装 Temurin 17 补齐真实 JVM 解析测试，并以 ruff 作为质量门禁。

## 许可证

私有项目，当前未附带开源许可证。
