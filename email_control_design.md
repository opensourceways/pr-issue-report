# PR/Issue 汇总邮件重构设计文档

## 1. 背景与目标

### 1.1 背景

当前 `pr_statistics.py` 与 `issue_statistics.py` 通过 `email_whitelist.yaml` 控制邮件发送范围，且存在以下问题：

- 白名单机制是“开关式”的：只有白名单内的 Maintainer 才发送邮件，不够灵活。
- PR 与 Issue 的邮件逻辑分别维护，双重身份（Maintainer + Committer）用户的邮件合并/拆分规则不一致。
- 用户无法自助选择接收内容，例如：
  - 某 Maintainer 不想看作为 Maintainer 的汇总，只想看作为 Committer 的汇总。
  - 某用户不想看 Issue 汇总，只想看 PR 汇总。

### 1.2 目标

1. **开放给所有人**：所有在邮件映射中有邮箱的 Maintainer/Committer 默认都会收到邮件。
2. **PR 与 Issue 分开发送**：
   - 一封 `PR 汇总邮件`
   - 一封 `Issue 汇总邮件`
3. **角色区分清晰**：
   - 纯 Committer：只接收作为 Committer 的汇总部分。
   - Maintainer：在一封邮件中区分“作为 Maintainer 的汇总”和“作为 Committer 的汇总”两个部分。
4. **精确的行为控制文件**：
   - 用新的 `email_controls.yaml` 替代 `email_whitelist.yaml`。
   - 用于处理退订和接收偏好。
   - 不在控制文件中的用户默认接收全部邮件。
5. **避免用户疑惑**：
   - 通过明确的标题、说明文字和空状态提示，让用户清楚自己为什么收到这封邮件、每个部分代表什么角色。

---

## 2. 术语定义

| 术语 | 含义 |
|------|------|
| **Maintainer** | SIG 级别的维护者，需要 review 该 SIG 下仓库的 PR/Issue。 |
| **Committer** | 仓库级别的提交者，需要处理该仓库的 PR/Issue。 |
| **双重身份用户** | 同时是 Maintainer 和 Committer 的用户。 |
| **邮件类型（mail_type）** | `pr` 或 `issue`，分别对应 PR 汇总邮件和 Issue 汇总邮件。 |
| **角色（role）** | `maintainer` 或 `committer`，表示用户在该邮件类型中以什么身份接收数据。 |
| **控制文件** | `email_controls.yaml`，用于精确控制每个用户接收哪些邮件/角色部分。 |

---

## 3. 控制文件设计

### 3.1 文件位置与命名

```text
email_controls.yaml
```

**默认放在代码仓库根目录**，与现有 `email_whitelist.yaml` 同级。

选择代码仓作为默认存放位置的原因：

- 版本可控，每次修改有提交记录，便于审计。
- 和代码一起部署，Jenkins 构建时自动获取最新配置。
- 修改走 PR/Code Review，流程规范，避免误操作。
- 实现最简单，Jenkins 脚本无需额外下载或挂载外部存储。

**注意事项**：Jenkins 为定时任务（如每周一次），管理员修改控制文件并合并代码后，需要等下一次定时任务才能生效。若需紧急生效，可通过 Jenkins 参数化构建**手动触发**一次任务。

### 3.2 文件格式

采用 YAML，以 `gitee_id` 为一级 key，支持按 `pr` / `issue` 分别控制，每个类型下再按 `maintainer` / `committer` 控制。

```yaml
# email_controls.yaml
# 说明：
# - 不在本文件中的用户，默认接收所有邮件和所有角色部分。
# - 每个字段为布尔值：true 表示接收，false 表示不接收。
# - 可以只声明需要关闭的部分，未声明的默认按 true 处理。

# 用户 A：作为 Maintainer 不想看 PR 汇总，但作为 Committer 仍想看 PR 汇总
#         Issue 两个角色都想看
zhangsan:
  pr:
    maintainer: false
    committer: true
  issue:
    maintainer: true
    committer: true

# 用户 B：完全不想看 Issue 汇总，PR 正常接收
lisi:
  issue: false

# 用户 C：PR 只保留 Committer 部分；Issue 只保留 Maintainer 部分
wangwu:
  pr:
    maintainer: false
    committer: true
  issue:
    maintainer: true
    committer: false

# 用户 D：完全退订所有邮件
zhaoliu:
  pr: false
  issue: false
```

### 3.3 简化写法

为了便于维护，支持以下简化语法：

```yaml
# 关闭整个 PR 邮件
lisi:
  pr: false

# 关闭所有邮件
zhaoliu:
  all: false
```

解析时统一展开为完整结构：

```yaml
lisi:
  pr:
    maintainer: false
    committer: false
  issue:
    maintainer: true
    committer: true

zhaoliu:
  pr:
    maintainer: false
    committer: false
  issue:
    maintainer: false
    committer: false
```

### 3.4 与现有 `email_whitelist.yaml` 的关系

- 本设计生效后，`email_whitelist.yaml` 废弃。
- 迁移策略：
  - 若存在 `email_controls.yaml`，完全使用新逻辑。
  - 若不存在 `email_controls.yaml` 但存在 `email_whitelist.yaml`，可兼容一次：将白名单内用户视为全部接收，白名单外用户视为全部不接收（仅 Maintainer 邮件）。
  - 后续迭代中彻底移除 `email_whitelist.yaml` 相关代码。

---

## 4. 邮件发送策略

### 4.1 总体原则

- 每个邮件类型（PR / Issue）单独发送一封邮件。
- 对每一封邮件，收件人是所有“在该类型下有数据且未被控制文件过滤掉”的 Maintainer/Committer 的并集。
- 同一封邮件内，根据用户角色展示不同部分：
  - 若用户是 Maintainer 且未退订 Maintainer 部分：展示“作为 Maintainer 的 PR/Issue”。
  - 若用户是 Committer 且未退订 Committer 部分：展示“作为 Committer 的 PR/Issue”。
- 若用户两个角色都没有数据或被全部退订：不发送邮件。

### 4.2 场景示例

#### 场景 1：纯 Committer

用户 `tom` 只是 `A` 仓库的 Committer，不是任何 SIG 的 Maintainer。

- PR 邮件：只包含“作为 Committer 的 PR”。
- Issue 邮件：只包含“作为 Committer 的 Issue”。

#### 场景 2：纯 Maintainer

用户 `jerry` 是 `B` SIG 的 Maintainer，不是任何仓库的 Committer。

- PR 邮件：只包含“作为 Maintainer 的 PR”。
- Issue 邮件：只包含“作为 Maintainer 的 Issue”。

#### 场景 3：双重身份用户，未配置控制文件

用户 `alice` 既是 Maintainer 又是 Committer，且不在 `email_controls.yaml` 中。

- PR 邮件：包含两个部分：
  - “作为 Maintainer 的 PR”
  - “作为 Committer 的 PR”
- Issue 邮件：同理。

#### 场景 4：双重身份用户，退订 Maintainer 部分

用户 `bob` 既是 Maintainer 又是 Committer，控制文件：

```yaml
bob:
  pr:
    maintainer: false
    committer: true
  issue:
    maintainer: false
    committer: true
```

- PR 邮件：只包含“作为 Committer 的 PR”。
- Issue 邮件：只包含“作为 Committer 的 Issue”。

#### 场景 5：退订整个 Issue 邮件

用户 `carol` 控制文件：

```yaml
carol:
  issue: false
```

- 只发送 PR 邮件，不发送 Issue 邮件。

---

## 5. 数据结构与处理流程

### 5.1 数据结构

在 `pr_statistics()` 和 `issue_statistics()` 中，仍构建两个字典：

```python
maintainer_dict = {
    'gitee_id_1': [pr_row_1, pr_row_2, ...],
    'gitee_id_2': [pr_row_3, ...],
}

committer_dict = {
    'gitee_id_1': [pr_row_4, ...],
    'gitee_id_3': [pr_row_5, ...],
}
```

### 5.2 控制文件解析

新增通用函数（建议放在 `common.py`）：

```python
def load_email_controls():
    """
    Load email control preferences from email_controls.yaml.
    Return a nested dict: controls[gitee_id][mail_type][role] = bool
    Missing entries default to True (receive everything).
    """
    controls = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: True)))
    if not os.path.exists('email_controls.yaml'):
        return controls
    raw = yaml.safe_load(open('email_controls.yaml', 'r').read()) or {}
    for gitee_id, config in raw.items():
        controls[gitee_id] = expand_controls(config)
    return controls


def expand_controls(config):
    """
    Expand simplified control syntax into full structure.
    """
    result = {
        'pr': {'maintainer': True, 'committer': True},
        'issue': {'maintainer': True, 'committer': True},
    }
    if config is False or config.get('all') is False:
        result['pr']['maintainer'] = False
        result['pr']['committer'] = False
        result['issue']['maintainer'] = False
        result['issue']['committer'] = False
        return result
    for mail_type in ('pr', 'issue'):
        if mail_type not in config:
            continue
        value = config[mail_type]
        if value is False:
            result[mail_type]['maintainer'] = False
            result[mail_type]['committer'] = False
        elif isinstance(value, dict):
            for role in ('maintainer', 'committer'):
                if role in value:
                    result[mail_type][role] = bool(value[role])
    return result
```

### 5.3 发送流程（以 PR 为例）

```python
# 1. 加载控制配置
controls = load_email_controls()

# 2. 确定所有潜在收件人
all_receivers = set(maintainer_pr_dict.keys()) | set(committer_pr_dict.keys())

# 3. 遍历每个收件人
for receiver in sorted(all_receivers):
    # 判断该用户在该邮件类型下应接收哪些角色部分
    want_maintainer = receiver in maintainer_pr_dict and controls[receiver]['pr']['maintainer']
    want_committer = receiver in committer_pr_dict and controls[receiver]['pr']['committer']

    if not want_maintainer and not want_committer:
        log.logger.info('Skipping PR email for {} due to controls'.format(receiver))
        continue

    # 4. 生成对应 HTML
    html_parts = []
    if want_maintainer:
        html_m = send_pr_email(maintainer_pr_dict[receiver], receiver, 'maintainer', compare_dict)
        if html_m:
            html_parts.append(('作为 Maintainer 的 PR', html_m))
    if want_committer:
        html_c = send_pr_email(committer_pr_dict[receiver], receiver, 'committer', compare_dict)
        if html_c:
            html_parts.append(('作为 Committer 的 PR', html_c))

    if not html_parts:
        continue

    # 5. 合并 HTML 部分
    final_html = merge_html_parts(html_parts, receiver, 'pr')

    # 6. 发送邮件
    send_email_with_html(final_html, receiver, 'openEuler 待处理PR汇总')
```

### 5.4 HTML 合并规则

合并多部分 HTML 时：

1. 以 Maintainer 部分（如果存在）作为基础 HTML。
2. 在每个部分前插入清晰的小标题 `<h3>`。
3. 若只有一个部分，不额外加角色标题（避免纯 Committer 看到“作为 Committer 的 PR”这种冗余标题）。
4. 若某部分为空（无 PR/Issue），跳过该部分。

示例合并后结构：

```html
<body>
  <p>Dear alice,</p>
  <p>以下是您参与 openEuler 社区的待处理 PR 汇总。</p>

  <h3 style="margin-top:30px">作为 Maintainer 的 PR</h3>
  <!-- Maintainer 表格 -->

  <h3 style="margin-top:30px">作为 Committer 的 PR</h3>
  <!-- Committer 表格 -->

  <p style="font-size:12px;color:#666;">
    如不想继续接收此类邮件，请回复本邮件并注明“退订”。
  </p>
</body>
```

---

## 6. 避免用户疑惑的文案设计

### 6.1 邮件顶部说明

```html
<p>Dear {nickname},</p>
<p>以下是您参与 openEuler 社区的待处理 PR 汇总。</p>
<p>本邮件根据您在社区中的 Maintainer/Committer 身份生成，不同部分代表您在不同角色下需要关注的 PR。</p>
```

### 6.2 角色部分标题

| 场景 | 标题 |
|------|------|
| 只有 Maintainer 部分 | 直接展示表格，不加角色标题 |
| 只有 Committer 部分 | 直接展示表格，不加角色标题 |
| 两者都有 | 分别加 `<h3>作为 Maintainer 的 PR</h3>` 和 `<h3>作为 Committer 的 PR</h3>` |

### 6.3 空状态说明

若某角色部分无数据，直接不展示该部分，避免用户看到空表格。顶部说明已解释邮件来源。

### 6.4 退订说明

邮件底部统一加：

```html
<p style="font-size:12px;color:#666;">
  如需退订，请直接回复本邮件，或发送邮件至 
  <b>huanglei227@h-partners.com</b>，并注明退订类型：<br>
  • 退订 PR 汇总<br>
  • 退订 Issue 汇总<br>
  • 只退订作为 Maintainer 的部分<br>
  • 只退订作为 Committer 的部分<br>
  • 完全退订所有邮件<br><br>
  管理员将在 1-2 个工作日内处理。
</p>
```

### 6.5 退订邮件接收方式

- 发送邮件时通过 `Reply-To` 头将退订回复指向 `huanglei227@h-partners.com`。
- 用户也可以直接发送新邮件到该地址申请退订。
- 退订请求由管理员人工处理，确认后修改 `email_controls.yaml` 并提交代码。

---

## 7. 控制文件与退订的衔接

### 7.1 当前阶段：人工维护

初期由管理员根据用户反馈手动编辑 `email_controls.yaml`：

- 退订请求统一发送到 `huanglei227@h-partners.com`。
- 用户回复邮件说“退订 Issue 汇总” → 管理员添加 `issue: false`。
- 用户说“不想看作为 Maintainer 的 PR” → 管理员添加 `pr.maintainer: false`。
- 用户说“完全退订” → 管理员添加 `all: false`。

处理时效：管理员在收到退订邮件后 1-2 个工作日内完成修改并回复用户。

### 7.2 未来可选：自助退订

后续可在一键退订服务中，让用户选择退订类型：

- 退订所有 PR 汇总
- 退订所有 Issue 汇总
- 只退订作为 Maintainer 的部分
- 只退订作为 Committer 的部分
- 完全退订

退订服务确认后自动更新 `email_controls.yaml` 或数据库。

---

## 8. 与现有代码的差异

| 现有代码（lei_dev） | 新设计 |
|--------------------|--------|
| `email_whitelist.yaml` 控制是否发送 Maintainer 邮件 | `email_controls.yaml` 精确控制每个用户、每种邮件类型、每个角色 |
| PR 中 Maintainer 和 Committer 可能合并成一封邮件 | PR 固定发一封邮件，内部按角色分部分；纯 Committer 只收到 Committer 部分 |
| Issue 逻辑与 PR 类似，也是合并 | Issue 同样固定发一封邮件，内部按角色分部分 |
| 白名单外用户默认不收 | 所有有邮箱映射的用户默认接收全部 |
| 邮件标题固定为 `openEuler 待处理PR汇总` 或 `（Committer）` | PR 统一为 `openEuler 待处理PR汇总`，Issue 统一为 `openEuler 待处理Issue汇总` |

---

## 9. 实施计划

### 阶段 1：控制文件解析与公共函数

1. 在 `common.py` 中实现 `load_email_controls()` 和 `expand_controls()`。
2. 增加 `should_send(gitee_id, mail_type, role, controls)` 辅助函数。
3. 增加 `merge_html_parts()` 通用 HTML 合并函数。
4. 修改 `send_email()` 支持 `Reply-To` 头，默认指向 `huanglei227@h-partners.com`。

### 阶段 2：PR 邮件逻辑重构

1. 修改 `pr_statistics.py`：
   - 删除 `committer_extras` 合并逻辑。
   - 改为遍历 `maintainer_pr_dict` 和 `committer_pr_dict` 的并集。
   - 根据控制文件决定展示哪些角色部分。
   - 合并 HTML 并发送一封 PR 邮件。

### 阶段 3：Issue 邮件逻辑重构

1. 修改 `issue_statistics.py`：
   - 与 PR 相同改造。

### 阶段 4：测试与上线

1. 测试场景：
   - 纯 Committer 只收到 Committer 部分。
   - 纯 Maintainer 只收到 Maintainer 部分。
   - 双重身份用户收到两部分。
   - 控制文件生效：退订某角色后不再收到该部分。
   - 无控制文件时默认全部接收。
2. 创建 `email_controls.yaml` 模板并替换 `email_whitelist.yaml`。
3. 上线并观察用户反馈。

### 阶段 5：Jenkins 参数与脚本调整

由于控制文件默认放在代码仓库，Jenkins 脚本改动较小，只需增加测试参数支持：

1. **参数化构建配置**：

   | 参数名 | 类型 | 默认值 | 说明 |
   |--------|------|--------|------|
   | `DRY_RUN` | Boolean | `false` | 只生成本地 HTML，不发送邮件 |
   | `TEST_USER` | String | 空 | 只处理该 gitee_id 的邮件 |
   | `test_reviever_email` | String | 空 | 测试模式重定向邮箱 |
   | `EMAIL_CONTROLS_PATH` | String | `email_controls.yaml` | 控制文件路径，默认仓库根目录 |
   | `email_reply_to` | String | `huanglei227@h-partners.com` | 退订回复邮箱，写入 `Reply-To` 头 |

2. **脚本调整**：

   ```bash
   # Jenkins job script for openEuler PR statistics weekly report

   # Initialize or update git repository
   if [[ ! -d .git ]]; then
       git init &> /dev/null
       git remote add origin https://github.com/opensourceways/pr-issue-report.git
       git config http.retry 2
       git fetch --depth=1 origin lei_dev || exit 1
       git checkout lei_dev
   else
       git remote set-url origin https://github.com/opensourceways/pr-issue-report.git
       git config http.retry 2
       git fetch origin --recurse-submodules=no --progress --prune
       git reset --hard origin/lei_dev
   fi

   # Load Python 3.11 environment
   source python3.11.env.sh

   # Install dependencies with mirror
   pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q

   # Test mode settings
   if [[ "$DRY_RUN" == "true" ]]; then
       rm -rf test_output
       mkdir -p test_output
   fi

   # Reply-To for unsubscribe emails
   export email_reply_to="${email_reply_to:-huanglei227@h-partners.com}"

   python3 pr_statistics.py
   python3 issue_statistics.py

   # Archive test output when DRY_RUN
   if [[ "$DRY_RUN" == "true" ]]; then
       tar -czf test_output.tar.gz test_output/
       echo "Test HTML files generated in test_output/"
   fi
   ```

3. **手动触发机制**：
   - 管理员修改 `email_controls.yaml` 并合并代码后，如需紧急生效，可手动触发一次 Jenkins 构建。
   - 常规情况下仍按定时任务执行。

---

## 10. 测试模式设计

### 10.1 现有测试模式回顾

现有测试模式通过环境变量 `test_reviever_email` 开启：

```python
test_email = os.getenv('test_reviever_email', '').strip()
test_mode = bool(test_email)
```

行为：

- 所有邮件重定向到该测试地址。
- 最多发送 3 封 PR + 3 封 Issue。
- 跳过白名单拦截。

不足：

- 无法验证控制文件逻辑。
- 无法针对单个用户调试。
- 测试时仍会真实发送邮件。

### 10.2 升级后的测试模式

新设计保留原有测试邮箱机制，并增加以下测试开关。

#### 开关 1：测试邮箱 `test_reviever_email`

保持不变，用于真实 SMTP 发送测试。

```bash
export test_reviever_email="test@example.com"
```

行为：

- 所有邮件重定向到该地址。
- 最多发送 3 封 PR + 3 封 Issue。
- 仍应用 `email_controls.yaml` 过滤逻辑（与生产行为一致，便于验证）。

#### 开关 2：本地生成模式 `DRY_RUN`

```bash
export DRY_RUN=true
```

行为：

- 正常执行数据收集、控制文件过滤、HTML 生成。
- **不调用 SMTP 发送**，而是把最终 HTML 保存到 `test_output/` 目录。
- 文件名格式：
  - `test_output/pr_{gitee_id}.html`
  - `test_output/issue_{gitee_id}.html`

适用场景：验证控制文件逻辑和邮件内容，无需外部邮箱。

#### 开关 3：单用户测试 `TEST_USER`

```bash
export TEST_USER="zhangsan"
```

行为：

- 只处理该 `gitee_id` 对应的邮件，其他用户全部跳过。
- 可与 `test_reviever_email` 或 `DRY_RUN` 组合使用。

适用场景：调试某个具体用户的接收偏好。

#### 开关 4：测试控制文件 `EMAIL_CONTROLS_PATH`

```bash
export EMAIL_CONTROLS_PATH="/path/to/test_email_controls.yaml"
```

行为：

- 指定一个专门用于测试的控制文件路径。
- 避免污染生产 `email_controls.yaml`。

测试文件示例：

```yaml
# test_email_controls.yaml
alice:
  pr:
    maintainer: true
    committer: false
  issue:
    maintainer: false
    committer: true

bob:
  issue: false

charlie:
  all: false
```

### 10.3 测试场景与对应开关

| 测试目标 | 推荐开关组合 |
|---------|-------------|
| 验证 SMTP 连通性 | `test_reviever_email=test@example.com` |
| 验证控制文件过滤 | `DRY_RUN=true EMAIL_CONTROLS_PATH=test_email_controls.yaml` |
| 验证单个用户邮件内容 | `DRY_RUN=true TEST_USER=zhangsan` |
| 全链路回归测试 | `test_reviever_email=test@example.com EMAIL_CONTROLS_PATH=test_email_controls.yaml` |

### 10.4 实现要点

1. `load_email_controls()` 读取 `EMAIL_CONTROLS_PATH` 环境变量，未设置时默认 `email_controls.yaml`。
2. 发送前判断 `DRY_RUN`，若为 true 则写入本地文件而非发送 SMTP。
3. `TEST_USER` 在遍历收件人时提前过滤。
4. `MAX_EMAILS` 限制在测试邮箱模式下仍然生效。

---

## 11. 风险与注意事项

| 风险 | 说明 | 缓解措施 |
|------|------|----------|
| 邮件总量增加 | 原来双重身份用户可能只收 1 封 PR + 1 封 Issue；现在固定 1 封 PR + 1 封 Issue，总体可控 | 这是预期行为；用户可通过控制文件减少 |
| 用户不理解角色划分 | 用户可能疑惑为什么收到两部分 | 邮件顶部加说明，角色标题清晰 |
| 控制文件误配置 | 管理员手动编辑可能写错格式 | YAML 解析失败时给出明确报错，并默认不发送 |
| 与旧白名单冲突 | 同时存在 `email_whitelist.yaml` 和 `email_controls.yaml` 时行为混乱 | 新代码优先使用 `email_controls.yaml`，旧文件逐步废弃 |
| 邮件大小问题 | 某 Maintainer 覆盖 SIG 多，单封 PR 邮件仍可能很大 | 后续可考虑按 SIG 拆分或摘要+链接方案 |
| 测试误发真实邮件 | 测试时忘记设置 `test_reviever_email` 或 `DRY_RUN` | 在 CI/Jenkins 测试任务中强制要求 `DRY_RUN=true`，生产任务才允许真实发送 |

---

## 12. 结论

本设计通过以下方式满足需求：

1. **两封邮件**：PR 汇总邮件、Issue 汇总邮件，互不干扰。
2. **角色区分**：每封邮件内按 Maintainer/Committer 分部分展示；纯 Committer 只展示 Committer 部分。
3. **精确控制**：用 `email_controls.yaml` 替代白名单，支持按用户/邮件类型/角色三级控制。
4. **默认开放**：不在控制文件中的用户默认接收全部邮件。
5. **体验清晰**：通过顶部说明、角色标题、空状态跳过，避免用户疑惑。

这是当前阶段兼顾实现成本、用户体验和可维护性的最佳方案。
