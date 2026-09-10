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
`alembic_version`。**直接 `upgrade head` 会因表已存在而启动即崩**，必须先 stamp。

### ⚠️ stamp 的目标是**基线** `c8c989251f69`，不是 `head`

create_all 时代的库，其 schema 永远等于**基线 revision 的那一刻**——不管它是哪天建的。
此后新增的每个迁移都**没有**在这个库上跑过。

`stamp head` 会把**所有**迁移（含基线之后的）标记成「已应用」而跳过执行：库能起来，
但后加的列/表永远不存在。2026-09-10 实测过这条路径的后果：

| | `stamp head` | `stamp c8c989251f69` + `upgrade head` |
|---|---|---|
| `users.session_epoch` | 缺失 | 存在 |
| `POST /auth/login` | **HTTP 500** | 401（凭据错误的正确响应） |

`users.session_epoch` 被 `app/tenancy.py` 在**每个带鉴权请求**里读取，`IDP_AUTH_MODE=on`
的生产上等于全员登录崩。**基线之后每加一个迁移，`stamp head` 就多漏一张表/一列。**

```bash
# 1. 备份（既有发布流程已含 sqlite3 .backup 一致性备份，保留）
# 2. 对备份副本演练（务必用 CLI + 独立进程核验，见下）
cp idp-pre-<release>.db /tmp/adopt.db
IDP_DATABASE_URL="sqlite+aiosqlite:////tmp/adopt.db" .venv/bin/python -m alembic stamp c8c989251f69
IDP_DATABASE_URL="sqlite+aiosqlite:////tmp/adopt.db" .venv/bin/python -m alembic upgrade head
sqlite3 /tmp/adopt.db "SELECT version_num FROM alembic_version;"   # 应为 head
sqlite3 /tmp/adopt.db "PRAGMA integrity_check;"                    # ok
# 3. 对真库执行同样两条；upgrade 之后所有部署照常 upgrade head（app 启动时自动跑）
```

两条注意事项：

- **`IDP_DATA_DIR` 必须与生产一致**（`/opt/doc-idp/shared/data`）：`c7ae5ec98d9e` 按这个前缀
  把绝对路径改写成 key，前缀不对则该迁移静默什么都不做。
- **用 `venv/bin/python -m alembic`，不要用 `venv/bin/pip`/控制台脚本**：release 里的 venv 是
  `cp -a` 传下来的，`bin/pip` 的 shebang 仍指向**最初创建它的那个 release**，装包会装进
  别人的 venv。验收一律以「独立进程重新读库」为准，不要采信同进程内的读值。

`tests/test_migrations.py::test_adopt_baseline_frozen_db_via_stamp` 钉的就是这条路径；
`tests/test_adopt_pre_alembic_db_via_stamp` 覆盖的是「库 schema 已经等于今天模型」的另一种情形。
