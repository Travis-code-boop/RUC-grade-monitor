# 人大成绩自动监控

该项目是人大成绩自动监控的公开源码模板。它包含成绩查询、变化检测、PushPlus 通知和可手动运行的 GitHub Actions 模板。

真实监控应放在 private repo 中运行，避免公开暴露 Actions 执行记录、运行时间、状态文件和日志。

## 公开仓库包含什么

```text
.github/workflows/
  grade-monitor.yml        # 手动运行的主监控 Action 模板
  grade-health-check.yml   # 手动运行的健康检查 Action 模板

check_grades.py            # 程序入口，处理 CLI 参数和主执行流程
config.py                  # 读取环境变量、.env 和 GitHub Secrets
ruc_auth.py                # 人大统一身份认证账号密码登录
ruc_jw_client.py           # 教务成绩接口客户端
http_json.py               # JSON HTTP 请求工具
grade_diff.py              # 成绩标准化、指纹生成、变化检测
state_store.py             # 读写本地状态文件
notifier.py                # PushPlus 通知

tests/test_grade_monitor.py
requirements.txt
.env.example
README.md
```

公开仓库不包含：

- 自动运行的真实 GitHub Actions schedules
- 真实 `seen_grades.json` 状态文件
- `.env`
- GitHub Secrets
- 账号、密码、token、Cookie 或成绩明文

## 私有运行仓库

建议把真实监控放在 private repo 中运行。克隆或 fork 后，在运行仓库中配置：

| Secret | 说明 |
| --- | --- |
| `RUC_USERNAME` | 人大统一身份认证账号，通常是学号或工号 |
| `RUC_PASSWORD` | 人大统一身份认证密码 |
| `PUSHPLUS_TOKEN` | PushPlus 个人 token |
| `GRADE_HASH_SALT` | 任意随机字符串，用于保护状态文件里的成绩指纹 |

账号密码登录成功后，程序只在本次运行内存里使用教务接口需要的临时 token/cookie，不会把它们写入 GitHub Secrets。

## GitHub Actions

公开模板内置两条 workflow：

- `grade-monitor`：查询成绩、对比状态、发现变化时通过 PushPlus 推送，并提交 `seen_grades.json`。
- `grade-health-check`：运行健康检查并发送 PushPlus 健康通知。

默认只启用 `workflow_dispatch`，也就是手动运行。设置好 Secrets 后，进入 `Actions` 页面分别选择 `grade-monitor` 和 `grade-health-check`，点击 `Run workflow` 即可运行。

如果要在自己的 private repo 里自动定时运行，可以取消 workflow 文件里 `schedule` 部分的注释：

- `grade-monitor` 示例：每小时第 17 分钟运行。
- `grade-health-check` 示例：北京时间每天 `00:11`、`06:11`、`12:11`、`18:11` 运行。

## 项目架构

程序查询成绩前会执行账号密码登录：

1. 使用 `RUC_USERNAME` / `RUC_PASSWORD` 登录人大统一身份认证。
2. 通过统一认证跳转换取教务接口需要的临时 token/cookie。
3. 使用本次运行内存里的临时 token/cookie 请求成绩接口。
4. 如果成绩接口返回 `401/403`，程序会重新账号密码登录一次，再重试成绩查询。
5. 登录统一认证时会对临时网络错误重试，避免 GitHub runner 偶发 DNS 或连接波动导致任务立即失败。

普通学号/工号会按统一认证网页规则自动加上 `ruc:` 前缀。邮箱、手机号、已经带 `:` 前缀的账号会原样使用。少数情况下如果学校代码不是 `ruc`，可以添加变量 `RUC_LOGIN_SCHOOL_CODE`。

## 日志隐私

代码默认按公开日志场景收敛输出：

- `--config-check` 只输出配置项是否存在，不输出账号、密码、token、salt 的片段、长度或格式。
- 成绩监控日志只输出基线建立、无变化、发现变化数量等状态。
- 健康检查日志不输出可见成绩数量。
- 新成绩的课程名、分数和绩点只通过 PushPlus 私信发送，不写入 Actions 日志。
- 状态文件只保存加盐 hash 指纹，不保存课程名和分数明文。

## 本地运行

复制本地配置模板：

```bash
cp .env.example .env
```

在 `.env` 里填入需要的变量，然后运行：

```bash
python3 check_grades.py --config-check
python3 check_grades.py --dry-run
python3 check_grades.py --notify-test
python3 check_grades.py
```

常用参数：

| 命令 | 作用 |
| --- | --- |
| `--config-check` | 输出严格隐私配置摘要，只确认配置是否存在 |
| `--dry-run` | 查询成绩但不发送通知、不写入状态 |
| `--notify-test` | 发送 PushPlus 测试消息 |
| `--health-check` | 运行健康检查流程 |
| 无参数 | 正式监控，可能发送成绩通知并更新状态 |

第一次正式运行只会建立基线状态，不会推送历史成绩。之后发现新增成绩或已有成绩变化才会推送。

## 运行测试

```bash
python3 -m unittest discover -s tests
```
