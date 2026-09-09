# Doc IDP Platform

<p align="center">
  <a href="./README.md"><b>English</b></a> · <a href="./README.zh-CN.md">简体中文</a>
</p>

> An LLM-driven Intelligent Document Processing (IDP) platform: **samples as model** — one or two sample documents plus natural-language rules are all it takes to launch structured extraction for a new document type.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)](https://vuejs.org/)
[![CI](https://github.com/EricZehuaChang/doc-idp-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/EricZehuaChang/doc-idp-platform/actions/workflows/ci.yml)

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [API](#api)
- [Database Migrations](#database-migrations)
- [Documentation](#documentation)
- [Development & CI](#development--ci)
- [License](#license)

## Overview

Doc IDP Platform is a privately deployable intelligent document processing platform covering the full `upload → parse → extract → verify → review → confirm` workflow, with enterprise-grade boundaries for authentication, multi-tenancy, billing, and notifications.

Core design principles:

- **Samples as model**: the `SkillPackage` DSL compiles "field rules + a few samples" into an extraction prompt — no model training, no annotation platform.
- **Two-stage parse / extract**: parsers emit a Unified Document Representation (UDR); the extraction pipeline runs on top of it, fully decoupled from parsing.
- **Confidence 0–3**: model self-reported confidence is never trusted; the platform grades evidence independently to drive review and masking decisions.
- **Tenant as a first-class citizen**: `tenant_id` spans every table; the `standard` tier enables row-level security (RLS) on PostgreSQL.
- **Shadow billing**: an append-only ledger is recorded from day one, powering usage-based settlement.

## Features

| Domain | Capability |
|---|---|
| Document parsing | markitdown (Office), pdfplumber (electronic PDF), glm-ocr-cloud, ofd, opendataloader, multimodal vlm-*, auto-routed by file type |
| Skill engine | `SkillPackage` versioning, template gallery, prompt compilation, `probe` / `draft-*` dry runs |
| Structured extraction | dual-channel rule / consistency validation, `$value/$confidence/$bbox/$pages/...` result contract |
| Visual detection | seal / signature detection (PP-DocLayoutV3 + red-seal HSV fallback + YOLOS), `/detect` is pure compute with zero persistence |
| Review console | side-by-side verification, bbox highlighting, lock / assign / correct / confirm / reject loop |
| Multi-tenancy | application-level middleware + PostgreSQL RLS dual isolation |
| Authentication | JWT sessions, API keys, OIDC, invite / activate / password reset |
| Billing | shadow / live dual-mode, pre-freeze-then-charge, plan entitlements, per-key quotas |
| Data | data cabinet, usage statistics, CSV export |
| Integration | webhooks, SMTP email notifications |

## Architecture

A modular monolith plus an optional Celery worker; infrastructure switches by deployment tier:

| Tier | Database | Queue | Use case |
|---|---|---|---|
| `lite` | SQLite | in-process runner | development, demos, single-machine delivery |
| `standard` | PostgreSQL 16 (RLS) | Redis 7 + Celery | multi-tenant production |
| `air_gapped` | same (offline) | same | intranet / no external network |

Dependency direction (enforced by an AST check in `tests/test_architecture.py`):

```text
parsers ──► skillengine ──► extraction/pipeline(pure) ──► tasks/runner ──► api/routes
 (UDR)       (SkillPackage DSL)      (UDR + Skill → result)   (idempotent 3-stage)  (FastAPI)
```

Architecture principles, build-vs-buy boundary, the do-not list, and protected assets are documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2 (async), Pydantic v2, Alembic, Celery + Redis, aiosqlite / asyncpg
- **Frontend**: Vue 3, TypeScript, Vite, Vue Router, TanStack Query, pdf.js
- **Infrastructure**: PostgreSQL 16, Redis 7, Docker Compose (development)

## Getting Started

### Prerequisites

- Python **3.11+**
- Node.js **18+** (frontend build)
- Java **11+** (required by the opendataloader parser; degrades to pdfplumber when absent)
- Docker (optional, standard-tier dev infrastructure)

### Install & Run the Backend

```bash
git clone https://github.com/EricZehuaChang/doc-idp-platform.git
cd doc-idp-platform

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# base + parsers; add the pg / vision extras for PostgreSQL / visual detection
pip install -e ".[dev,parsers]"      # full: pip install -e ".[dev,parsers,pg,vision]"

cp .env.example .env                 # fill in model keys as needed (see Configuration)
python run_dev.py                    # injects machine-level keys and starts uvicorn :8200
```

After startup:

- OpenAPI docs: <http://127.0.0.1:8200/docs>
- Health probes: `GET /healthz` (liveness), `GET /readyz` (dependencies ready)

> `run_dev.py` injects model keys from a machine-level `env/secrets.yaml` (BYOK — keys never enter git). You can also run `uvicorn --factory app.main:create_app --port 8200` directly and export the environment variables yourself.

### Frontend

```bash
cd frontend
npm install
npm run build            # output → app/webdist, served by the backend (private-deploy mode)
# or hot-reload dev: npm run dev   # Vite on :5180, proxies /api → :8200
```

### Standard Tier (PostgreSQL + Redis + Celery)

```bash
docker compose -f docker-compose.dev.yml up -d    # PG16 + Redis7 (bound to 127.0.0.1 only)

export IDP_DEPLOY_TIER=standard
export IDP_QUEUE_BACKEND=celery
export IDP_DATABASE_URL=postgresql+asyncpg://idp:idp_dev_pw@127.0.0.1:5432/idp
celery -A app.tasks.celery_app worker -Q orchestrate,parse_cpu,parse_gpu,extract   # add -P threads on Windows dev
python run_dev.py
```

## Configuration

All application configuration is injected via environment variables (prefix `IDP_`) or a `.env` file; system-level configuration (model channels / parsers / detectors) is config-as-code:

| Environment variable | Default | Description |
|---|---|---|
| `IDP_DATABASE_URL` | `sqlite+aiosqlite:///./data/idp.db` | Database connection string; `postgresql+asyncpg://...` for standard |
| `IDP_DEPLOY_TIER` | `lite` | `lite` / `standard` / `air_gapped` |
| `IDP_QUEUE_BACKEND` | `inprocess` | `inprocess` (lite) / `celery` (standard) |
| `IDP_REDIS_URL` | `redis://127.0.0.1:6379/0` | Celery broker / backend |
| `IDP_AUTH_MODE` | `off` | when `on`, `/api` requires a Bearer JWT or API key |
| `IDP_ADMIN_EMAIL` / `IDP_ADMIN_PASSWORD` | — | bootstrap admin is created only when the user table is empty and a password is set |
| `IDP_DEFAULT_TENANT` | `default` | default tenant |
| `IDP_DATA_DIR` | repo-root `data/` | data directory (blobs and derived files) |
| `IDP_MULTI_DOC_SPLIT` | `auto` | multi-document split: `auto` / `off` |
| `IDP_PUBLIC_URL` | — | base URL used in invite / reset emails (required for public deployments) |

Model keys (BYOK — keys live only in environment variables, never in the DB or git):

- `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` / `IRUIDONG_API_KEY`
- Air-gapped: `LOCAL_VLLM_BASE_URL` points to a self-hosted vLLM

> ⚠️ **Never put `IDP_SECRET_KEY` in the environment**: env takes precedence over the platform-persisted key, so setting it renders the SMTP password / OIDC secret / tenant BYOK keys undecryptable (see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §5).

Model channels, parsers, and detectors are declared in [`configs/providers.yaml`](configs/providers.yaml), [`configs/parsers.yaml`](configs/parsers.yaml), and [`configs/detectors.yaml`](configs/detectors.yaml).

## Testing

```bash
pytest -q                          # full unit suite (LLM mocked, no token burn), 187 tests
pytest tests/test_architecture.py      # architecture constraint AST check
python tests/canary_real.py         # real canary: real PDF + real model channel
python tests/acceptance_samples.py      # acceptance sample set (burns tokens)
```

- Migration tests need PostgreSQL: run `docker compose -f docker-compose.dev.yml up -d`, then `tests/test_migrations.py`.
- Without Java, the opendataloader real-parse cases auto-skip (CI installs Temurin 17 to cover them).

## Project Structure

```
doc-idp-platform/
├─ app/
│  ├─ main.py            # app factory + /healthz /readyz
│  ├─ config.py          # configuration (env + providers/parsers/detectors.yaml)
│  ├─ db.py  models.py   # engine/session + ORM (tenant_id on every table)
│  ├─ tenancy.py         # tenant middleware
│  ├─ parsers/           # parser plugins (UDR protocol)
│  ├─ skillengine/       # SkillPackage DSL + prompt compiler
│  ├─ extraction/        # extraction pipeline (pure) + confidence 0–3 + dual-channel validation
│  ├─ detectors/         # seal/signature visual detection
│  ├─ tasks/             # task execution (in-process runner / Celery)
│  ├─ billing/           # shadow-billing ledger + billing engine
│  ├─ auth/              # JWT / OIDC / API keys / invite & activate
│  ├─ notify/  integrations/   # email / webhooks
│  ├─ storage/           # storage-key seam (LocalStorage)
│  ├─ plugins/           # plugin registry
│  ├─ api/               # FastAPI routes
│  └─ webdist/           # built frontend (served by the backend)
├─ frontend/             # Vue 3 + TS + Vite
├─ configs/              # providers.yaml / parsers.yaml / detectors.yaml
├─ alembic/              # schema migrations (the only schema evolution path)
├─ docs/                 # ARCHITECTURE.md / ALEMBIC.md
├─ tests/                # pytest suite (incl. architecture AST check)
├─ docker-compose.dev.yml  # dev PG16 + Redis7
└─ pyproject.toml
```

## API

Prefix `/api/v1`; the full contract is in the OpenAPI docs (visit `/docs` after startup).

| Route group | Key endpoints | Description |
|---|---|---|
| `process` | `POST /process`, `GET /status/{id}` | submit processing / query task status |
| `skills` | `GET/POST /skills`, `/skills/{code}/versions`, `/skills/probe` | skill CRUD, versions, dry run |
| `detect` / `locate` | `POST /detect`, `POST /locate` | seal/signature detection / name-list locate & mask |
| `review` | `/review/queue`, `/{file_id}/confirm`, … | review console |
| `data` | `/data/cabinet/{code}`, `/data/stats/*` | data cabinet / statistics |
| `auth` | `/auth/login`, `/auth/invite`, `/auth/oidc/*` | authentication |
| `billing` | `/billing/account`, `/billing/ledger`, `/billing/topup` | account / ledger / top-up |
| `settings` | `/settings/smtp`, `/settings/api-keys`, `/settings/custom-providers` | system configuration |
| `hooks` | `POST/GET/DELETE /hooks` | webhooks |

## Database Migrations

Alembic is the only schema evolution path (`init_db` runs `upgrade head`; `create_all` and runtime column patching have been removed). After editing `models.py`:

```bash
IDP_DATABASE_URL=sqlite+aiosqlite:////tmp/idp_gen.db alembic revision --autogenerate -m "<description>"
# review the generated version file, then:
alembic upgrade head
```

Existing databases (created in the historical `create_all` era) must be adopted via `stamp head` first — see [`docs/ALEMBIC.md`](docs/ALEMBIC.md).

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — architecture principles, dependency direction, build-vs-buy boundary, do-not list, protected assets (authoritative)
- [`docs/ALEMBIC.md`](docs/ALEMBIC.md) — database migrations and the existing-database adoption flow

## Development & CI

- Commit convention: conventional commits (`feat` / `fix` / `chore` / `docs` / `test` …); one revision per schema change, in the same PR.
- Lint: `ruff check app tests` (ruleset pinned in `pyproject.toml`).
- Frontend type-check & build: `npm run build` (`vue-tsc -b && vite build`).

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on Python 3.11 and 3.12, spins up PG16 + Redis7 service containers (migrations / RLS run for real), installs Temurin 17 to cover real-JVM parsing tests, and gates on ruff.

## License

Private project; no open-source license is currently attached.
