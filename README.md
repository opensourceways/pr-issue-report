# pr-issue-report

每周定时统计 openEuler 社区的待处理 PR 和 Issue，按 maintainer/committer 分组，生成报表邮件发送。

## 项目结构

```
├── common.py                # 共享基础设施（日志、SIG解析、邮件映射、Excel生成、SMTP）
├── pr_statistics.py         # PR 统计入口
├── issue_statistics.py      # Issue 统计入口
├── email_whitelist.yaml     # 白名单（gitee_id 列表）
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
| `test_reviever_email` | 测试模式：重定向到该地址，仅发 3 封 PR + 3 封 Issue |

## 白名单

`email_whitelist.yaml` 控制邮件投递范围：

| | Maintainer | Committer |
|---|---|---|
| **PR 邮件** | 白名单拦截 | 不拦截 |
| **Issue 邮件** | 白名单拦截 | 白名单拦截 |

白名单文件存在即为启用，不存在则不拦截。

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

- 维护者 + 仓库提交者：一封邮件，上下两个表分别展示
- 纯维护者或纯提交者：一封邮件
- Kernel SIG 跳过（由 hulk_robot_test 独立处理）
- 测试阶段：设置 `test_reviever_email` 后精确发送 3 封 PR + 3 封 Issue 到该测试地址
