# musiccil GUI

给 `musiccil` 用的图形前端。搜歌、翻列表、慢慢攒一份歌单，然后一键丢给终端播放器去放。

![界面](../docs/images/gui.png)

## 为什么只做前端，不做第二个播放器

播放内核只有一份：mpv 出声、封面提主色、唱片中心转像素封面、歌词、系统媒体键，
全都在终端播放器里。GUI 只干找歌和组歌单这两件事，点「播放」的时候把歌单写成 JSON，
交给终端播放器。

好处是只有一套播放逻辑要维护。播放器那边改了什么（配色、转速、媒体键、退出时收掉 mpv），
GUI 这边自动就跟着变。

## 编译

需要 MinGW-w64 的 `g++`，或者任何支持 Win32 API 的 C++ 编译器。不需要 Qt、wxWidgets
和 CMake，链接的都是 Windows 自带的库。

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

这几个参数少一个就出问题：

- `-DUNICODE -D_UNICODE` 是让它用宽字符版的 Win32 API。不加的话 MinGW 的 `commctrl.h`
  会报一串「LVS_EX_FULLROWSELECT was not declared」，中文也不会正常。
- `-mwindows` 指定 GUI 子系统，运行时不会弹一个黑控制台。

## 用法

1. 填关键词，选平台（网易云 / QQ音乐 / 酷狗 / 酷我 / 咪咕 / 千千），回车或者点「搜索」。
2. 在结果里双击一首加进歌单，已经在歌单里的会打个 √；也可以直接「全部加入」。
3. 右边那份歌单可以用「移出 / 上移 / 下移 / 清空」调整。
4. 点「播放」，它会自动存歌单，然后在独立终端窗口里拉起播放器。
5. 「保存歌单」只落盘不放，「打开目录」跳到歌单所在的目录。

歌单固定存在 `%LOCALAPPDATA%\musiccil-gui\playlist.json`，想拿别的工具接着用也行。

## 前置条件

GUI 自己能独立编译运行，不过有两件事得先办：

- 搜索要用密钥。去 <https://muscil.geekdom.top> 免费领一把（注册 → 控制台 → 我的密钥 →
  复制），然后让 Python 版帮你存好：`musiccil --setup`，或者 `musiccil --setup --key <密钥>`。
  GUI 读的是同一个文件 `%USERPROFILE%\.musiccil\key`，也认 `MUSICCIL_API_KEY` 环境变量，
  配一次两边都能用。没配的话界面会直接告诉你去哪领。
- 播放要用 Python 和 musiccil。GUI 是靠 `python -m musiccil` 拉起播放器的，所以得先在
  项目根目录 `pip install -e .`，再装好 mpv。找不到 Python 它会弹框提示。

## 代码结构

| 文件 | 作用 |
|---|---|
| `src/main.cpp` | 窗口、控件、布局、事件处理 |
| `src/api.cpp` | 接口客户端（WinINet）+ UTF-8/UTF-16 转换 |
| `src/json.cpp` | 极简 JSON 解析器（不引第三方库） |
| `src/playlist.cpp` | 歌单落盘 + 拉起终端播放器 |

四个文件都不大，加起来一千行出头，想改直接看就行。

## 改代码时容易踩的坑

拉起播放器的时候必须传 `--no-window`。这里已经用 `CREATE_NEW_CONSOLE` 给了它一个真控制台，
但那个控制台刚创建时还不是前台窗口，播放器自己那句「控制台可见吗」会判否，于是又开一层，
结果是首层空转退出、白起一个进程，看上去就像点了没反应。

不能重定向子进程的 stdout 和 stderr。新控制台里的 tty 只有在不重定向的时候才成立，
一重定向，播放器拿到的是管道，`isatty()` 为假，界面就画不出来了。

`build.bat` 必须是 CRLF 换行。cmd 解析 LF 换行的批处理不稳，会冒出
`'cho' is not recognized` 这种莫名其妙的报错。

密钥不进仓库。读取顺序和 Python 版保持一致（先环境变量，再本地密钥文件），
别在代码里写死一个默认密钥。

接口基址走 musiccil API 调用站，默认 `https://muscil.geekdom.top/open/music/`，
和 Python 版一样读 `MUSICCIL_API_BASE` 覆盖（本地调试才需要）。
客户端里只有用户自己那一把密钥，不内置任何默认密钥。
