# pr-issue-report

每周定时统计 openEuler / BoostKit 社区的待处理 PR 和 Issue，按 maintainer/committer 分组，生成报表邮件发送。目标社区由 `COMMUNITY` 环境变量选择（见「多社区支持」）。

另有「资料汇总」流水线：把全社区**资料相关**（文档）的待处理 PR / Issue 汇总成两封社区级邮件，直接发给资料经理名单，不按 maintainer/committer 分发。入口是 `docs_statistics.py`。

## 项目结构

```
├── common.py                # 共享基础设施（日志、SIG解析、邮件映射、Excel生成、SMTP、GitCode API）
├── pr_statistics.py         # PR 统计入口
├── issue_statistics.py      # Issue 统计入口
├── docs_statistics.py       # 资料汇总入口（社区级，发资料经理）
├── communities.yaml         # 多社区配置（openeuler / boostkit）
├── email_controls.yaml      # 邮件接收偏好控制文件
├── jenkins_job_openeuler.sh # openEuler 周报 Jenkins 脚本
├── jenkins_job_boostkit.sh  # BoostKit 周报 Jenkins 脚本
├── jenkins_job_boostkit_docs.sh # BoostKit 资料汇总 Jenkins 脚本
├── requirements.txt         # Python 依赖
├── python3.11.env.sh        # Python 3.11 venv 环境
├── Dockerfile               # 容器化部署
├── docs/                    # 维护者文档（架构说明、部署与运维）
└── tests/                   # 单元测试（pytest）
```

## 文档

- [docs/architecture.md](docs/architecture.md) — 架构与数据流、模块说明、`communities.yaml` 字段参考（**新人维护从这里读起**）
- [docs/deployment.md](docs/deployment.md) — Jenkins 部署配置、上线验证流程、日志与常见问题

## 环境变量

| 变量 | 说明 |
|---|---|
| `email_username` | SMTP 用户名 |
| `email_password` | SMTP 密码 |
| `smtp_host` | SMTP 服务器 |
| `smtp_port` | 端口，默认 465 |
| `email_sender` | 发件人地址 |
| `email_reply_to` | 退订回复邮箱，默认 `huanglei227@h-partners.com` |
| `test_reviever_email` | 测试模式：重定向到该地址，仅发 3 封 PR + 3 封 Issue（资料汇总为 2 封） |
| `DRY_RUN` | 本地生成模式：只生成 HTML 到 `<workdir>/test_output/`，不发送邮件 |
| `TEST_USER` | 单用户测试：只处理该 gitee_id 的邮件（资料汇总为收件人 key） |
| `EMAIL_CONTROLS_PATH` | 控制文件路径，默认 `email_controls.yaml` |
| `COMMUNITY` | 目标社区，默认 `openeuler`，可选 `boostkit`（见 communities.yaml；资料汇总同样用 `boostkit`） |
| `GITCODE_TOKEN` | GitCode API 访问令牌，boostkit 周报与资料汇总 job 必需 |

## 多社区支持

- 活跃社区由 `COMMUNITY` 环境变量选择（默认 openEuler），社区差异配置集中在 `communities.yaml`。
- 每个社区在独立子目录运行：`setup_community()` 会创建并 chdir 到 `<community>/`，其下的 `community/`、`data/`、`email_mapping.yaml`、`statistics.log` 均按社区隔离，互不干扰。
- openEuler 的 PR/Issue 数据来自聚合接口 `ipb.osinfra.cn`；BoostKit 逐仓库调用 GitCode API（需要 `GITCODE_TOKEN`），私有仓库的 403/404 只记 warning 跳过，不中断任务。
- 处理率周对比（dsapi.osinfra.cn）仅 openEuler 支持；BoostKit 报表中对比信息为空。
- BoostKit 的 CI 失败标签未知，第一版不检测（`ci_failed_label` 为空串表示跳过该检查）。
- 「资料汇总」是同一个社区的第二条流水线（入口 `docs_statistics.py`），不是新社区：`COMMUNITY` 仍是 `boostkit`，但运行目录由 `docs_report.workdir` 指定为 `boostkit-docs/`，因此可以和周报 job 并行跑而互不干扰。社区级资料配置（口径、收件人、邮件文案）都在 `communities.yaml` 的 `docs_report` 块里。

## 邮件接收控制

`email_controls.yaml` 精确控制每个用户接收哪些邮件和角色部分：

```yaml
# 不在本文件中的用户默认接收全部邮件
zhangsan:
  pr:
    maintainer: false   # 不收 PR 的 Maintainer 部分
    committer: true     # 收 PR 的 Committer 部分
  issue:
    maintainer: true
    committer: true

lisi:
  issue: false          # 完全不看 Issue 汇总

wangwu:
  all: false            # 完全退订所有邮件

# 资料汇总（只发给 communities.yaml 里 docs_report.receivers 配的收件人）
# 键既可以是收件人的 gitcode_id，也可以是配置里直接写的邮箱地址
docs_manager:
  docs_pr: false        # 退订「资料相关 PR 汇总」
  docs_issue: false     # 退订「资料相关 Issue 汇总」
```

支持简化写法：`pr: false`、`issue: false`、`docs_pr: false`、`docs_issue: false`、`all: false`。
资料汇总没有 Maintainer/Committer 之分，只有单一 `receiver` 角色。

同一个人可能同时是两个社区的 SIG 成员。默认配置对所有社区生效；如需按社区区别退订，用 `communities` 覆盖（该社区的配置**整体覆盖**基础配置，未声明的部分默认接收）：

```yaml
lisi:
  issue: false          # 基础配置：所有社区都不看 Issue 汇总
  communities:
    boostkit:
      all: false        # BoostKit 完全退订；openEuler 仍按基础配置
```

## 运行

```bash
# 安装依赖
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# PR 统计（默认 openEuler 社区）
python3 pr_statistics.py

# Issue 统计
python3 issue_statistics.py

# BoostKit 社区（需要 GITCODE_TOKEN）
COMMUNITY=boostkit GITCODE_TOKEN=xxx python3 pr_statistics.py
COMMUNITY=boostkit GITCODE_TOKEN=xxx python3 issue_statistics.py

# BoostKit 资料汇总（社区级；一次运行发两封：资料 PR 汇总 + 资料 Issue 汇总）
COMMUNITY=boostkit GITCODE_TOKEN=xxx python3 docs_statistics.py
```

每次运行的产物（克隆的 community 仓库、中间数据、日志）都在各自的运行目录下，与代码和其他流水线隔离：周报在 `<community>/`，资料汇总在 `docs_report.workdir`（BoostKit 是 `boostkit-docs/`）。

## 邮件规则

- 周报：固定两封邮件（PR 汇总、Issue 汇总），每封按 reviewer 分发；每封内部按角色区分 Maintainer 部分和 Committer 部分。
- 纯 Committer 只展示 Committer 部分；双重身份用户展示两个部分。
- 资料汇总：固定两封邮件（资料相关 PR 汇总、资料相关 Issue 汇总），**社区级**、不按角色分部分，只发给 `docs_report.receivers` 配置的名单（gitcode_id 或邮箱）。
- 资料口径：PR 带 `docs_report.pr_labels` 中任一标签即算（`need-doc-sig-review` / `doc-sig-reviewed` / `sig/doc` / `ai-docs-only` 等）；Issue 是标题以 `[资料]:` 开头**或** `issue_type` 为「资料」。注意 `docs-ci-pipeline-*` 是仓库级 CI 状态标签，不作为资料信号。
- 资料 PR 报表比周报多一列**「资料状态」**，直接体现资料标签状态，一眼看出哪些还需要处理：`待资料评审`（黄色，还等资料 SIG 评审）、`资料已评审`（绿色，已通过）。文案与底色在 `docs_report.status_labels` 配置；Issue 报表没有这一列（Issue 没有资料评审标签）。
- 邮件底部提供退订说明，退订请求发送至 `huanglei227@h-partners.com`。
- openEuler 的 Kernel SIG 默认跳过（由 `communities.yaml` 的 `skip_sigs` 配置控制）。

## 测试

```bash
# 运行单元测试
python3 -m pytest tests/ -v

# 本地生成模式（不发送邮件）
DRY_RUN=true python3 pr_statistics.py

# 单用户测试
TEST_USER=zhangsan DRY_RUN=true python3 pr_statistics.py

# 资料汇总：本地生成 HTML（产物在 boostkit-docs/test_output/docs_{pr,issue}_all.html）
COMMUNITY=boostkit GITCODE_TOKEN=xxx DRY_RUN=true python3 docs_statistics.py
```
