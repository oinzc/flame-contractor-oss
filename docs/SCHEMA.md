# Flame Contractor SQLite Schema 规格

> 本文描述当前 0.1.0 运行 schema（含 Alembic 迁移扩展）。实际建表与升级由 Alembic 完成。

## 1. 关系图

```mermaid
erDiagram
  ACCOUNT ||--o{ CONTRACT_TARGET : owns
  MESSAGE_TEMPLATE ||--o{ CONTRACT_TARGET : binds
  ACCOUNT ||--o{ SEND_JOB : schedules
  CONTRACT_TARGET ||--o{ SEND_JOB : receives
  MESSAGE_TEMPLATE ||--o{ SEND_JOB : renders
  SEND_JOB ||--o{ SEND_ATTEMPT : has
  ACCOUNT ||--o{ SEND_SCHEDULE : schedules_daily
  ACCOUNT ||--o{ SCAN_TASK : scans

  ACCOUNT {
    string id PK
    string display_name UK
    string status
    boolean enabled
    string timezone
    string send_window_start
    string send_window_end
    datetime last_verified_at
    datetime last_success_at
    datetime created_at
    datetime updated_at
  }

  MESSAGE_TEMPLATE {
    string id PK
    string name UK
    string type
    text content
    string image_path
    string sticker_name
    string sticker_label
    string sticker_category
    integer sticker_fallback_index
    integer version
    boolean enabled
    datetime created_at
    datetime updated_at
  }

  CONTRACT_TARGET {
    string id PK
    string account_id FK
    string platform_user_id
    string platform_handle
    string platform_conversation_id
    string conversation_type
    string nickname_snapshot
    string template_id FK
    boolean enabled
    integer streak_days
    datetime last_sent_at
    datetime last_confirmed_at
    datetime created_at
    datetime updated_at
  }

  SEND_JOB {
    string id PK
    string account_id FK
    string target_id FK
    string template_id FK  # 可空：单好友手动发送「直接输入文本」时不引用模板
    date business_date
    string message_slot_id
    string status
    datetime scheduled_at
    string lease_owner
    datetime lease_expires_at
    integer attempt_count
    string failure_class
    datetime confirmed_at
    datetime created_at
    datetime updated_at
  }

  SEND_ATTEMPT {
    string id PK
    string job_id FK
    integer attempt_no
    string stage
    string status
    string failure_class
    string artifact_ref
    datetime started_at
    datetime finished_at
  }

  SEND_SCHEDULE {
    string id PK
    string account_id FK
    date business_date
    datetime scheduled_at
    string status
    string lease_owner
    datetime lease_expires_at
    integer attempt_count
    datetime started_at
    datetime finished_at
    string last_error
    datetime created_at
    datetime updated_at
  }

  SCAN_TASK {
    string id PK
    string account_id FK
    string status
    integer total
    integer completed
    string stage
    string current_item
    integer round
    integer max_rounds
    string overall
    string detail
    text entries_json
    datetime started_at
    datetime finished_at
    datetime updated_at
  }
```

## 2. 约束

### account

- `display_name` 全局唯一；
- 不存在 Cookie、密码、Token、Storage State 字段；
- 时间窗口由应用层校验 `HH:mm`。

### contract_target

- 外键 `account_id → account.id`；
- 外键 `template_id → message_template.id`，可空；
- 展示名称回退唯一：`(account_id, nickname_snapshot)`；
- 稳定身份唯一：`(account_id, platform_user_id)`；SQLite 允许同账号存在多个 `NULL` 稳定 ID，非空 ID 不可重复；
- 群会话稳定身份唯一：`(account_id, platform_conversation_id)`；群聊使用抖音会话 protobuf 的 ID，不能与用户 `platform_user_id` 混用；
- `conversation_type` 取值为 `user`/`group`（历史用户记录可为空）；同名群聊只有在会话 ID 唯一时才自动绑定，同名冲突保留为空；
- `streak_days >= 0` 由 API 和数据库 Check 共同限制。

### message_template

- `name` 全局唯一；
- `type` 取值：`text` / `image` / `sticker`（M5 扩展）；`image` 需 `image_path`；
  `sticker` 需 `sticker_name`（`sticker_label` / `sticker_category` / `sticker_fallback_index` 可选）；
- `version >= 1`；
- 文本内容只允许白名单变量，应用层验证；无任何凭据字段。

### send_job

- `template_id` 允许为空：单好友手动「直接输入文本」发送时不引用模板（`message_slot_id` 使用内容哈希命名空间）；
  普通发送、模板发送仍必须引用有效的启用模板。

核心唯一约束：

```sql
UNIQUE (account_id, target_id, business_date, message_slot_id)
```

该约束是防重复的最终边界，即使 Scheduler 重复运行也不得生成第二条同业务任务。

### send_attempt

```sql
UNIQUE (job_id, attempt_no)
```

同一 Job 的尝试序号不可重复。

### send_schedule

M4 新增的「账号级每日调度」记录，只决定某账号某业务日期何时发（好友级发送仍走 send_job 幂等）。

```sql
UNIQUE (account_id, business_date)
```

保证同账号同一业务日期只有一条调度计划；Scheduler 重复运行也不会生成第二条。

### scan_task

- 外键 `account_id → account.id`，删除账号时由应用层先检查运行中任务并阻止危险删除；
- `status` 取值：`running` / `completed` / `failed` / `interrupted` / `cancelled`；
- `completed >= 0`；扫描滚动到底前 `total` 可以为 `NULL`，不以滚动轮次伪造总数或百分比；
- `entries_json` 保存结构化的只读会话扫描快照，供进度展示、刷新恢复和用户手动导入，
  本身不构成发送决策，也不会自动新增好友；
- `reconciliation_json` 保存完整成功扫描生成的五类前后对账快照；旧任务、失败、中断或取消任务保持无版本空结构，不构成自动导入、删除或发送依据；
- `updated_at` 建立索引，用于读取账号最近一次扫描快照。

## 3. SQLite 配置

连接建立时执行：

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
```

理由：

- WAL 允许控制台读与调度写更好地并行；
- SQLite 默认不强制外键，必须显式开启；
- busy timeout 避免短暂写锁立即失败。

## 4. 迁移策略

骨架期使用 `SQLModel.metadata.create_all()`，只负责空库初始化，不承担生产结构升级。

进入 M2 前引入 Alembic，并生成第一份基线迁移。不得长期用 `create_all()` 冒充迁移系统。

## 5. 数据保留

建议默认值（下一阶段实现）：

- SendJob/Attempt：保留 180 天；
- 普通日志：30 天；
- 失败截图：14 天；
- Playwright Trace：7 天；
- `unknown_requires_review` 相关 artifact：人工处理前不自动删除。

任何清理策略不得删除账号 Profile 或会话凭据。
