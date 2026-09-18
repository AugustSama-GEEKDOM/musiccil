// 音乐接口客户端（WinINet）+ 歌单模型。
#pragma once

#include <string>
#include <vector>

struct Track {
    std::string id;
    std::string type;      // wy / qq / kg / kw ...
    std::string name;
    std::string artist;
    std::string album;
    std::string url;
    std::string duration;
};

// UTF-8 <-> UTF-16：Win32 的宽字符 API 需要
std::wstring toWide(const std::string& utf8);
std::string toUtf8(const std::wstring& wide);

// URL 百分号编码（UTF-8 字节逐个转义），用于把关键词安全拼进 query。
std::string urlEncode(const std::string& utf8);

// 发起 GET 并返回响应体。失败时返回空串并填充 error。
std::string httpGet(const std::string& url, std::string* error);

class MusicApi {
public:
    explicit MusicApi(const std::string& key = "");

    // 关键词搜索。brief=true 时跳过详情请求，只拿列表所需字段（快、省配额）。
    bool search(const std::string& keyword, const std::string& platform, int limit,
                std::vector<Track>* out, std::string* error);

    // 取一首歌的音频直链/时长。
    bool resolve(Track* track, std::string* error);

    const std::string& key() const { return key_; }

private:
    std::string key_;
};
