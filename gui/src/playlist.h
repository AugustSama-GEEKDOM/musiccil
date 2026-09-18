// 歌单落盘 + 调用终端播放器。
#pragma once

#include <string>
#include <vector>

#include "api.h"

// 把曲目写成终端播放器能读的 JSON 歌单，返回实际路径（失败返回空串）。
// 只写 id/type/name/artist/album —— 音频直链有时效，交给播放器自己重新解析，
// 这样歌单可以长期保存、下次打开仍然能放。
std::string savePlaylist(const std::vector<Track>& tracks, const std::string& path,
                         std::string* error);

// 默认歌单路径：%LOCALAPPDATA%\musiccil-gui\playlist.json
std::string defaultPlaylistPath();

// 在独立终端窗口里打开播放器播放该歌单。
// 返回进程 id，失败返回 0。
unsigned long launchPlayer(const std::string& playlistPath, const std::string& extraArgs,
                           std::string* error);

// 探测终端播放器怎么调用：优先 `python -m musiccil`，找不到 python 时试 PATH 上的 musiccil。
std::string playerCommand();
