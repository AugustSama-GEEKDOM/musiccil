# musiccil GUI

给 `musiccil` 用的图形前端：搜歌、翻列表、慢慢攒歌单，然后一键丢给终端播放器播放。

![界面](../docs/images/gui.png)

## 为什么是"前端"而不是另一个播放器

播放内核只有一份：mpv 出声、封面提主色、唱片中心转像素封面、歌词、系统媒体键。
GUI 只负责**找歌和组歌单**——点「播放」时把歌单写成 JSON、交给终端播放器。

这样做的直接好处是没有第二套实现要维护：播放器的任何改进（配色、转速、媒体键、
退出时收 mpv）GUI 这边自动就有。

## 编译

需要 MinGW-w64 的 `g++`（或任何支持 Win32 API 的 C++ 编译器）。
**不需要 Qt、不需要 wxWidgets、不需要 CMake**——链接的都是 Windows 自带的库。

```bat
cd gui
build.bat
```

产出 `gui\build\musiccil-gui.exe`，可以直接双击运行。

手动编译的话（和 `build.bat` 等价）：

```bat
g++ -std=c++11 -O2 -mwindows -DUNICODE -D_UNICODE ^
    -o build\musiccil-gui.exe ^
    src\main.cpp src\api.cpp src\json.cpp src\playlist.cpp ^
    -lwininet -lcomctl32 -luser32 -lgdi32 -lshell32 -lole32
```

这几个参数都是必须的，少一个就出问题：

- `-DUNICODE -D_UNICODE`：用宽字符版 Win32 API。不加的话 MinGW 的 `commctrl.h`
  会报一堆 "LVS_EX_FULLROWSELECT was not declared"，中文也不会正常。
- `-mwindows`：GUI 子系统，运行时不弹一个黑控制台。

## 用法

1. 输入关键词，选平台（网易云 / QQ音乐 / 酷狗 / 酷我 / 咪咕 / 千千），
   回车或点「搜索」。
2. 结果里**双击**一首加入歌单（已经在歌单里的会打 √），或点「全部加入」。
3. 右侧歌单可以用「移出 / 上移 / 下移 / 清空」调整。
4. 点「播放」——自动保存歌单，并在独立终端窗口里拉起播放器。
5. 「保存歌单」只落盘不播放；「打开目录」跳到歌单所在目录。

歌单固定存在 `%LOCALAPPDATA%\musiccil-gui\playlist.json`，随时能拿别的工具复用。

## 前置条件

GUI 本身能独立编译运行，但两点要注意：

- **搜索要密钥**：去 <https://muscil.geekdom.top> 免费领一把（注册 → 控制台 → 我的密钥 → 复制），
  然后让 Python 版帮你存好：`musiccil --setup`（或 `musiccil --setup --key <密钥>`）。
  GUI 读的是同一个文件 `%USERPROFILE%\.musiccil\key`，也认环境变量 `MUSICCIL_API_KEY`，
  配一次两边都能用。没配的话界面会直接告诉你去哪领。
- **播放要 Python + musiccil**：GUI 是通过 `python -m musiccil` 拉起播放器的，
  所以得先在项目根目录 `pip install -e .`，并装好 mpv。
  找不到 Python 时会弹框提示。

## 代码结构

| 文件 | 作用 |
|---|---|
| `src/main.cpp` | 窗口、控件、布局、事件处理 |
| `src/api.cpp` | 接口客户端（WinINet）+ UTF-8/UTF-16 转换 |
| `src/json.cpp` | 极简 JSON 解析器（不引第三方库） |
| `src/playlist.cpp` | 歌单落盘 + 拉起终端播放器 |

四个文件都不大，加起来一千行出头，改起来直接看就行。

## 改代码时容易踩的坑

**拉起播放器必须传 `--no-window`。** 我们已经用 `CREATE_NEW_CONSOLE` 给了它一个真控制台，
但那个控制台刚创建时还不是前台窗口，播放器自己的"控制台可见吗"判断会判否、
于是又开一层窗口——结果是首层空转退出、白起一个进程，看起来像"点了没反应"。

**不能重定向子进程的 stdout/stderr。** 新控制台里的 tty 只有在不重定向时才成立，
一旦重定向，播放器拿到的是管道、`isatty()` 为假，界面画不出来。

**`build.bat` 必须是 CRLF 换行。** cmd 对 LF 换行的批处理解析不稳，
会出现 `'cho' is not recognized` 这种莫名其妙的报错。

**密钥不进仓库。** 读取顺序和 Python 版保持一致（环境变量 → 本地密钥文件），
不要在代码里写死默认密钥。

**接口基址走 musiccil API 调用站。** 默认 `https://muscil.geekdom.top/open/music/`，
和 Python 版一样读 `MUSICCIL_API_BASE` 覆盖（本地调试时才需要）。
密钥只有用户自己那一把，客户端不内置任何默认密钥。
