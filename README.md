# pta-ccs-api

为 PTA (pintia.cn) 上采用 ACM/ICPC 赛制的题目集(比赛)提供 [ICPC Contest API 2023-06](https://ccs-specs.icpc.io/2023-06/contest_api) 支持,
使 ICPC Tools CDS 等工具可以直接连接。以独立 Python 脚本方式运行。

## 工作原理

```
pintia.cn  ──(限速拉取)──>  pta-ccs-api  ──(Contest API)──>  ICPC Tools CDS
                            本地缓存 data/<id>/
```

- 启动时全量同步比赛数据:题目集信息、题目、报名成员、全部提交、提交详情(逐测试点结果)
- 之后按轮询间隔增量同步新提交与判题结果变化,比赛状态(开始/封榜/结束)变化时补发 `state` 事件
- 所有数据写入本地事件日志 `events.jsonl`,重启后直接回放恢复,无需重新抓取
- 对 PTA 的所有请求经过全局限速器(默认最小间隔 1 秒 + 随机抖动),429/5xx 自动指数退避重试
- event-feed 流式连接空闲时每 3 秒检查一次新事件,一旦有新事件立即排空下发,保证数据尽可能及时

## 数据来源(PTA 接口)

| 接口 | 用途 |
|---|---|
| `/api/problem-sets/<id>` | 比赛名称、开始/结束时间、时长、封榜配置、编译器、所属单位 |
| `/api/problem-sets/<id>/problems` | 题目 label(A/B/C…)、标题、时限、测试点数量 |
| `/api/problem-sets/<id>/members` | 报名名单(队伍来源,含未提交的选手)、分组、真实姓名/学号 |
| `/api/problem-sets/<id>/submissions` | 提交列表(`before` 游标翻页) |
| `/api/submissions/<id>` | 提交详情 → 逐测试点判定结果(runs) |

## 已支持

- `/api`
- `/api/contests`
- `/api/contests/<contestId>`
- `/api/contests/<contestId>/state`
- `/api/contests/<contestId>/languages`
- `/api/contests/<contestId>/problems`
- `/api/contests/<contestId>/teams`
- `/api/contests/<contestId>/organizations`
- `/api/contests/<contestId>/groups`
- `/api/contests/<contestId>/judgement-types`
- `/api/contests/<contestId>/submissions`
- `/api/contests/<contestId>/judgements`
- `/api/contests/<contestId>/runs`
- `/api/contests/<contestId>/event-feed` (支持 `stream` 和 `since_token` 查询参数)

## 未支持

- `webhooks` / `scoreboard` / `Persons` 接口
- `Awards` / `Accounts` / `Commentary` / `Clarifications` 接口
- 所有 `POST` / `PUT` / `PATCH` 接口

## How to use

Windows / Linux 通用,Python ≥ 3.8:

```bash
pip3 install -r requirements.txt   # 仅依赖 requests

# 登录 pintia.cn 后,从浏览器 DevTools (F12) 复制 Cookie 中的 PTASession 值
python3 -m pta_ccs <题目集ID> --cookie "PTASession=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

Linux 后台运行示例:

```bash
nohup python3 -m pta_ccs <题目集ID> --cookie "PTASession=xxxx" > pta-ccs.log 2>&1 &
# cookie 过期后更换: kill 进程 → 重新执行上面的命令(本地缓存与事件不会丢失)
```

> [!TIP]
> 题目集 ID 就在你进入题目集(比赛)页面后的 URL 里:
> 形如 `https://pintia.cn/problem-sets/2000172178531069952`,题目集 ID 就是 `2000172178531069952`

首次启动会全量同步(受限于限速,几百条提交约需数分钟),完成后即可对外服务。
之后重启会直接回放本地事件日志,秒级就绪,并继续增量轮询。

- 使用 ICPC Tools CDS 连接,URL 格式为:
  `http(s)://<host>:<port>/api/contests/<题目集ID>`
- 认证方式为 HTTP Basic,用户名/密码见下方参数(默认 `pintia` / `pintia-pass`)

## 中断恢复

进程意外中断后重启,会根据本地缓存自动选择恢复方式:

| 中断时机 | 恢复行为 |
|---|---|
| 全量同步完成后 | 回放事件日志,秒级就绪,零额外请求 |
| 提交详情抓取阶段(最耗时) | **续传**:只补抓缺失的详情,已抓取的进度不丢失 |
| 提交列表翻页阶段(约几十秒) | 重新翻页抓取(代价很小) |

进度每 50 条详情保存一次,不存在"中断即全量重来"的情况。

## 参数说明

| 参数 | 默认值 | 说明 |
|---|---|---|
| `<题目集ID>` | (必填) | PTA 题目集(比赛) ID |
| `--cookie` | 环境变量 `PTA_COOKIE` | PTA Cookie,可只给 `PTASession` 的值 |
| `--host` / `--port` | `0.0.0.0` / `8890` | HTTP 监听地址/端口 |
| `--user` / `--password` | `pintia` / `pintia-pass` | Basic 认证凭据 |
| `--rate` | `1.0` | 访问 PTA 的最小请求间隔(秒),**请勿设置过小以免触发风控** |
| `--limit` | `50` | PTA 分页大小 |
| `--poll-interval` | `15` | 比赛进行中的增量轮询间隔(秒) |
| `--poll-interval-ended` | `60` | 比赛结束后的轮询间隔(秒) |
| `--proxy` | 直连 | 访问 PTA 使用的代理(默认忽略系统代理设置) |
| `--data-dir` | `./data` | 缓存目录(按题目集 ID 分目录) |
| `--colors` | `./colors.csv` | 题目配色 CSV,不存在时自动生成默认配色 |
| `--assets-dir` | `./assets` | 本地图片资源目录(CDP 布局) |
| `--no-details` | 关 | 不抓取提交详情(将没有 runs 数据,大幅减少请求量) |
| `--no-poll` | 关 | 不轮询 PTA,仅用本地缓存对外服务 |
| `--no-serve` | 关 | 仅同步数据后退出,不启动 HTTP 服务 |
| `--reset` | 关 | 删除本地缓存并重新全量同步 |
| `--verbose` | 关 | 输出 HTTP 请求等详细日志 |

## 题目配色与图片

### 题目颜色(colors.csv)

首次运行会自动生成默认配色文件 `colors.csv`,A~M 题各不同颜色,可自行修改:

```csv
# 每行 label,颜色名,RGB;# 开头为注释
label,color,rgb
A,Red,#E53935
B,Orange,#FB8C00
...
```

- 支持自定义任意 label(包括超出 M 的题目),未列出的题目为白色
- 修改后重启本程序生效(仅重建本地事件日志,不会重新请求 PTA)
- 也可用 `--colors <文件>` 指定其他配色文件

### 图片资源(assets 目录)

按 CDP(Contest Data Package)目录布局存放图片,首次运行自动创建骨架:

```
assets/
├── contest/logo.<ext>                比赛图标(Resolver 主页面图片,也是无照片队伍的默认照片)
├── organizations/<orgId>/logo.<ext>  机构图标(该机构所有队伍共享)
└── teams/<teamId>/photo.<ext>        队伍照片(teamId 为队伍 API id,可简写为数字 uid)
```

- 支持格式:png / jpg / gif / svg / webp / bmp
- 放入图片后重启即可,事件中的 `logo` / `photo` 字段会自动生成,本程序同时提供对应下载端点
  (`/api/contests/<id>/logo.png`、`/api/contests/<id>/organizations/<orgId>/logo.png`、
  `/api/contests/<id>/teams/<teamId>/photo.png`)
- 目录内容变化后重启会自动重建事件日志,不会重新请求 PTA

## 映射说明

- **contest id** = PTA 题目集 ID;比赛时间取自题目集的 `startAt` / `endAt` / `duration`
- **封榜**:按题目集配置 `hidingTime`(开赛后第 N 分钟封榜)计算,
  `scoreboard_freeze_duration = duration − hidingTime`
- **teams** 来自报名名单(members),队伍名为学生真实姓名(缺省用昵称),label 为学号;
  未报名却提交的用户会根据提交数据补建队伍
- **organizations** 为题目集所属单位(单一机构)
- **problems** 的颜色来自 `colors.csv`(见上),缺省白色
- **groups** 来自 PTA 用户组(userGroups)
- **languages** 来自题目集配置的编译器(`GCC`/`GXX`/`JAVAC`/`PYPY3` 等)
- **judgement-types** 将 PTA 状态映射为 API 短名:`ACCEPTED→ac`、`WRONG_ANSWER→wa`、
  `PRESENTATION_ERROR→pe`、`COMPILE_ERROR→ce`、`TIME_LIMIT_EXCEEDED→tle`、
  `MEMORY_LIMIT_EXCEEDED→mle`、`OUTPUT_LIMIT_EXCEEDED→ole`、
  `RUNTIME_ERROR/SEGMENTATION_FAULT→rte`、`INTERNAL_ERROR→ice`、
  `PARTIAL_ACCEPTED→pa`、`SKIPPED→skipped`(CE/ICE 不计罚时)
- **runs** 来自提交详情的逐测试点结果,`ordinal` 为测试点序号,`run_time` 为该点耗时(秒)
- **judgements**:提交时先发出 `judgement_type_id=null` 的待判事件,出结果后更新

## 注意事项

- `PTASession` cookie 过期后轮询会持续失败并提示,重新登录 PTA 后带新 cookie 重启即可,
  本地缓存与事件不会丢失
- 隐私最小化:仅使用昵称、真实姓名、学号字段,不保存邮箱、手机号等其余个人信息
- 多场比赛请为每个题目集 ID 分别启动一个实例
- 请合理设置 `--rate`,请求过快可能触发 PTA 风控导致账号受限

## PRs welcome ~
