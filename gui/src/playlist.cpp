#include "playlist.h"

#include <windows.h>

#include <cstdio>

#include "json.h"

namespace {

// 找 python：PATH 里挨个试，都没有再退回裸名让系统去解析。
std::string findPython() {
    static const char* names[] = {"python.exe", "python3.exe", "py.exe"};
    char buf[MAX_PATH];
    for (int i = 0; i < 3; i++) {
        char* found = 0;
        DWORD n = SearchPathA(0, names[i], 0, sizeof(buf), buf, &found);
        if (n > 0 && n < sizeof(buf)) return std::string(buf, n);
    }
    return "python";
}

// 项目根目录：GUI 通常放在 <repo>\gui\build\，往上找含 musiccil 包的那一层。
std::string findProjectRoot() {
    char exe[MAX_PATH];
    DWORD n = GetModuleFileNameA(0, exe, sizeof(exe));
    if (n == 0 || n >= sizeof(exe)) return "";
    std::string dir(exe, n);
    for (int up = 0; up < 5; up++) {
        size_t slash = dir.find_last_of("\\/");
        if (slash == std::string::npos) break;
        dir = dir.substr(0, slash);
        // 判定依据：存在 musiccil\__init__.py
        std::string marker = dir + "\\musiccil\\__init__.py";
        if (GetFileAttributesA(marker.c_str()) != INVALID_FILE_ATTRIBUTES) return dir;
    }
    return "";
}

}  // namespace

std::string defaultPlaylistPath() {
    std::string base;
    char buf[MAX_PATH];
    DWORD n = GetEnvironmentVariableA("LOCALAPPDATA", buf, sizeof(buf));
    if (n > 0 && n < sizeof(buf)) base = std::string(buf, n);
    else base = ".";
    std::string dir = base + "\\musiccil-gui";
    CreateDirectoryA(dir.c_str(), 0);
    return dir + "\\playlist.json";
}

std::string savePlaylist(const std::vector<Track>& tracks, const std::string& path,
                         std::string* error) {
    if (tracks.empty()) {
        if (error) *error = "歌单是空的";
        return "";
    }
    FILE* fp = fopen(path.c_str(), "wb");
    if (!fp) {
        if (error) *error = "无法写入歌单文件：" + path;
        return "";
    }
    std::string out = "[\n";
    for (size_t i = 0; i < tracks.size(); i++) {
        const Track& t = tracks[i];
        out += "  {\"id\": \"" + json::escape(t.id) + "\", ";
        out += "\"type\": \"" + json::escape(t.type) + "\", ";
        out += "\"name\": \"" + json::escape(t.name) + "\", ";
        out += "\"artist\": \"" + json::escape(t.artist) + "\", ";
        out += "\"album\": \"" + json::escape(t.album) + "\"}";
        if (i + 1 < tracks.size()) out += ",";
        out += "\n";
    }
    out += "]\n";
    fwrite(out.data(), 1, out.size(), fp);
    fclose(fp);
    return path;
}

std::string playerCommand() {
    return findPython();
}

unsigned long launchPlayer(const std::string& playlistPath, const std::string& extraArgs,
                           std::string* error) {
    std::string python = findPython();
    std::string root = findProjectRoot();

    // 拼命令行。所有用户可见的路径都加引号——Program Files、中文目录
    // 都会让不加引号的命令行被拆错。
    // --no-window 是必须的：下面已经用 CREATE_NEW_CONSOLE 给了播放器一个真控制台，
    // 但它刚创建时还不是前台窗口，播放器的"控制台是否可见"判断会判否，
    // 于是又开一层窗口——首层空转退出、白起一个进程。直接告诉它就地运行。
    std::string cmd =
        "\"" + python + "\" -m musiccil --file \"" + playlistPath + "\" --no-window";
    if (!extraArgs.empty()) cmd += " " + extraArgs;

    std::vector<char> mutableCmd(cmd.begin(), cmd.end());
    mutableCmd.push_back('\0');

    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));

    // CREATE_NEW_CONSOLE：给播放器一个真正的新控制台（它有 tty 才画得出界面）。
    // 不重定向 stdout/stderr，否则新控制台里拿到的是管道、界面画不出来。
    std::string workdir = root.empty() ? std::string() : root;
    BOOL ok = CreateProcessA(0, &mutableCmd[0], 0, 0, FALSE,
                             CREATE_NEW_CONSOLE, 0,
                             workdir.empty() ? 0 : workdir.c_str(), &si, &pi);
    if (!ok) {
        if (error) {
            char buf[256];
            snprintf(buf, sizeof(buf), "启动播放器失败（错误码 %lu）。请确认已安装 Python 和 musiccil。",
                     (unsigned long)GetLastError());
            *error = buf;
        }
        return 0;
    }
    unsigned long pid = pi.dwProcessId;
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return pid;
}
