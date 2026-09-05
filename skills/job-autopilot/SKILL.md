---
name: job-autopilot
description: >-
  自动求职助手：读取随插件发布的 2027 届秋招职位快照并通过 JDWatch 补充查询，维护本地投递队列，使用浏览器登录招聘网站、填写申请和上传简历，
  必要时通过已授权 Android ADB 设备获取手机验证码，并记录已访问网站与投递结果以防重复。
  用于用户要求找岗、批量投递、继续未完成申请、查看投递进度或打开 Job Autopilot 控制台。
---

# Job Autopilot

把岗位发现、浏览器申请、手机验证码和投递账本组织成一个可恢复、可审计的工作流。

## 运行入口

插件根目录是本文件的上两级目录。所有本地状态操作使用：

```bash
<plugin-root>/scripts/job-autopilot <command>
```

默认数据库为 `~/.job-autopilot/state.db`。不要直接编辑数据库。

## 控制台发起的任务

用户说“继续 Job Autopilot”、“处理前端待办”或从支持 MCP Apps 桥接的控制台发来后续消息时，首先运行：

```bash
<plugin-root>/scripts/job-autopilot run-state
```

当 `state` 为 `requested` 时：

1. 记住 `request_id`、`action` 和 `criteria`，运行 `run-claim`。
2. `discover_and_apply` 按条件找岗并处理队列；`process_queue` 从未完成状态恢复；`parse_resume` 执行下方简历流程。
3. 需要用户回答或操作时用 `run-stop --message "<具体动作>"`。
4. 本轮真正完成后用 `run-complete --message "<简短结果>"`。

独立浏览器中的本地页面通过 Codex App Server 直接创建或恢复一个长期任务，并在页面中转发运行进度及必要确认。App Server 已经为该任务注入本 Skill；先核对 `request_id` 再认领，避免重复执行。支持 `sendFollowUpMessage` 的 MCP Apps 嵌入式 UI 则继续把请求交给当前 Codex 对话。

## 共享实时进展

每次执行本 Skill 都必须把安全、简短的阶段进展写入共享活动流，这样无论任务从独立网页、嵌入式 UI，还是任意 Codex 对话发起，两个前端都能看到同一份进度。

开始时先读取 `run-state`：

- 若状态为 `requested`，使用其中的 `activity_run_id` 作为本轮 `run_id`，并执行 `run-claim`。不要再创建第二条活动。
- 若没有待认领请求，执行下列命令创建活动，记住返回的 `run_id`，后续更新始终使用它：

```bash
<plugin-root>/scripts/job-autopilot activity-start \
  --source codex_conversation \
  --action "discover_and_apply" \
  --label "查找并推进校招岗位" \
  --message "正在检查任务条件"
```

只在实际阶段变化时更新，不要为模型思考或每个点击刷屏。可用阶段依次包括 `preparing`、`discovering`、`screening`、`queueing`、`opening`、`authenticating`、`filling`、`uploading`、`reviewing`、`awaiting_confirmation`、`submitting`。示例：

```bash
<plugin-root>/scripts/job-autopilot activity-update <run-id> \
  --state running --stage discovering --message "正在查询符合条件的岗位"

<plugin-root>/scripts/job-autopilot activity-update <run-id> \
  --state running --stage filling --application-id <application-id> \
  --current 2 --total 8 --message "正在填写第 2 个岗位"
```

需要用户确认、回答新字段或手动处理验证时，先将状态更新为 `waiting`、阶段更新为 `awaiting_confirmation`，消息只说明所需动作；收到回答后再更新为 `running`。直接从 Codex 对话发起的任务结束时执行：

```bash
<plugin-root>/scripts/job-autopilot activity-complete <run-id> --message "本轮任务已完成"
```

失败时增加 `--failed`。若本轮来自控制台请求，则继续使用 `run-stop` / `run-complete`，它们会同步更新同一个活动，不额外创建或完成第二条活动。

活动流是面向用户的安全摘要。严禁写入姓名、手机号、邮箱、简历正文、表单答案、短信内容、验证码、密码、API 密钥、包含秘密的命令、隐藏推理或完整浏览器日志。岗位标题、公司、非敏感阶段、数量和本地 `application-id` 可以记录。验证码等敏感内容即使随后会使用，也不能作为 `--message` 传入。

## 依赖与能力边界

1. 2027 届秋招查询优先使用插件随附的公开职位快照；其他届别、实习或社招查询使用已安装的 `jdwatch-skills` / `@jdwatch/cli` 补充。
2. 网页交互必须使用可用的 Browser 或 Chrome skill，并完整遵守该 skill 的浏览器选择、登录和证据规则。不要自行启动独立 Playwright。
3. ADB 只用于用户自己的、已授权的 Android 设备。验证码仅在登录流程刚刚触发后读取，不保存短信正文或验证码。在向网站输入验证码前遵循 Browser skill 的即时确认规则。
4. 插件不能绕过 CAPTCHA、设备验证、网站风控或使用条款。需要人工处理时暂停并明确告诉用户在哪个页面完成什么。

进行岗位查询时先读 JDWatch 的 `jobs.md`；进行浏览器操作前先读 [browser-workflow.md](references/browser-workflow.md)。

## 每次任务的门禁

开始找岗或投递前：

```bash
<plugin-root>/scripts/job-autopilot status
<plugin-root>/scripts/job-autopilot profile
<plugin-root>/scripts/job-autopilot settings
```

必须确认：

- JDWatch `token_valid` 为 `true`（仅浏览用户给出的现成链接时可不要求 JDWatch）。
- `profile` 中本次表单所需事实已存在；简历路径存在且指向用户指定文件。
- 浏览器可用。
- 手机登录时 ADB 设备状态为 `device`。

缺少姓名、联系方式、学历、毕业年份、工作授权、期望地点等事实时询问用户。禁止猜测或从无关上下文推断。

## 简历解析与动态个人资料

当 `run-state.action` 为 `parse_resume` 或 `resume-import-status.status` 为 `requested` 时：

1. 读取 `resume-import-status` 中的绝对路径，使用 `resume-extract "<path>"` 安全提取文本。
2. 用 AI 理解简历的版面、标题、上下文和时间关系，输出结构化候选字段。不要仅用正则表达式或字面关键词映射。
3. 候选 JSON 中每项包含 `target_key`、`label`、`value`、`aliases`、`scope`、`confidence` 和 `reason`。标准字段用 [profile-fields.md](references/profile-fields.md) 的 key，新概念使用稳定的英文 key。
4. 将候选数组写入临时 JSON 文件，运行 `resume-import-complete <json-file>`。这只会将建议显示到前端，不会更改个人资料。
5. 只有用户在前端点击“确认写入”后，候选字段才会进入资料库。

浏览器填表时，对每个页面字段执行语义匹配：同时考虑标签、帮助文本、选项、所在表单分区、公司与岗位上下文，再与基础字段及 `custom_fields` 的 `label` / `aliases` / `scope` 匹配。

- 语义一致且无冲突时，复用已确认事实。
- 只是字面相似、但语义或时间范围可能不同时，不要自动填写。
- 新问题缺少可验证答案时询问用户，明确告知原问题、你的理解和拟保存范围。用户确认后用 `profile-field-set` 记住，并把该网站的实际问法加入 `aliases`。
- 密码、验证码、银行信息等永不进入动态字段库。身份证号等高敏感信息不做跨站自动复用。

## 发现岗位

当用户查询 2027 届秋招时，先查询随插件导入的本地职位表：

```bash
<plugin-root>/scripts/job-autopilot source-jobs --query "Agent 后端" --limit 200
```

公开快照只收录 `2027届 + 秋招 + 校招`。`source-jobs` 是候选职位库，不等同于已投递队列；用户选中后使用：

```bash
<plugin-root>/scripts/job-autopilot source-job-queue <source-job-id>
```

仓库不包含职位抓取实现。若公开快照无法覆盖用户条件，再根据用户条件执行 JDWatch：

根据用户条件执行：

```bash
<plugin-root>/scripts/job-autopilot discover \
  --keyword "Agent,后端" \
  --location "北京,上海" \
  --rt campus \
  --page-size 20
```

该命令会把结果保存为 `discovered`，并按 JDWatch Job ID 去重。向用户展示标题、公司、地点、通道和远程 Job ID；官网链接只使用 JDWatch 返回的 `job.url`。

筛选后将要处理的条目改为 `queued`：

```bash
<plugin-root>/scripts/job-autopilot update <application-id> --status queued
```

不要自动把不符合届别、地点或岗位方向的结果加入待投队列。

## 投递主流程

对每个岗位按以下顺序执行，不得跳过记录：

1. 用 `check --jdwatch-id ... --url ...` 检查重复。若已是 `submitted`，停止该岗位；若是未完成状态，从记录处恢复。
2. 确保岗位已 `record`，再打开官网 URL。打开后更新为 `opened`，因此“访问过的网站”会在账本中出现。
3. 需要登录时更新为 `auth_required`，按 [browser-workflow.md](references/browser-workflow.md) 完成登录。
4. 开始填写时更新为 `form_filling`。只使用 `profile` 和用户本轮明确提供的事实。
5. 上传简历前检查文件存在、文件类型正确，并核对页面显示的文件名。
6. 填完后检查必填项、联系方式、教育经历、岗位名称、附件和隐私/诚信声明。
7. 根据提交策略处理：
   - `review`：更新为 `ready_for_review`，展示页面状态并等用户确认最终提交。
   - `automatic`：仅当当前域名在 `allowed_domains` 中，且页面无未知问题、CAPTCHA 或额外声明时，自动推进到 `ready_for_review`；它不代表跳过 Browser skill 要求的即时确认。
   - 无论哪种模式，向网站发送联系方式、简历等敏感信息前，以及点击最终提交前，都必须按 Browser skill 在动作当时向用户确认。
8. 只有看到明确成功页面、申请编号或成功提示后才能更新为 `submitted`，并用 `--confirmation-ref` 保存非敏感的申请编号或截图路径。
9. 失败时更新为 `failed`，备注真实错误和可恢复位置；不要反复提交。

示例：

```bash
<plugin-root>/scripts/job-autopilot update 12 --status submitted \
  --confirmation-ref "application-id: 845392"
```

## 手机号登录与验证码

在浏览器中点击“发送验证码”之前记录当前毫秒时间戳。点击后运行：

```bash
<plugin-root>/scripts/job-autopilot otp-wait \
  --after-ms <timestamp> \
  --timeout 120 \
  --sender-contains "可选发送方关键字"
```

获取返回的验证码后，在向当前招聘网站输入前做即时确认，然后立即使用并丢弃。不要在对用户回复、数据库备注、截图文件名或事件日志中复述验证码。登录成功后记录：

```bash
<plugin-root>/scripts/job-autopilot site-login --url "<current-url>" --method phone_otp
```

一次登录只触发一次验证码；超时后先检查页面与设备，再决定是否由用户重发。

## 特殊表单规则

- 开放性问题、薪资、到岗时间、工作授权、竞业、亲属关系、纪律处分等没有明确事实时暂停询问。
- 自愿人口统计类问题可以跳过时保持未选；不要代替用户选择族群、性别、残障或退伍军人状态。
- 隐私协议和真实性声明属于用户承诺。首次遇到新的声明文本时停在提交前让用户确认。
- 不上传与本岗位无关的附件；不修改简历文件。
- 同一岗位出现多个官网入口时，以 JDWatch `job.url` 或用户指定的官网链接为准，并以 Job ID 防重。

## 控制台

启动本地控制台：

```bash
<plugin-root>/scripts/job-autopilot serve --host 127.0.0.1 --port 8765
```

在独立长运行终端中保持服务，然后用普通浏览器打开 `http://127.0.0.1:8765/`。控制台可查看和筛选 27 届秋招职位库、一键加入投递队列，也可直接启动或恢复长期 Codex 任务、暂停本轮工作、回答必要确认、查看投递漏斗、修改状态、打开官网、发起 AI 简历解析、维护动态个人资料和自动推进域名白名单。

## 对用户汇报

完成一批后只汇报：

- 新发现、跳过重复、成功提交、待确认和失败的数量。
- 每个成功岗位的标题、公司、Job ID、官网链接和非敏感确认信息。
- 每个阻塞岗位需要用户完成的具体动作。

不要输出原始短信、验证码、完整个人资料或长篇浏览器日志。
