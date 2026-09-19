---
name: musiccil
description: Recommend music and play it in the terminal vinyl-player TUI (musiccil). Use when the user asks for songs, playlists, background music, "放首歌", "推荐歌单", "来点音乐", or wants playback started/controlled from a terminal session.
metadata:
  short-description: 推荐歌曲并用终端唱片机播放
---

# musiccil

`musiccil` 是个终端唱片机播放器：界面配色跟着当前封面走，唱片中心有一张跟着转的像素封面，
歌词会滚，音频交给 mpv 真放。

**本文档里一律用 `python -m musiccil`**：它依赖已装好的包（在项目根目录 `pip install -e .`），
装好之后在**任何目录**下都能跑。裸命令 `musiccil` 的 console script 目录常常不在 PATH 里，
直接用会报"不是可识别的命令"，所以不要写。

## 第 0 步：先确认有密钥（用户第一次用时会卡在这里）

接口按次计配额，需要一把密钥。密钥从本项目的 **musiccil API 调用站**免费领取：
<https://muscil.geekdom.top> 注册账号，登录后「控制台 → 我的密钥」里就有。

**先跑一次 `--setup` 探一下**，它会自己判断要不要引导：

```bash
python -m musiccil --setup --key 你的密钥      # 已知密钥：直接校验并保存，非交互
python -m musiccil --setup                    # 让用户自己粘贴（需要真终端）
```

校验走调用站的 `/api/key-info`，**不扣配额**，通过后才写入 `%USERPROFILE%\.musiccil\key`。
配好一次，之后所有命令都自动用它。

**用户还没有密钥时**，别自己编、也别去翻代码：告诉他去 <https://muscil.geekdom.top> 注册，
拿到密钥贴回来，你再执行 `python -m musiccil --setup --key <密钥>` 保存，然后继续放歌。
这一步很常见，别卡在这里反复试。

手动配置的三条等价路径（一般用不上，排查时可提）：环境变量 `MUSICCIL_API_KEY`、
上面那个密钥文件、命令行 `--key`。没配置时命令会直接提示怎么做。

## 什么时候用

用户想听歌、要歌单推荐、要"随便放点音乐"、想切换/暂停正在播放的歌时使用。
只做单纯的歌曲信息查询、不打算播放时，用搜索模式即可，不必启动界面。

## 核心流程

**铁律：一次工具调用搞定，不要拆步骤。** 实测"搜一次 → 读结果 → 写歌单 → 再解析 → 再播放"
这种多轮往返要花五分钟，而命令本身只要一秒——时间几乎 99% 耗在往返上。
播放器提供了两条一步到位的命令，覆盖绝大多数请求：

| 用户想要什么 | 用什么 | 耗时 |
|---|---|---|
| 指定的一首歌 | `--book` | ~1 s |
| 一份推荐歌单 | `--mix` | ~1 s |
| 想听某首 + 再推荐几首 | `--mix --seed` | ~1.5 s |

先选对命令，再发一次调用，然后告诉用户"已经在窗口里放了"。**不要**先搜索看结果再决定。

### 单曲/指定歌曲：直接 --book

用户说清歌名（尤其是"我想听 X 的 Y"）时，一条命令就够：

```bash
python -m musiccil "A Rusty Dream" --artist DOUDOU --book %TEMP%\song.json
```

它会自己完成：搜索 → 按歌手/歌名匹配出原唱 → 解析直链/封面/歌词 → 写歌单 → 开窗播放。
实测耗时约 1 秒。匹配失败时它会**自动换平台**（周杰伦、陈奕迅等在网易云常缺原版，
原版多在酷狗/酷我），不用你再手动重搜。

要点：

- **一定要带 `--artist`**。这是选对原唱的关键；不带时只按歌名取第一条，很容易拿到翻唱。
- 想多带几首：`--book-count 5`。只要歌单不播放：`--no-play`。
- 别先 `--search` 再手动拼 JSON —— 那是慢路径，除非用户要的是一份**推荐歌单**。
- `--book` / `--mix` 后面的路径**直接写成 `%TEMP%\song.json` 就行**：播放器自己会
  展开 `%TEMP%` / `~` / 环境变量，目录不存在也会自动建。不要换成 PowerShell 的
  `$env:TEMP` 拼接（多一层转义，容易写错）。

### 推荐歌单：直接 --mix

用户说"放点音乐 / 推荐几首 / 来点 XX 风格的"时，**一条 `--mix` 命令走完全程**：

```bash
python -m musiccil --mix %TEMP%\mix.json "深夜 city pop 氛围" --mix-count 8
```

它自己完成：搜索 → 去重、去掉 live/remix/伴奏版、同一歌手最多 2 首 → 并发解析
音频/封面/歌词 → 写歌单 → 开窗播放。实测 1 秒左右，歌单摘要会打印在标准错误里。

风格描述写得越具体越好（`"深夜 city pop 氛围"`、`"lofi 学习 BGM"`、`"90 年代港乐"`），
关键词直接决定选歌质量。常用参数：`--mix-count 6` 控制首数，`--per-artist 1` 收紧同歌手限制。

### 「我想听 X，再推荐几首」：--mix 加 --seed

用户点名一首歌、同时又要推荐时，**不要**拆成 `--book` + `--mix` 两次调用，
`--seed` 一条命令就能把这首排在歌单第一位、其余自动补推荐：

```bash
python -m musiccil --mix %TEMP%\mix.json "DOUDOU 福禄寿 独立民谣" \
  --seed "A Rusty Dream" --artist DOUDOU --mix-count 6
```

- `--seed` 是要听的那首，**配上 `--artist`** 才会走跨平台原唱匹配（否则可能拿到翻唱）。
- 种子占一个位置，`--mix-count` 是**总数**（上面例子里 = 1 首种子 + 5 首推荐）。
- 推荐关键词按风格/歌手写；搜索引擎对多个词的关键词容易报"获取失败"，
  播放器会自动逐级丢词退化成能查的关键词，不用自己改词。

需要自己从候选里精挑细选时（少见，通常没必要），才退回两步流程：

```bash
# 第 1 步：一次搜索拿候选。--brief 跳过逐首详情，输出体积只有 1/15、耗时 0.3 秒
python -m musiccil "深夜 city pop 氛围" -l 12 --search --brief --compact-json

# 第 2 步：把选中的 {id, type} 写成 JSON，直接播放
python -m musiccil --file picked.json
```

从候选里挑 5–12 首：同歌手不超过 2 首、避开同一首的多个版本（`api.pick_mix` 的规则同理）。
**不要**逐首去核对详情——`--file` 播放时会自动补全音频/封面/歌词。

筛选阶段的几条硬规矩：

- 用 `--brief`，不要用默认的完整解析。搜 15 首时完整 JSON 有 2.6 万字符（其中 57% 是歌词），
  这些都会被读进上下文；`--brief` 只要 1.7 千字符，而且不消耗逐首详情配额。
- 加 `--compact-json` 去掉空字段。
- 用 `-l 10~15` 一次取够，别为了凑列表反复翻页。

### 其他播放方式

直接播放：歌名搜索、平台:ID、本地文件、目录、m3u、音频直链、歌单、排行榜、用户歌单全部支持。

```bash
python -m musiccil "加州梦游" -l 5                     # 搜 5 首依次播放
python -m musiccil qq:001I6gzS3LufWy                   # 指定平台与歌曲 ID
python -m musiccil "D:\Music\夜曲.flac"                # 本地文件
python -m musiccil "D:\Music"                          # 整个目录
python -m musiccil --list 149553                       # 网易云歌单
python -m musiccil --toplist 3778678                   # 排行榜
python -m musiccil --user 123456 --pick 0              # 某用户收藏的第 1 个歌单
```

把筛选结果落盘再播放（推荐方式，可控性最好）：

```bash
python -m musiccil --playlist-out /tmp/night.json "城市夜晚 氛围" -l 20   # 搜索并落盘
python -m musiccil --file /tmp/night.json                                 # 播放该歌单
```

也可以直接把 JSON 从标准输入喂进去：`... | python -m musiccil --stdin-json`。

## 别让流程变慢

这是最容易踩的坑：命令本身很快（搜索 0.3 秒、解析 10 首并发后 0.3 秒、开窗 1 秒），
慢的是来回调用。硬性要求：

1. **能用 `--book` / `--mix` 就用它**，别把"搜→筛→写文件→播放"拆成好几轮。
   用户实测过一次推荐花 5 分钟——全部花在多轮往返上，命令换成 `--mix` 后是 1 秒。
2. 筛选阶段一律加 `--brief --compact-json`，别把歌词灌进上下文。
3. **不要为了挑一首歌去逐首查详情**（`--file` 播放时会自动补全）。
4. 一次搜索取够数量；失败/无结果时先换平台再换关键词，别在同一平台反复翻页。
5. 目标：从用户开口到窗口开始播放，**普遍在 1–2 秒内完成、最多一到两次工具调用**。若某步明显超时，
   检查是不是落到了慢平台或漏了 `--brief`。

## 播放：必须开一个独立窗口（重要）

播放器是交互式 TUI，需要真正的终端和键盘。**最关键的要求：它是给用户看的窗口，不是后台任务。**
直接在你的会话里跑 `python -m musiccil`，输出会进管道，用户什么都看不到——必须避免这种情况。

正常做法就是直接运行，播放器会自己判断并开窗：

```bash
python -m musiccil --file /tmp/night.json     # 检测到输出不是终端 → 自动弹出独立窗口
python -m musiccil "城市夜晚 氛围" -l 10        # 同上，会先解析再开窗
python -m musiccil --window --file p.json     # 显式要求开窗
```

**窗口默认是竖屏 70×56**（实测约 780×1240 像素），唱片约占整屏高的三分之一。需要
别的尺寸时：

```bash
python -m musiccil --window-size 100x30 --file p.json   # 想要更矮/更宽就改这里
```

觉得唱片转得太快或太慢时用 `--spin` 调（度/秒，默认 15 就是约 24 秒一圈，数值越小越慢）：

```bash
python -m musiccil --spin 8 --file p.json              # 约 45 秒一圈，更从容
```

换曲时主题配色和封面默认都会在 0.9 秒内渐变过去（不是瞬间跳色/跳图）。用户嫌快慢不合适时用
`--theme-fade` 调，数值越大越柔：

```bash
python -m musiccil --theme-fade 1.6 --file p.json     # 更慢更柔
python -m musiccil --theme-fade 0 --file p.json        # 不要过渡
```

切歌、上一首/下一首、快进快退都会做一次 0.22 秒的音频淡入淡出（声音不会被硬切）。
用户嫌快慢不合适时用 `--audio-fade` 调（0 为直接切换）：

```bash
python -m musiccil --audio-fade 0.5 --file p.json     # 更柔和
```

关掉播放器窗口/进程时 mpv 会被一起收掉，不会留在后台继续放歌。

播放时会把当前曲目发布到 **Windows 系统媒体通知**：任务栏媒体面板能看到曲名、歌手、专辑、
封面和进度，键盘媒体键（播放/暂停、上一首/下一首）也能直接控制播放器。这是默认开启的；
不想要就加 `--no-media-controls`。它依赖 `winsdk`，没装也不影响播歌，只是没有系统媒体控制。

自动开窗的判据是"当前输出不是终端"，所以在 agent 会话里直接跑就会开窗；如果已经在一个真实终端里，
它就地显示、不再多开一个。窗口里的子进程直接接收已解析好的歌单（临时文件随用随删），
不会重复消耗接口配额。

注意：**不要**对你启动的播放器做输出重定向（不要 `> log`、不要 `| tee`）。
Windows 上新控制台的 tty 只有在不重定向 stdout 时才成立，一旦重定向窗口里就画不出界面。
若确实需要自己观察输出，加 `--no-window` 并用带 TTY 的交互式会话运行。

想用别的终端模拟器或核查启动逻辑时，可用技能自带脚本（路径写全，免得依赖当前目录）：

```bash
python "%USERPROFILE%\.codex\skills\musiccil\scripts\launch_player.py" --file /tmp/night.json
```

告诉用户按键：`SPC` 播放/暂停、`←/→` 快退快进、`↑/↓` 音量、`N/P` 上下首、`H` 红心、
`S` 播放模式、`L` 歌词、`T` 列表、`R` 换主题、`M` 静音、`Q` 退出。

启动前确认曲目确有音频直链（`--search --json-out` 里的 `url` 非空即已验证），
避免把用户丢进一个没声音的窗口。开窗成功后命令会打印窗口进程号与按键提示。

## 注意事项

- **接口有固定包量配额**，搜索和详情都会消耗。批量筛选时先用 `--search --no-resolve` 少取数据，
  确认要播的那几首再解析音频地址；不要为了凑列表反复翻页。
- **音频直链有时效**，是 CDN 临时地址。歌单 JSON 可以长期保存和复用，但里面的 `url` 会过期，
  重新播放前用 `--no-resolve` 之外的方式重新解析（直接 `python -m musiccil --file ...` 即可，它会重新拉取）。
- 密钥存在本机（`%USERPROFILE%\.musiccil\key` / 环境变量 / `--key`），按次计配额。
  **不要把密钥抄进交付给用户的文件、日志、回复或提交到仓库**。用户的密钥就是他的账号。
- 客户端请求的是本项目的 musiccil API 调用站（`https://muscil.geekdom.top/open/music/`），
  调用方式与文档里写的完全一致。要换地址时用 `MUSICCIL_API_BASE` 覆盖。
- 播放器找不到 mpv 时会报错，用 `MUSICCIL_MPV` 指定 mpv 路径。
- `--no-audio` 只画界面不发声，适合演示或截图；`--render-once` 输出一帧后退出。
- 界面配色自动跟随封面主色；用户想固定配色时按 `R` 切换预设场景。

## 参考

- 接口字段、平台代号与已知坑：[references/api.md](references/api.md)
- 播放器全部命令行参数与 JSON 歌单格式：[references/cli.md](references/cli.md)
