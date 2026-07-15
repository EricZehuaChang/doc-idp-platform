# Doc IDP Platform

LLM 驱动的智能文档处理平台（M1 walking skeleton）。设计权威文档：
`D:\brain\projects\project05-智能文档识别平台\deliverables\2026-07-15-系统设计方案-v0.2-定稿.md`。

核心理念：**样本即模型**——1-2 份样本 + 自然语言规则 = 一类文档的结构化抽取技能；
解析→抽取两段式；置信度 0-3 不信模型自报；租户一等公民；影子计费从第一天记账。

## 快速开始

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,parsers]"
copy .env.example .env    # 填模型 key（或用 D:\Claude Code\env\load_keys.py 注入）
.venv\Scripts\uvicorn --factory app.main:create_app --port 8200
# OpenAPI 文档: http://localhost:8200/docs
```

## 测试

```powershell
.venv\Scripts\python -m pytest -q          # 全 mock，不烧 token
.venv\Scripts\python tests\canary_real.py  # 真实金丝雀：真 PDF + GLM-OCR + qwen（烧少量 token）
```

## 结构（按域拆模块）

```
app/
├─ main.py            # app 工厂 + healthz/readyz 探针
├─ config.py          # 系统级配置（env + providers.yaml/parsers.yaml）
├─ db.py  models.py   # 全表带 tenant_id；assignee/corrections/golden_samples/ledger 第一天就位
├─ tenancy.py         # 租户中间件（应用层防线；M2 加 PG RLS 第二道）
├─ plugins/registry.py# 插件注册表（fork-and-own 自 KBase m5-1）
├─ parsers/           # UDR 统一中间表示；glm-ocr-cloud / pdfplumber / markitdown；自动路由
├─ skillengine/       # SkillPackage schema + prompt 编译器（样本即模型的实现）
├─ extraction/        # provider 客户端(重试) + 规则/一致性双通道 + 置信度 0-3 + 管线
├─ tasks/runner.py    # M1 进程内 jobs（幂等/页级失败隔离；M2 换 Celery 分池）
├─ billing/ledger.py  # 影子计费（append-only，只记不扣）
└─ api/routes/        # /api/v1/process /status /skills（API-first）
```

## M1 剩余 / M2 入口

- 校验界面 v1（双屏 + bbox 高亮）与校验 API（锁定/修正/Confirm→corrections 表）
- RapidOCR CPU 兜底插件、验收跑中投三组样本
- M2：Celery 分池、GLM-OCR 本地 vLLM、MonkeyOCR 插件、框选标注、质量看板、Golden Set 发布门禁
