# pr-issue-report

每周定时统计 openEuler 社区的待处理 PR 和 Issue，按 maintainer/committer 分组，生成报表邮件发送。

## 项目结构

```
├── common.py                # 共享基础设施（日志、SIG解析、邮件映射、Excel生成、SMTP）
├── pr_statistics.py         # PR 统计入口
├── issue_statistics.py      # Issue 统计入口
├── email_controls.yaml      # 邮件接收偏好控制文件
├── requirements.txt         # Python 依赖
├── python3.11.env.sh        # Python 3.11 venv 环境
├── Dockerfile               # 容器化部署
└── hulk_robot_test/         # Kernel SIG 专用系统（独立项目，仅供参考）
```

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
| `DRY_RUN` | 本地生成模式：只生成 HTML 到 `test_output/`，不发送邮件 |
| `TEST_USER` | 单用户测试：只处理该 gitee_id 的邮件 |
| `EMAIL_CONTROLS_PATH` | 控制文件路径，默认 `email_controls.yaml` |

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

## 运行

```bash
# 安装依赖
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# PR 统计
python3 pr_statistics.py

# Issue 统计
python3 issue_statistics.py
```

## 邮件规则

- 固定两封邮件：一封 PR 汇总、一封 Issue 汇总。
- 每封邮件内部按角色区分：Maintainer 部分和 Committer 部分。
- 纯 Committer 只展示 Committer 部分；双重身份用户展示两个部分。
- 邮件底部提供退订说明，退订请求发送至 `huanglei227@h-partners.com`。
- Kernel SIG 跳过（由 hulk_robot_test 独立处理）。

## 测试

```bash
# 运行单元测试
python3 -m pytest tests/ -v

# 本地生成模式（不发送邮件）
DRY_RUN=true python3 pr_statistics.py

# 单用户测试
TEST_USER=zhangsan DRY_RUN=true python3 pr_statistics.py
```
