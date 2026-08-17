# pr-issue-report

每周定时统计 openEuler / BoostKit 社区的待处理 PR 和 Issue，按 maintainer/committer 分组，生成报表邮件发送。目标社区由 `COMMUNITY` 环境变量选择（见「多社区支持」）。

## 项目结构

```
├── common.py                # 共享基础设施（日志、SIG解析、邮件映射、Excel生成、SMTP、GitCode API）
├── pr_statistics.py         # PR 统计入口
├── issue_statistics.py      # Issue 统计入口
├── communities.yaml         # 多社区配置（openeuler / boostkit）
├── email_controls.yaml      # 邮件接收偏好控制文件
├── jenkins_job_openeuler.sh # openEuler 周报 Jenkins 脚本
├── jenkins_job_boostkit.sh  # BoostKit 周报 Jenkins 脚本
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
| `test_reviever_email` | 测试模式：重定向到该地址，仅发 3 封 PR + 3 封 Issue |
| `DRY_RUN` | 本地生成模式：只生成 HTML 到 `<community>/test_output/`，不发送邮件 |
| `TEST_USER` | 单用户测试：只处理该 gitee_id 的邮件 |
| `EMAIL_CONTROLS_PATH` | 控制文件路径，默认 `email_controls.yaml` |
| `COMMUNITY` | 目标社区，默认 `openeuler`，可选 `boostkit`（见 communities.yaml） |
| `GITCODE_TOKEN` | GitCode API 访问令牌，boostkit 社区必需 |

## 多社区支持

- 活跃社区由 `COMMUNITY` 环境变量选择（默认 openEuler），社区差异配置集中在 `communities.yaml`。
- 每个社区在独立子目录运行：`setup_community()` 会创建并 chdir 到 `<community>/`，其下的 `community/`、`data/`、`email_mapping.yaml`、`statistics.log` 均按社区隔离，互不干扰。
- openEuler 的 PR/Issue 数据来自聚合接口 `ipb.osinfra.cn`；BoostKit 逐仓库调用 GitCode API（需要 `GITCODE_TOKEN`），私有仓库的 403/404 只记 warning 跳过，不中断任务。
- 处理率周对比（dsapi.osinfra.cn）仅 openEuler 支持；BoostKit 报表中对比信息为空。
- BoostKit 的 CI 失败标签未知，第一版不检测（`ci_failed_label` 为空串表示跳过该检查）。

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
```

支持简化写法：`pr: false`、`issue: false`、`all: false`。

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
```

每次运行的产物（克隆的 community 仓库、中间数据、日志）都在 `<community>/` 子目录下，与代码和其他社区隔离。

## 邮件规则

- 固定两封邮件：一封 PR 汇总、一封 Issue 汇总。
- 每封邮件内部按角色区分：Maintainer 部分和 Committer 部分。
- 纯 Committer 只展示 Committer 部分；双重身份用户展示两个部分。
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
```
