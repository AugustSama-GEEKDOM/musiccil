# 音乐接口速查

客户端用的基址是**本项目的 musiccil API 调用站**：`https://muscil.geekdom.top/open/music/`。
全部 GET/POST、返回 JSON、用 `key` 鉴权。
路径、参数、响应形态都按下面这套约定，服务端只用请求里带的 `key` 鉴权。
密钥在 <https://muscil.geekdom.top> 注册后领取；配额用完后可在面板签到或用兑换码补。

播放器已经封装好这些接口；直接调用的场合主要是排查问题和理解数据来源。
需要指向别的部署时用 `MUSICCIL_API_BASE` 覆盖基址。

## 校验密钥（不扣配额）

```
GET /api/key-info?key=你的密钥
→ {"ok":true,"quota":183,"email":"you@example.com","granted_today":0,"site":"https://muscil.geekdom.top"}
→ {"ok":false,"msg":"密钥无效，请到 ... 重新获取"}
```

配置密钥时用它确认这把钥匙能用、还剩多少配额。这个接口不扣配额。

| 端点 | 作用 | 必填参数 | 可选 |
|---|---|---|---|
| `search` | 按歌名搜索 | `key,name,type` | `page,limit,format` |
| `info` | 按 ID 取详情 | `key,id,type` | `url,pic,lrc` |
| `url` | 只取 MP3 地址 | `key,id,type` | — |
| `pic` | 取封面地址 | `key,id,type` | — |
| `lrc` | 取歌词 | `key,id,type` | `t`（翻译）,`ksc` |
| `list` | 歌单歌曲 | `key,id,type` | `format` |
| `toplist` | 排行榜歌曲 | `key,id,type` | `format` |
| `userlist` | 用户歌单 | `key,uid,type` | `format` |

平台代号：`wy` 网易云 · `qq` QQ音乐 · `kw` 酷我 · `kg` 酷狗 · `mg` 咪咕 · `qi` 千千 · `my` 明月浩空。
`toplist` 仅 `wy/qq/kw`；`userlist` 仅 `wy/qq/my`。

## 必须容忍的不一致

1. **失败也是 HTTP 200**，只能看 body 里的 `code`。
2. **成功码不统一**：多数接口是 `1`，`userlist` 是 `200`。
3. **失败时 `data` 类型不定**：可能是 `""`、`"[]"`、`"{}"`、`null` 或对象。
4. **搜索必须带 `format=1`** 才是精简结构；`format=0` 返回各平台原始结构（`result.songs`，含大量无用字段）。
5. **`userlist` 的返回不在 `data` 里**，而是顶层 `playlist` 数组。
6. **`list` 的成功返回比文档多** `data.name` 和 `data.cover`。

## 数据形态

`search`（`format=1`）→ `data[]`，每项 `{type,id,name,album,artist,pic_id}`；`artist` 可能是字符串也可能是数组。

`info` → `data{name,album,artist,picid,url,pic,lrc}`。

`list` / `toplist` → `data` 是**并行数组**，不是对象数组：
`songId[]`、`songName[]`、`albumName[]`、`artistName[]`、`type[]`，需要按下标配对，
且数组长度可能不一致（缺失项按空值处理）。

## 时效与配额

- `url`（MP3）和 `pic`（封面）都是带签名/时间戳的 CDN 地址，**会过期**，不要长期缓存或分享。
  封面地址形如 `...?param=800y800`（文档示例写的是 300，实测返回 800）。
- 配额有固定包量（按账号计），耗尽时返回
  `{"code":0,"msg":"配额已用完：请到 ... 签到或使用兑换码补充"}`。
- 数据源偶发超时/5xx 时会**退还**这一次的配额，只返回错误；重试不会白花配额。
