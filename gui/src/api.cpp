#include "api.h"

#include <windows.h>
#include <wininet.h>

#include <cstdio>
#include <cstdlib>

#include "json.h"

namespace {

// 默认指向 musiccil API 调用站。客户端只带用户自己的密钥，不内置任何默认密钥。
// 需要换地址时用环境变量 MUSICCIL_API_BASE 覆盖（和 Python 版读同一个变量，
// 这样同一台机器上两个客户端不会各自记一份地址）。
const char* kDefaultBase = "https://muscil.geekdom.top/open/music/";
const char* kApiSite = "https://muscil.geekdom.top";

std::string envOr(const char* name, const std::string& fallback) {
    char buf[512];
    DWORD n = GetEnvironmentVariableA(name, buf, sizeof(buf));
    if (n == 0 || n >= sizeof(buf)) return fallback;
    return std::string(buf, n);
}

// 每次现取，而不是启动时缓存：改了环境变量后重开一次窗口就生效，
// 不需要重新编译；和 Python 版 api_base() 的行为保持一致。
std::string apiBase() {
    std::string base = envOr("MUSICCIL_API_BASE", "");
    if (base.empty()) base = kDefaultBase;
    if (!base.empty() && base[base.size() - 1] != '/') base += "/";
    return base;
}

// 密钥查找顺序和 Python 版一致：环境变量 → %USERPROFILE%\.musiccil\key。
// 仓库里不放默认密钥——公开仓库硬编码密钥等于把接口配额送人。
std::string keyFromFile() {
    std::string home = envOr("USERPROFILE", "");
    if (home.empty()) return "";
    std::string path = home + "\\.musiccil\\key";
    FILE* fp = fopen(path.c_str(), "rb");
    if (!fp) return "";
    std::string text;
    char buf[512];
    size_t n = fread(buf, 1, sizeof(buf), fp);
    fclose(fp);
    text.assign(buf, n);
    // 去掉首尾空白与可能的 BOM
    size_t b = text.find_first_not_of(" \t\r\n");
    size_t e = text.find_last_not_of(" \t\r\n");
    if (b == std::string::npos) return "";
    return text.substr(b, e - b + 1);
}

std::string lastErrorText(const char* what) {
    DWORD code = GetLastError();
    char buf[256];
    snprintf(buf, sizeof(buf), "%s（错误码 %lu）", what, (unsigned long)code);
    return buf;
}

}  // namespace

std::wstring toWide(const std::string& utf8) {
    if (utf8.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), (int)utf8.size(), 0, 0);
    if (n <= 0) return std::wstring();
    std::wstring out((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), (int)utf8.size(), &out[0], n);
    return out;
}

std::string toUtf8(const std::wstring& wide) {
    if (wide.empty()) return std::string();
    int n = WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), (int)wide.size(), 0, 0, 0, 0);
    if (n <= 0) return std::string();
    std::string out((size_t)n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), (int)wide.size(), &out[0], n, 0, 0);
    return out;
}

std::string urlEncode(const std::string& utf8) {
    static const char* hex = "0123456789ABCDEF";
    std::string out;
    for (size_t i = 0; i < utf8.size(); i++) {
        unsigned char c = (unsigned char)utf8[i];
        bool safe = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                    (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~';
        if (safe) {
            out += (char)c;
        } else {
            out += '%';
            out += hex[c >> 4];
            out += hex[c & 0x0F];
        }
    }
    return out;
}

std::string httpGet(const std::string& url, std::string* error) {
    // WinINet 是随系统带的，不用额外依赖；这也是 GUI 能单文件编译的前提。
    HINTERNET session = InternetOpenA("musiccil-gui/1.0", INTERNET_OPEN_TYPE_PRECONFIG, 0, 0, 0);
    if (!session) {
        if (error) *error = lastErrorText("无法初始化网络（InternetOpen 失败）");
        return "";
    }
    DWORD timeout = 15000;
    InternetSetOptionA(session, INTERNET_OPTION_CONNECT_TIMEOUT, &timeout, sizeof(timeout));
    InternetSetOptionA(session, INTERNET_OPTION_RECEIVE_TIMEOUT, &timeout, sizeof(timeout));

    std::wstring wurl = toWide(url);
    HINTERNET conn = InternetOpenUrlW(session, wurl.c_str(), 0, 0,
                                      INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE |
                                          INTERNET_FLAG_SECURE,
                                      0);
    if (!conn) {
        if (error) *error = lastErrorText("连接服务器失败");
        InternetCloseHandle(session);
        return "";
    }

    std::string body;
    char buf[8192];
    DWORD read = 0;
    while (InternetReadFile(conn, buf, sizeof(buf), &read) && read > 0) {
        body.append(buf, read);
    }
    InternetCloseHandle(conn);
    InternetCloseHandle(session);
    return body;
}

MusicApi::MusicApi(const std::string& key) {
    if (!key.empty()) {
        key_ = key;
    } else {
        key_ = envOr("MUSICCIL_API_KEY", "");
        if (key_.empty()) key_ = keyFromFile();
    }
}

bool MusicApi::search(const std::string& keyword, const std::string& platform, int limit,
                      std::vector<Track>* out, std::string* error) {
    out->clear();
    if (key_.empty()) {
        if (error) {
            *error =
                "没有找到接口密钥。免费领取：" + std::string(kApiSite) +
                "（注册后页面里可复制）。"
                "也可以让播放器代你保存：musiccil --setup --key <密钥>，"
                "或把密钥写进 %USERPROFILE%\\.musiccil\\key（一行，只有密钥本身）。";
        }
        return false;
    }
    // 多词关键词接口常直接回"获取失败"（单关键词就正常），这里逐级丢词退化重试。
    std::vector<std::string> attempts;
    attempts.push_back(keyword);
    size_t sp = keyword.find(' ');
    if (sp != std::string::npos) {
        attempts.push_back(keyword.substr(0, sp));
        size_t pos = 0;
        while (pos <= keyword.size() && pos != std::string::npos) {
            size_t next = keyword.find(' ', pos);
            std::string w = keyword.substr(pos, next == std::string::npos ? std::string::npos : next - pos);
            if (!w.empty()) attempts.push_back(w);
            if (next == std::string::npos) break;
            pos = next + 1;
        }
    }

    std::string lastError = "没有结果";
    for (size_t a = 0; a < attempts.size(); a++) {
        std::string url = apiBase() + "search?key=" + urlEncode(key_) +
                          "&name=" + urlEncode(attempts[a]) +
                          "&type=" + urlEncode(platform) +
                          "&page=1&limit=" + json::Value::num2str(limit) + "&format=1";
        std::string body = httpGet(url, error);
        if (body.empty()) continue;
        bool ok = false;
        json::Value root = json::parse(body, &ok);
        if (!ok) { lastError = "返回内容无法解析"; continue; }
        std::string code = root.str("code");
        if (code != "1" && code != "200") {
            lastError = root.str("msg", "接口返回失败");
            continue;
        }
        const json::Value& data = root.at("data");
        if (!data.isArray()) { lastError = "返回数据格式异常"; continue; }
        for (size_t k = 0; k < data.items.size(); k++) {
            const json::Value& item = data.items[k];
            if (!item.isObject()) continue;
            Track t;
            t.id = item.str("id");
            t.type = item.str("type", platform);
            t.name = item.str("name");
            t.album = item.str("album");
            // artist 可能是字符串也可能是数组
            const json::Value& ar = item.at("artist");
            if (ar.isString()) {
                t.artist = ar.text;
            } else if (ar.isArray()) {
                for (size_t j = 0; j < ar.items.size(); j++) {
                    if (!t.artist.empty()) t.artist += "/";
                    t.artist += ar.items[j].text;
                }
            }
            if (t.id.empty() || t.name.empty()) continue;
            out->push_back(t);
        }
        if (!out->empty()) return true;
        lastError = "没有搜到结果";
    }
    if (error) *error = lastError;
    return false;
}

bool MusicApi::resolve(Track* track, std::string* error) {
    std::string url = apiBase() + "info?key=" + urlEncode(key_) +
                      "&id=" + urlEncode(track->id) + "&type=" + urlEncode(track->type) +
                      "&url=1";
    std::string body = httpGet(url, error);
    if (body.empty()) return false;
    bool ok = false;
    json::Value root = json::parse(body, &ok);
    if (!ok) {
        if (error) *error = "返回内容无法解析";
        return false;
    }
    std::string code = root.str("code");
    if (code != "1" && code != "200") {
        if (error) *error = root.str("msg", "取详情失败");
        return false;
    }
    const json::Value& data = root.at("data");
    if (!data.isObject()) {
        if (error) *error = "详情格式异常";
        return false;
    }
    track->url = data.str("url");
    std::string dur = data.str("duration");
    if (!dur.empty()) track->duration = dur;
    if (track->url.empty()) {
        if (error) *error = "这首没有可用的音频直链（多为版权限制）";
        return false;
    }
    return true;
}
