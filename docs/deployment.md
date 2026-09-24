# 部署与运维

## 部署形态

项目以 **Jenkins 定时 job** 方式运行，每个社区一个独立 job，互不影响。也支持 Docker（见文末），但当前生产环境用的是 Jenkins。

## Jenkins job 清单

Jenkins 实例：<https://ci.openeuler.openatom.cn>，现有 job：
<https://ci.openeuler.openatom.cn/job/Infra/job/pr-statistics-report/job/pr-statistics-community/>
（需要相应权限；调度 cron、凭据注入方式等以该 job 配置页为准，新人先找管理员开权限，再对照本文档查看。）

| Job | 执行脚本 | 社区 / 运行目录 | 调度 |
|---|---|---|---|
| pr-statistics-community（现有，openEuler） | `jenkins_job_openeuler.sh` | openeuler / `openeuler/` | 见 job 配置页 |
| BoostKit 周报（待新建） | `jenkins_job_boostkit.sh` | boostkit / `boostkit/` | 建议与 openEuler 错开 |
| BoostKit 资料汇总（待新建） | `jenkins_job_boostkit_docs.sh` | boostkit / `boostkit-docs/` | 建议与上面两个错开 |

## job 脚本在做什么

三个脚本结构完全一致，周报脚本仅 `COMMUNITY` 取值不同；资料汇总脚本额外固定 `WORKDIR=boostkit-docs`（对应 `docs_report.workdir`），运行目录与周报隔离，可并行：

1. **代码自更新**：job 工作区内 `git init/fetch/reset --hard` 到本项目（`https://github.com/opensourceways/pr-issue-report.git`）的 `lei_dev` 分支（脚本顶部 `REPO_URL`/`BRANCH` 两个变量控制）。因此 Jenkins job 的构建步骤里**直接粘贴脚本全文**即可，不需要预先配置 SCM。注意 Jenkins 节点需能访问 github.com。
2. **Python 环境**:`source python3.11.env.sh`，在节点上建 `.venv`（python3.11 + requirements.txt）。依赖节点预装 python3.11；PyPI 走清华镜像（节点直连 PyPI 不通）。
3. **运行统计**：周报依次跑 `python3 pr_statistics.py` 和 `python3 issue_statistics.py`；资料汇总只跑 `python3 docs_statistics.py`（一次运行发两封邮件）。
4. **DRY_RUN 时**：清理并重新生成运行目录下的 `test_output/`，打包为 `test_output.tar.gz` 供构建产物归档。

## Jenkins 上需要配置什么

### 环境变量 / 凭据

| 名称 | 配置方式 | 说明 |
|---|---|---|
| `email_username` / `email_password` | 凭据注入（方式参照现有 job 配置页） | SMTP 认证 |
| `smtp_host` / `smtp_port` | 环境变量 | SMTP 服务器，465 走 SSL，其他端口走 STARTTLS |
| `email_sender` | 环境变量 | 发件人地址 |
| `email_reply_to` | 环境变量 | 退订回复邮箱，默认 `huanglei227@h-partners.com` |
| `GITCODE_TOKEN` | 凭据注入（Secret text，已配置在 job 中，参照配置页） | BoostKit 周报与资料汇总 job 都需要；GitCode 个人访问令牌 |
| `DRY_RUN` / `test_reviever_email` / `TEST_USER` | 构建参数（可选） | 验证模式，见下节 |

> 注意：openEuler 的存量 job 执行命令要从旧的 `jenkins_job.sh` 改为 `jenkins_job_openeuler.sh`（脚本已改名）。

### 新建一个社区 job 的步骤（以 BoostKit 为例）

1. 复制现有 openEuler job，或新建自由风格 job。
2. 构建步骤粘贴 `jenkins_job_boostkit.sh` 全文。
3. 配置 SMTP 相关环境变量（同上表）。
4. 用 "Secret text" 凭据绑定注入 `GITCODE_TOKEN`。
5. 配置定时调度（Build periodically）。
6. 按下节流程验证后再放开真实发送。

## 上线验证流程（新社区/大改动必走）

```
第 1 步：DRY_RUN=true 构建一次
         → 归档 test_output.tar.gz，人工抽查 HTML：
           SIG 分组是否正确、仓库/链接能否点开、状态标注是否合理

第 2 步：test_reviever_email=<你的邮箱> 构建一次
         → 真实走 SMTP，但全部重定向给你，最多 3+3 封
           （资料汇总 job 为 2 封：资料 PR 汇总 + 资料 Issue 汇总）
         → 检查邮件客户端渲染效果

第 3 步：去掉测试参数，正式跑
         → 次日抽查 statistics.log 确认无异常
```

单用户排查：`TEST_USER=<gitcode_id> DRY_RUN=true`，只看某一个人的报表。

## 日志与产物

| 位置 | 内容 |
|---|---|
| `<workdir>/statistics.log` | 运行日志（按天滚动，保留 3 份），Jenkins 工作区内。周报在 `<community>/`，资料汇总在 `docs_report.workdir`（`boostkit-docs/`） |
| `<workdir>/email_mapping.yaml` | 当次运行解析出的 gitcode_id → 邮箱映射 |
| `<workdir>/data/` | 中间产物 CSV/XLSX/HTML（每次运行重建） |
| `<workdir>/test_output/` | DRY_RUN 的邮件 HTML |

## 常见问题

- **日志里大量 `Skip pulls boostkit/xxx: HTTP 403`**：正常。约 49 个私有仓库当前 token 不可见，已跳过。要覆盖需给 token 账号加仓库成员权限。
- **BoostKit 报表没有"PR处理率"行**：正常。dsapi.osinfra.cn 不支持 boostkit，配置为 `processed_rate: none`。
- **某 SIG 整组缺失**：先看该 SIG 的 `sig-info.yaml` 是否存在且格式正确（BoostKit 以它为仓库清单来源，`repo_source: sig_info`）。
- **某人收不到邮件**：按序排查——① 其 gitcode_id 是否在 `<community>/email_mapping.yaml` 中且邮箱非空；② `email_controls.yaml` 是否配置了退订；③ 日志中是否有 `does not match any email address` 警告（说明 sig-info.yaml 里没填邮箱）。
- **GitCode API 报错/限流**：确认 `GITCODE_TOKEN` 有效；实测全量 320 次调用约 2 分钟，无 429。资料汇总 job 会各自再跑一轮全量抓取，所以两个 BoostKit job 建议错开调度，避免同时打 API。
- **资料汇总收不到邮件**：按序排查——① `communities.yaml` 的 `docs_report.receivers` 是否配了名单（默认是空列表，此时日志里会有 `No doc report receiver could be resolved`）；② 收件人写的是 gitcode_id 时，该 id 是否在 `docs_report.workdir` 下的 `email_mapping.yaml` 里且邮箱非空（不是 SIG 成员就查不到，此时直接在配置里写邮箱）；③ `email_controls.yaml` 是否退订了 `docs_pr` / `docs_issue`；④ 本期确实没有资料相关条目时不会发空邮件，日志里会有 `No doc pr found, skip docs_pr email`。
- **资料汇总里出现了没改文档的 PR**：先看它的标签是否在 `docs_report.pr_labels` 里，再回查该 PR 的改动文件是否匹配 `docs` / `README.md` / `README_en.md`（标签由 robot 按改动路径自动打，路径表和仓库覆盖范围在 robot 配置里维护，不在本仓库）。**不要把 `docs-ci-pipeline-*` 加进 `pr_labels`**：它是仓库级 CI 状态标签，配了资料流水线的仓库里所有 PR 都会带。
- **退订请求处理**：流程由代码决定——每封邮件底部带退订说明，`Reply-To` 指向 `email_reply_to`（默认 `huanglei227@h-partners.com`），用户直接回复邮件并注明退订类型。管理员收到回复后，编辑**仓库根目录**的 `email_controls.yaml`（语法见 README，可精确到 PR/Issue × Maintainer/Committer）。注意必须**提交到 `lei_dev` 分支**才生效：job 每次构建都会 `git reset --hard` 到该分支，工作区里的本地修改会被覆盖。改动随下一次定时构建生效。控制文件路径基于代码所在位置解析（`common.py` 同级），与运行时的社区子目录无关，也可用 `EMAIL_CONTROLS_PATH` 覆盖。

## Docker 部署（备选）

```bash
docker build -t pr-statistics .
docker run -e COMMUNITY=boostkit -e GITCODE_TOKEN=... \
           -e email_username=... -e email_password=... \
           -e smtp_host=... -e smtp_port=... -e email_sender=... \
           pr-statistics
```

注意：镜像 ENTRYPOINT 只跑 `pr_statistics.py`，Issue 统计需另行执行 `python3 issue_statistics.py`，资料汇总需另行执行 `python3 docs_statistics.py`（且需带 `COMMUNITY=boostkit` / `GITCODE_TOKEN`）；容器每次运行是全新环境，无日志/产物持久化（如需挂载卷）。
