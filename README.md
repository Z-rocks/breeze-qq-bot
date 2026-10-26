# Breeze QQ Bot

**跑在你自己电脑上的 QQ 聊天机器人** —— 本地合成语音、有人设、有记忆，全程不联网。

基于 [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2) 与 [Breeze-TTS-2.cpp](https://github.com/HoppouAI/Breeze-TTS-2.cpp)，
通过 MCP 协议接入 WorkBuddy，用 NapCat 连接 QQ。一张 8GB 显存的笔记本显卡就能跑。

```
QQ 消息  ──►  NapCat (OneBot 11)  ──►  qq_mcp (MCP)  ──►  WorkBuddy
              ▲                                              │
              └──── record 语音段 ◄── tts.py ◄── Breeze TTS 2
```

## 能做什么

| 能力 | 说明 |
| --- | --- |
| 🎙️ **本地语音** | 文字 → 24kHz wav → QQ 语音条，约 1.25× 实时，显存占用 4.2 GB |
| 🎨 **音色设计** | 一句话描述造音色："活泼开朗的少女音，语调轻快上扬" |
| 🧬 **音色克隆** | 5~15 秒参考音频 + 逐字稿，复刻一个人的声音 |
| 🎭 **表演指令** | 自然语言控情绪、语速、气声；`[笑]` `[叹气]` 等语气标签 |
| 🧠 **记忆** | 情景记忆（谁说过什么）+ 事实记忆，SQLite 存储，中文可检索 |
| 👤 **人设** | 性格、说话风格、声音参数写在同一个文件里，改完即生效 |
| 🔌 **MCP 接口** | 9 个工具，WorkBuddy 直接调用，不写代码也能用 |

## 快速开始

### 1. 准备 QQ 协议端

`bin/napcat/` 里已经放好 NapCat 一键包。运行 `NapCatInstaller.exe` 装好，
**用小号**扫码登录一次（之后走快速登录，不用再扫）。

### 2. 一键启动

双击 **`启动.bat`**，或者：

```bash
python start.py
```

它会依次：预检环境 → 拉起 NapCat → 等 OneBot 就绪 → 打印状态面板。

```
====================================================
  已就绪    ㅤㅤ (3554609881)
====================================================
  OneBot    http://127.0.0.1:3000
  群 / 好友  3 / 27
  已存音色   1 个
  记忆       12 条对话 · 2 条事实
  WebUI     http://127.0.0.1:6099/webui
====================================================
```

只看预检不启动：`python start.py --check`

### 3. 让 WorkBuddy 信任这个 MCP

配置已经写进 `~/.workbuddy-ai/mcp.json`（名字 `qq`）。到**连接器管理页右上角
「自定义连接器」→ 找到 `qq` → 点「信任」**。首次必须手动点一次。

之后就能直接说人话：

> 「看看 QQ 登录了没」
> 「用少女音跟 1513929790 说句晚安」
> 「读一下那个群最近 20 条，接一句」

## 目录结构

```
Breeze/
├─ 启动.bat                 一键启动入口
├─ start.py                 预检 + 启动 + 状态面板
├─ tts.py                   语音合成命令行封装
├─ qq_mcp/                  MCP 服务器
│  ├─ server.py             9 个工具：上下文/人设/记忆/收发
│  ├─ memory.py             SQLite 记忆库（情景 + 事实）
│  ├─ persona.json          人设 + 声音配置
│  └─ memory.db             记忆数据
├─ scripts/
│  ├─ send_qq_voice.py      应急手动发送（自己启 NapCat 再发）
│  ├─ dub_dialog.py         多角色配音拼接
│  ├─ mock_onebot.py        假的 OneBot 服务端，用来测链路
│  └─ dl.py                 多线程下载器
├─ docs/ARCHITECTURE.md     架构设计（分层/契约/安全/演进）
├─ bin/                     推理程序 + NapCat
├─ models/                  GGUF 权重（3.3 GB）
├─ voices/                  已保存的音色
└─ outputs/                 生成的 wav
```

## 架构

分四层：**接入适配 → 网关 → 内核 → 插件**，内核只认工具契约，不认识 TTS。
安全上把"不可信输入 / 沙箱 / 可信区"隔开，默认拒绝文件访问。

完整设计（分层职责、三个核心契约、人设与记忆体系、安全边界、从原型到项目的演进路径）
见 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**。

## 本地语音合成

### 为什么选 Q8_0 量化

| 版本 | 体积 | 显存需求 | 结论 |
| --- | --- | --- | --- |
| F16 | 5.9 GB | ~7 GB | 8GB 卡加上桌面占用必爆，排除 |
| **Q8_0** | **3.3 GB** | **~4.2 GB** | **实测峰值 5.3 GB（含桌面 1.1 GB），推荐** |
| Q6_K | 2.9 GB | ~3.5 GB | 想再省显存可以换 |
| Q4_K | 2.4 GB | ~3 GB | 最小安全档，音质有可闻损失 |

**实测性能**（RTX 4060 Laptop 8GB，Vulkan）：13.1 秒音频生成约 10.4 秒，约 **1.25× 实时**；
首次加载模型约 4 秒。

### 三种模式

```bash
# 1. 音色设计（无参考音频）
python tts.py "欢迎来到今晚的故事时间。" \
  --instruction "温柔的年轻女性，语气亲切" --cfg-scale 2 --out outputs/design.wav

# 2. 音色克隆（参考音频 + 逐字稿，必须完全一致）
python tts.py "It is good to hear your voice again." \
  --ref-audio ref.wav --ref-text "参考音频的准确逐字稿" --out outputs/clone.wav

# 存成音色复用（推荐，编码一次反复用）
python tts.py --save-voice narrator --ref-audio ref.wav --ref-text "逐字稿"
python tts.py "今晚的事，我们得谈谈。" --voice narrator

# 3. 表演指令（克隆音色 + 控情绪）
python tts.py "我们得谈谈。" --voice narrator \
  --instruction "语速放慢，语气克制而严肃。" --cfg-scale 2
```

### 语气标签

中文用 `[笑] [叹气] [咳嗽] [清嗓子]`，英文用 `(laugh) (sigh)`。
**需要 `--cfg-scale 2~3` 才会真的触发**，默认 1.0 会被当普通文字读过去；超过 3 声音会发紧。

### 常用参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--instruction` | 说清楚自然即可 | 音色描述 / 表演指令 |
| `--cfg-scale` | 1.0 | 指令强度，2~3 才压得住标签 |
| `--seed` | 42 | 同输入同 seed 输出一致，换 seed 换演绎 |
| `--max-new` | 750 帧（60 秒） | 帧上限，防跑飞 |
| `--split-chars` | 600 | 长文切分阈值 |
| `--list-voices` | — | 查看已存音色 |

## 常见问题

**Q：`start.py` 说 OneBot 没起来？**
看 `outputs/napcat_run.log` 尾部。常见原因是需要扫码登录、QQ 号不对、或 3000 端口被旧进程占着。

**Q：改了人设要重启吗？**
不用。`persona.json` 每次调用都重新读，改完下一条消息就生效。

**Q：合成很慢？**
首次要加载模型（约 4 秒）。之后 30 秒语音约等 24 秒。8GB 显存只能跑一个实例，
并发请求会 OOM，所以不要同时发多条。

**Q：想让声音前后一致？**
用 `--save-voice` 存音色再 `--voice` 复用。**不要逐句改 `--instruction`** ——
音色设计是指令条件生成，指令一变就换人了。

## 许可与声明

| 范围 | 许可 |
| --- | --- |
| **本项目源代码**（`qq_mcp/`、`scripts/`、`start.py`、`tts.py`） | **MIT** — 见 [LICENSE](LICENSE) |
| Breeze TTS 2 **模型权重** | BreezeBlue Research and Non-Commercial License |
| Breeze-TTS-2.cpp 推理程序 | Apache 2.0 |
| NapCat QQ 协议端 | 遵循其仓库自带许可 |

⚠️ **重要**：模型权重**仅限研究与非商业用途**。这意味着即使代码是 MIT，
用这套东西生成的语音也**不能商用**。商用需走 BreezeBlue 官方托管平台。

第三方组件的完整说明见 [NOTICE](NOTICE)。

### 风险提示

- NapCat 是第三方注入方案，**违反 QQ 用户协议，有账号被限制的风险**。请用小号。
- 克隆他人声音前必须取得授权；不得用于冒充、诈骗等用途。
- 使用即表示你自行承担上述风险。
