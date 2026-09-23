# 架构说明

面向维护者的项目架构导读。配合 README.md（使用说明）阅读。

## 项目定位

每周定时统计社区（openEuler / BoostKit）的待处理 PR 和 Issue，按 SIG → 仓库 → reviewer（maintainer/committer）归属，为每位 reviewer 生成一份个性化 HTML 报表，通过邮件发送，督促处理积压。

在此基础上还有一条**社区级**的「资料汇总」流水线（`docs_statistics.py`）：同样抓取全社区待处理项，但过滤出资料相关的部分，按 SIG 分组后发给固定的资料经理名单，不做 reviewer 归属。

## 总体数据流

```
┌─────────────────────────────────────────────────────────────────┐
│ main()（pr_statistics.py / issue_statistics.py）                 │
│                                                                 │
│ 1. setup_community()      读 COMMUNITY 环境变量（默认 openeuler） │
│                           加载 communities.yaml，chdir 到         │
│                           <community>/ 子目录                     │
│ 2. prepare_env()          全新克隆 <community>/community 仓库     │
│                           （SIG/仓库/人员信息的唯一来源）          │
│ 3. get_sigs()             解析 SIG → 仓库列表                     │
│ 4. all_sigs_compare()     各 SIG 的 PR 处理率周对比（仅 openEuler）│
│ 5. get_repos_*_mapping()  拉取全社区待处理 PR / Issue             │
│ 6. *_statistics()         归属 reviewer → 生成报表 → 发邮件       │
└─────────────────────────────────────────────────────────────────┘
```

第 6 步内部：

```
遍历 SIG → 仓库 → 待处理项
  ├─ 状态标注（草稿 / CLA认证失败 / 门禁检查失败 / 存在冲突 / 等待更新）
  ├─ 归属：SIG maintainers ∪ 仓库 committers（来自 OWNERS / sig-info.yaml）
  └─ 按 reviewer 聚合
       ├─ 每人每角色生成 CSV → XLSX（样式/着色）→ HTML
       ├─ Maintainer 部分 + Committer 部分合并为一封邮件
       ├─ 经 email_controls.yaml 过滤（退订/偏好）
       └─ SMTP 发送（或 DRY_RUN 写入 <community>/test_output/）
```

资料汇总（`docs_statistics.py`）的 main() 步骤与上面一致（同样一次抓取全社区），只是第 1 步多传一个 `workdir`，第 6 步换成：

```
遍历 SIG → 仓库 → 待处理项
  ├─ 过滤：PR 命中 docs_report.pr_labels；Issue 标题前缀或 issue_type 命中
  ├─ 按 SIG 生成 CSV → XLSX → HTML（列布局与周报一致；PR 报表末尾多一列「资料状态」，
  │  由 excel_optimization(extra_header=..., extra_fills=...) 渲染，见下）
  ├─ 每类合并为一封邮件（PR 一封、Issue 一封）
  ├─ 经 email_controls.yaml 过滤（docs_pr / docs_issue × receiver）
  └─ SMTP 发送（或 DRY_RUN 写入 <workdir>/test_output/）
```

## 核心文件

| 文件 | 职责 |
|---|---|
| `pr_statistics.py` | PR 统计入口：数据源分发、PR 状态标注、按 reviewer 聚合发邮件 |
| `issue_statistics.py` | Issue 统计入口：同上，列布局少一列（无分支列），状态列为 类型/状态/负责人 |
| `docs_statistics.py` | 资料汇总入口：复用上面的抓取，按资料口径过滤，社区级发两封（PR / Issue） |
| `common.py` | 全部共享能力（详见下节） |
| `communities.yaml` | 社区差异的唯一配置出口（新增社区只改这里和加 Jenkins job） |
| `email_controls.yaml` | 用户级邮件接收偏好（退订管理），支持 `communities.<社区名>` 按社区覆盖 |

## common.py 模块划分

| 小节 | 关键函数 | 说明 |
|---|---|---|
| Logger | `log`、`Logger.rebind()` | 全局日志；chdir 后 rebind 到社区子目录的 statistics.log |
| Community config | `load_community_config()`、`setup_community()` | 配置加载（路径基于 `__file__`，与 CWD 无关）+ 社区工作目录切换 |
| Environment | `prepare_env()` | 克隆 community 仓库、建 data/ |
| SIG parsing | `get_sigs()`、`get_maintainers()`、`get_committers_mapping()`、`get_repo_members()` | SIG→仓库、人员解析；reviewer = maintainers ∪ committers |
| Email mapping | `create_email_mappings()`、`get_email_mappings()` | 从 sig-info.yaml 提取 gitcode_id → 邮箱，生成 email_mapping.yaml |
| Email controls | `MAIL_TYPES`、`load_email_controls()`、`should_send()`、`expand_controls()` | 退订/偏好过滤；邮件类型与角色登记在 `MAIL_TYPES`，新增类型改这里即可 |
| Helpers | `fill_status()`、`sort_rows_by_sig_duration()`、`clean_env()` | 状态文案、报表行排序（SIG 升序 + 组内开启天数降序）、临时目录清理 |
| Excel/HTML | `csv_to_xlsx()`、`excel_optimization()` | 报表生成：按 SIG 分组标题、开启天数着色（7/30/365 天三档）、异常状态标黄；可选 `extra_header`/`extra_fills` 追加一列（资料报表的「资料状态」列，默认不传即与周报完全一致） |
| Email sending | `send_email()`、`merge_html_parts()`、`write_dry_run_html()` | SMTP 发送；合并 Maintainer/Committer 两部分；DRY_RUN 本地输出 |
| Processed-rate | `all_sigs_compare()`、`compare_sig_processed_rate()` | dsapi 周对比；`processed_rate: none` 的社区直接返回空 |
| GitCode API | `gitcode_open_items()`、`gitcode_fetch_repo_items()`、`adapt_gitcode_pr()`、`adapt_gitcode_issue()` | gitcode_api 数据源：逐仓库拉取并适配为内部格式 |

## 两种数据源

| | `ipb`（openEuler） | `gitcode_api`（BoostKit） |
|---|---|---|
| 接口 | `ipb.osinfra.cn/pulls`、`/issues` 聚合接口 | GitCode API 逐仓库 `/pulls`、`/issues` |
| 认证 | 无 | `GITCODE_TOKEN`（access_token 参数） |
| 规模 | 全社区 2 次分页拉取 | 约 160 仓库 × 2，实测约 2 分钟 |
| 私有仓库 | 不涉及 | 403/404 记 warning 跳过，不中断 |
| 字段差异 | 原生即内部格式 | 需适配：html_url→link、labels 对象数组→字符串、ISO 时间→`%Y-%m-%d %H:%M:%S` |

## communities.yaml 字段参考

| 字段 | 含义 |
|---|---|
| `display_name` | 社区展示名（邮件文案用） |
| `community_repo` | community 仓库 clone 地址 |
| `orgs` | 仓库所属 org 列表（用于目录解析和仓库名校验） |
| `repo_source` | `dir_walk`：遍历 sig/<Sig>/<org>/**/*.yaml；`sig_info`：以 sig-info.yaml 的 repositories 为准（BoostKit 用，因为分片 yaml 滞后） |
| `data_source` | `ipb` / `gitcode_api` |
| `cla_label` / `ci_failed_label` / `wait_update_label` | 状态判定标签；空串表示跳过该项检查 |
| `processed_rate` | `dsapi` 启用周对比；`none` 跳过（dsapi 不支持 boostkit） |
| `skip_sigs` | 跳过的 SIG（如 openEuler 的 Kernel） |
| `mail_subject_*` / `mail_body_*` | 周报邮件标题与正文引导语 |
| `docs_report` | 资料汇总配置块（仅 `docs_statistics.py` 读取），见下 |

`docs_report` 子字段：

| 字段 | 含义 |
|---|---|
| `enabled` | 是否启用资料汇总；关掉后 `docs_statistics.py` 直接退出 |
| `workdir` | 独立运行目录（BoostKit 用 `boostkit-docs/`），使资料 job 与周报 job 可并行 |
| `pr_labels` | 资料 PR 的标签集合。**不要**放 `docs-ci-pipeline-*`：那是仓库级 CI 状态标签 |
| `issue_title_prefix` / `issue_type` | 资料 Issue 的两个信号（标题前缀 / issue_type），取并集 |
| `receivers` | 收件人列表：含 `@` 的条目直接当邮箱，否则当 gitcode_id 去 sig-info 邮箱映射里查 |
| `status_header` / `status_labels` | 资料 PR 报表额外一列的列名与取值：标签 → `{text, color}`，按声明顺序匹配（命中第一个即用），未命中留空 |
| `nickname` | 邮件开头称呼 |
| `subject_pr` / `body_pr` / `subject_issue` / `body_issue` | 两封邮件的标题与正文引导语 |

## 三种测试/验证模式

| 环境变量 | 行为 |
|---|---|
| `DRY_RUN=true` | 不连 SMTP，邮件 HTML 写入 `<workdir>/test_output/`（资料汇总为 `docs_pr_all.html` / `docs_issue_all.html`） |
| `test_reviever_email=<addr>` | 真实发送但全部重定向到该地址，周报最多 3 封 PR + 3 封 Issue，资料汇总为 2 封 |
| `TEST_USER=<gitcode_id>` | 只处理该用户（资料汇总为收件人 key），常配合 DRY_RUN 使用 |

## 已知设计取舍

- **目录隔离**：每个社区一个 `<community>/` 工作目录，community 克隆、data、日志、email_mapping 全在里面，靠 `setup_community()` 的 chdir 实现，代码内全部是相对路径。
- **BoostKit 无 CI 失败检测**：其 CI 标签体系未梳理，`ci_failed_label` 置空跳过。
- **BoostKit 私有仓库缺席**：约 49 个私有仓库对当前 token 也不可见，报表不含这些仓库的 PR/Issue；如需覆盖要给 token 账号加权限。
- **处理率对比仅 openEuler**：dsapi.osinfra.cn 只支持 openEuler，BoostKit 报表对比行为空。
- **资料汇总是社区级邮件**：内容对所有收件人相同，因此不像周报那样按人渲染，只做一次渲染 + 多收件人扇出；邮件控制在 `email_controls.yaml` 里以 `docs_pr` / `docs_issue` 两个类型、单一 `receiver` 角色表达。
