# Alembic 采纳与使用（WP1）

自 2026-09-07 起，schema 的唯一演进方式是 Alembic 迁移；`create_all` 与运行时
PRAGMA 补列已删除（`docs/ARCHITECTURE.md` §4）。URL 一律来自 `IDP_DATABASE_URL`
（`alembic/env.py` 读取 app settings），`alembic.ini` 里的 `sqlalchemy.url` 是占位符。

## 日常操作

```bash
# 改了 models.py 之后：
IDP_DATABASE_URL=sqlite+aiosqlite:////tmp/idp_gen.db .venv/bin/alembic revision --autogenerate -m "<what>"
# 人工审阅生成的 versions/*.py（autogenerate 会漏 server_default 与部分类型变更），再：
.venv/bin/alembic upgrade head
```

规则：

1. **每个 schema 变化一个 revision**，跟对应代码改动同一个 PR/commit。
2. 生成后**必须人工审阅**；SQLite 走 batch mode（env 已配 `render_as_batch`）。
3. PG 新增带 `tenant_id` 的表时，`init_db` 的 `apply_rls` 会在启动时自动覆盖新表；
   手工建表的环境要自己跑 `apply_rls`。
4. 测试铁律：`tests/test_migrations.py` 三条（sqlite 无漂移 / PG 无漂移 / 旧库 stamp 采纳）保持绿。

## 既有数据库（生产 SQLite）一次性采纳

生产库（`/opt/doc-idp/shared/data/idp.db`）的表是 create_all 时代建的，没有
`alembic_version`。**直接 `upgrade head` 会因表已存在而启动即崩**，必须先 stamp：

```bash
# 1. 备份（既有发布流程已含 sqlite3 .backup 一致性备份，保留）
# 2. 对备份副本演练：
cp idp-pre-<release>.db /tmp/adopt.db
IDP_DATABASE_URL="sqlite+aiosqlite:////tmp/adopt.db" .venv/bin/alembic stamp head
IDP_DATABASE_URL="sqlite+aiosqlite:////tmp/adopt.db" .venv/bin/alembic upgrade head   # 必须 no-op
sqlite3 /tmp/adopt.db "PRAGMA integrity_check;"   # ok
# 3. 发布时对真库执行同样的 stamp（upgrade 不需要——基线即现状）
```

`stamp head` 只是写入版本号，不改任何业务表；`tests/test_adopt_pre_alembic_db_via_stamp`
验证的就是这条路径。采纳之后的所有部署照常 `upgrade head`（app 启动时自动跑）。
