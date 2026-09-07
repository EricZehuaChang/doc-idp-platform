# 架构原则（权威）

> 评估依据:`brain/projects/project05-智能文档识别平台/deliverables/2026-09-07-架构优化建议评估与执行方案.md`。
> 本文件是依赖方向与边界的**唯一权威**;`tests/test_architecture.py` 用 AST 检查强制执行 §2。

## 1. 一句话架构

**模块化单体 + 可选 Celery worker**:lite 档单进程(SQLite + inprocess runner),standard 档 PostgreSQL + Redis + Celery 分池。业务状态机的唯一事实源是 DB 行,队列可丢可重建。

## 2. 依赖方向(强制)

```text
parsers ──► skillengine ──► extraction/pipeline(纯函数) ──► tasks/runner ──► api/routes
 (UDR 协议)   (SkillPackage DSL)   (UDR + SkillPackage → result)      (幂等三阶段)    (FastAPI)
```

**纯核心禁区**——下列文件不得 import 任何基础设施/框架模块
(`app.db`、`app.models`、`app.tasks`、`app.api`、`app.billing`、`app.notify`、`app.integrations`、`app.auth`):

- `app/parsers/**`(全部)
- `app/skillengine/**`(全部)
- `app/extraction/pipeline.py`、`confidence.py`、`validators.py`、`splitter.py`、`provider_client.py`

例外(按 adapter 对待,不入禁区):`app/extraction/byok.py`、`app/extraction/custom_providers.py`(读租户 DB 配置,由 runner 层 warm 后传入)。

纯核心允许的依赖:标准库、pydantic、`app.config`(yaml 配置加载)、纯核心内部互相 import。

## 3. 自研 / 外购边界

| 自研(核心竞争力) | 外购/标准件(不自研) |
|---|---|
| UDR 统一文档协议(`parsers/base.py`) | 认证协议:OIDC 通用实现即可,不写 Keycloak/LDAP/SCIM 代码 |
| SkillEngine / SkillPackage DSL(`skillengine/`) | 对象存储:Storage 接口后只配 Local;S3/MinIO 有客户要求再加 |
| Extraction 管线(`extraction/pipeline.py`,纯函数) | 监控:结构化事件 + /metrics;不引 Tempo/Loki/Grafana 全家桶 |
| Evidence/Locate(`extraction/confidence.py`) | 队列:Celery+Redis 已就绪,不再造 |
| Review/Correction 闭环(`review.py` + Correction 表) | DB 迁移:Alembic,禁运行时 schema patch |
| Evaluation / Golden Set(`evaluation/`) | 邮件/Webhook:薄 adapter 已就绪 |

## 4. 禁做清单

- ❌ 微服务拆分(auth-service/billing-service/… 一律不拆)
- ❌ 为目录好看大搬 code(四层目录收敛判定为"已满足",不做)
- ❌ 用 LangChain/Dify/Flowise DSL 替代 SkillPackage
- ❌ 运行时 `ALTER TABLE`/PRAGMA 补列(schema 变化必须走 Alembic)
- ❌ 删除 Billing(计费闭环是云市场上架前置);只冻结范围:不做发票/退款/账期/优惠券/订阅
- ❌ 接大量功能重复的 LLM/OCR 通道;没有 benchmark 数字不换默认模型

## 5. 受保护资产(改动前必读)

1. **`Block.chars` 逐字框**:值级紧框的来源,KMBP 脱敏依赖;`_tight()` 的 char-union 语义不许动。
2. **`$`-前缀结果契约**:`$value/$confidence/$bbox/$pages/$hits/$cells/$challenger/$reasoning`——mask-guard(`intelligent-mask-guard/backend/app/services/idp_client.py`)直接消费;只许增键,不许改语义。
3. **`escalate_if_tables_missing`**:技能声明表字段而免费解析零表时才升级付费 OCR,只对 opendataloader 输出生效——防存量流被静默改道付费。
4. **`entity_list` 表**:文档级 PII 扫描,不分页、不触发 OCR 升级。
5. **`_merge_page_raw` 标量取"最后非空"**:发票合计在末页,方向不许反。
6. **`Skill.code` 全局主键**(`models.py`),tenant_id 只是普通列;模板导入按租户派生代码。
7. **RLS**:PG 上每张带 tenant_id 的表 FORCE RLS + GUC;Alembic 新表必须重跑 `apply_rls`。
8. **`IDP_SECRET_KEY` 禁入 env**(env 优先于 platform_settings 持久化的 key,一加全部密文失效)。
9. **systemd `WorkingDirectory=/opt/doc-idp/current`**:SQLite 缺省路径相对 CWD——Storage 改造落地后此坑应消失。

## 6. 双档纪律

每个结构性改动在 lite(SQLite+inprocess)与 standard(PG16+Redis+Celery,`docker-compose.dev.yml`)都要可用;CI 在两套 Python(3.11/3.12)上跑单测(mock,不烧 token)。
