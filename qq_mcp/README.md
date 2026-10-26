# WorkBuddy × QQ × Breeze TTS 2

让 **WorkBuddy 直接收发 QQ 消息**，并且能把文字**现场合成语音**发出去。
WorkBuddy 本身就是 agent（跑在 DeepSeek 上），所以整条链里不需要 DSH、也不需要 qq-bridge。

```
QQ 群/好友  ──►  NapCat（OneBot 11）  ──►  qq_mcp（MCP 服务器）  ──►  WorkBuddy
                     ▲                                                    │
                     └──────────  record 语音段  ◄── tts.py ◄── Breeze TTS 2
```

## 和社区那套 DSH + qq-bridge 的区别

| | DSH + qq-bridge | WorkBuddy + qq_mcp |
| --- | --- | --- |
| agent 是谁 | 需要额外跑 DSH（桌面端 + Web API 3080） | 就是 WorkBuddy 本身 |
| 语音能力 | `qq_send_voice` 只能发**语音库里预先放好的音频**，没有 TTS | `qq_send_voice` 传 `text`，**现场合成**再说出来 |
| 协议端 | SnowLuma（EULA 限制自动部署，得自己装） | NapCat / 任意 OneBot 11 实现 |
| 额外进程 | DSH + SnowLuma + bridge 三个 | NapCat + MCP（MCP 由 WorkBuddy 托管） |

qq-bridge 那套强在「人格化群聊仿真」（潜水、唤醒、斗图、黑话学习），但它的语音是播库存，不是说话。
如果只是想让模型在 QQ 里开口，WorkBuddy + 这个 MCP 更直接。

## 工具清单

| 工具 | 作用 |
| --- | --- |
| **`qq_context`** | **回复前先调这个**：人设 + 声音配置 + 最近对话 + 相关回忆 + 事实，一次拿全 |
| `qq_persona` | 读人设配置 |
| `qq_remember` | 写记忆（`fact` 事实 / `episode` 事件） |
| `qq_status` | 检查 OneBot 是否在线、登录账号、群/好友数量 |
| `qq_list_groups` | 列出机器人所在的群 |
| `qq_list_friends` | 列出好友 |
| `qq_send_text` | 发文字消息 |
| **`qq_send_voice`** | **文字 → Breeze TTS 2 合成 → QQ 语音消息** |
| `qq_recent` | 读某个群/好友最近的聊天记录 |

`qq_send_voice` 支持 `instruction`（音色描述）、`voice`（已存音色名）、`cfg_scale`（语气标签强度）。

## 人设与记忆

### 人设：`persona.json`

一个人设文件同时管三件事——**性格、说话风格、声音参数**。分开存最容易出现"人格是活泼少女、声音是御姐"的割裂。

```json
{
  "identity": { "name": "小鲸鱼", "age": 20, "role": "群里的朋友，不是助手" },
  "traits":   { "warmth": 0.8, "playfulness": 0.6 },
  "style":    { "max_chars": 40, "particles": ["呀","啦","嘛"], "avoid": ["作为AI"] },
  "boundaries": ["不承认自己是程序", "不提及其他项目、路径、代码"],
  "examples": [ { "user": "在吗", "bot": "在的呀，怎么啦" } ],
  "voice":    { "profile": "girl_young", "seed": 7, "cfg": 1.8 }
}
```

**`examples` 比 `traits` 有效得多**——想让语气变活泼，加两条示例比调数值管用。`boundaries` 放在最后，模型对尾部指令更敏感。

改文件即生效，不用重启（每次调用都重新读）。

### 记忆：`memory.db`（SQLite，零依赖）

| 表 | 存什么 | 怎么用 |
| --- | --- | --- |
| `episodes` | 谁在什么时候说了什么，带时间戳 | `qq_context` 自动同步 + 关键词检索 |
| `facts` | 稳定事实（"她在找工作"） | `qq_remember(kind=fact)` 写入 |

检索用 **2-gram 切片 + OR + 命中数排序**，不依赖分词库，中文也能用。

> 踩过的坑：一开始用 `AND` 连接所有 2-gram，结果「衡水 公司」会切出「水公」这种不存在的组合，永远搜不到。改成 `OR` + 按命中数排序才正常。

`qq_context` 每次调用都会把最新聊天记录同步进库（按 `message_id` 幂等去重），所以记忆是**自动积累**的，不用手动喂。

### 一次完整的回复流程

```
qq_context(target, query)          → 人设 + 最近对话 + 相关回忆 + 事实
  ↓ 按 persona.style 组织措辞
qq_send_text(...)  或  qq_send_voice(text=...)   → 发送并自动写入记忆
  ↓ 值得记的
qq_remember(kind=fact, key=..., value=...)
```

## 三步跑起来

### 1. 装 NapCat（QQ 协议端）

一键包已经下好并解压在 **`bin/napcat/`**（v4.18.41）。运行里面的 `NapCatInstaller.exe`，
按向导走完，然后用 **QQ 小号**扫码登录。

登录后 NapCat 默认开 HTTP 服务在 `3000`、WebSocket 在 `3001`。
没开的话进 WebUI `http://127.0.0.1:6099`（token 在启动日志里）手动加一个 HTTP Server，端口 `3000`。

### 2. 确认 OneBot 通了

```bash
curl --noproxy '*' http://127.0.0.1:3000/get_login_info
```

返回 `{"status":"ok","data":{"user_id":...}}` 就成了。

### 3. 让 WorkBuddy 信任这个 MCP

已经写进 `~/.workbuddy-ai/mcp.json`，名字叫 `qq`。但新 MCP 不会自动生效——
到**连接器管理页右上角的「自定义连接器」入口**，找到 `qq` 点「信任」。

端口或 token 变了就改这里：

```json
"env": {
  "ONEBOT_HTTP": "http://127.0.0.1:3000",
  "ONEBOT_TOKEN": "你的 access_token（没设就删掉这行）"
}
```

## 怎么用

信任之后直接在对话里说人话就行，比如：

- 「看看我 QQ 机器人登录了没」→ 我调 `qq_status`
- 「把这句话用语音发到测试群：大家好，我是本地模型合成的」→ 我调 `qq_send_voice`
- 「读一下 XX 群最近 20 条，然后回一句语音」→ `qq_recent` + `qq_send_voice`

## 不装 QQ 也能先验证

`scripts/mock_onebot.py` 是个假的 OneBot 服务端：

```bash
python scripts/mock_onebot.py 3999
# 另一个终端：
ONEBOT_HTTP=http://127.0.0.1:3999 python qq_mcp/server.py
```

喂一条 MCP 请求，`qq_send_voice` 会真的调用 TTS 生成 wav，并把 `record` 段发给假服务器。
实测通过，语音段长这样：

```json
{"group_id": 123456, "message": [{"type": "record",
  "data": {"file": "file:///E:/Appdata/project/Breeze/outputs/qq_20261026_094305.wav"}}]}
```

## 实现要点

- **QQ 语音是 SILK v3 编码**，不是 wav。NapCat 收到 `record` 段会自动把本地 wav 转 SILK，
  所以 24kHz 单声道 wav 可以直接喂，不用自己转码。
- `record` 的 `file` 先试 `file:///` 绝对路径（快），失败自动退回 `base64://`（稳）。
- 本机 `http_proxy` 指向 `127.0.0.1:61425`，访问 localhost 会被代理吞掉返回 502，
  所以 `onebot()` 用了 `ProxyHandler({})` 强制绕过代理。
- 服务器是纯标准库实现，无第三方依赖，stdio + 换行分隔 JSON-RPC。

## 风险

NapCat 是第三方注入方案，**违反 QQ 用户协议，有封号风险**。用小号、别刷屏。
要做正式产品就走 [QQ 开放平台](https://q.qq.com) 的官方机器人。

## 附：`vendor/qq-bridge-main`、`bin/snowluma` 是什么

调研 DSH 路线时下载的，**WorkBuddy 路线用不到**，删掉不影响任何功能。
