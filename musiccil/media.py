"""系统媒体控制（Windows SMTC）：把当前曲目信息送进系统媒体通知，
并接收系统/键盘媒体键的回调。

为什么由播放器自己接管而不是靠 mpv：
mpv 虽然也注册媒体会话，但它只给出标题，拿不到歌手/专辑/封面
（实测 ``try_get_media_properties_async`` 里 artist 为空、thumbnail 为 None），
而且系统按键只会落在 mpv 上、播放器界面收不到。所以这里用 winsdk 的
``MediaPlayer`` 借一条 SMTC 通道，**只用来显示信息与收按键**，音频依旧由 mpv 播。

两个容易踩的坑：

1. **必须泵消息**：WinRT 的按键回调靠 Windows 消息队列派发，只 ``sleep`` 是收不到的，
   主循环里要周期性调用 :meth:`MediaSession.pump`。
2. **不能真的播放**：这个 ``MediaPlayer`` 只当通道用，一旦让它播音频就会和 mpv 抢声音。
   所以只设元数据、状态和时间轴，绝不调 ``play()``。

依赖是可选的：没装 ``winsdk`` 或不在 Windows 上时，整个模块静默降级成空实现，
播放器照常用。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import datetime
import os
from typing import Callable, Optional

from .api import Track

#: 系统媒体键 → 播放器内部动作名
BUTTON_ACTIONS = {
    "PLAY": "play",
    "PAUSE": "pause",
    "STOP": "stop",
    "NEXT": "next",
    "PREVIOUS": "previous",
    "FAST_FORWARD": "forward",
    "REWIND": "rewind",
}


def available() -> bool:
    """本机能否使用系统媒体控制。"""
    if os.name != "nt":
        return False
    try:
        import winsdk.windows.media.playback as _playback
    except Exception:
        return False
    return hasattr(_playback, "MediaPlayer")


class MediaSession:
    """系统媒体通知的读写通道。

    ``on_action`` 收到的是 :data:`BUTTON_ACTIONS` 里的动作名（play/pause/next…）。
    """

    def __init__(self, on_action: Optional[Callable[[str], None]] = None):
        self.enabled = False
        self.on_action = on_action
        self._player = None
        self._smtc = None
        self._last_key = None
        self._hook: Optional[Callable] = None
        if not available():
            return
        try:
            self._start()
        except Exception:
            # 系统媒体通道起不来不能拖垮播放器，静默降级
            self.enabled = False
            self._player = None
            self._smtc = None

    # ------------------------------------------------------------------ 启动
    def _start(self) -> None:
        from winsdk.windows.media import MediaPlaybackType
        from winsdk.windows.media.playback import MediaPlayer

        player = MediaPlayer()
        # 只当通道用：不让它碰音频，否则会和 mpv 抢声音
        try:
            player.audio_category = 3          # MediaPlayerAudioCategory.MEDIA
        except Exception:
            pass
        try:
            player.command_manager.is_enabled = False
        except Exception:
            pass

        smtc = player.system_media_transport_controls
        smtc.is_enabled = True
        for flag in ("is_play_enabled", "is_pause_enabled", "is_next_enabled",
                     "is_previous_enabled", "is_stop_enabled"):
            try:
                setattr(smtc, flag, True)
            except Exception:
                pass
        try:
            smtc.display_updater.type = MediaPlaybackType.MUSIC
        except Exception:
            pass

        # 回调签名是 (sender, args)；用闭包把事件转成动作名，
        # 同时**保持引用**，否则回调会被 GC 掉、按键再也不触发。
        def handler(sender, args):
            # 注意：WinRT 传回来的是枚举对象，str() 得到的是 "0"/"6" 这种数字，
            # 名字在 .name 上（"PLAY"/"NEXT"）。用 str() 会全部匹配不上而静默失效。
            button = getattr(args, "button", None)
            name = getattr(button, "name", None) or str(button or "").split(".")[-1]
            action = BUTTON_ACTIONS.get(name)
            if action and self.on_action is not None:
                self.on_action(action)

        self._hook = handler
        smtc.add_button_pressed(handler)
        self._player = player
        self._smtc = smtc
        self.enabled = True

    # ------------------------------------------------------------------ 信息
    def update(
        self,
        track: Track,
        playing: bool,
        position: float = 0.0,
        duration: float = 0.0,
    ) -> None:
        """把当前曲目写进系统媒体通知（标题/歌手/专辑/封面/进度/播放状态）。"""
        if not self.enabled or self._smtc is None:
            return
        try:
            updater = self._smtc.display_updater
            props = updater.music_properties
            props.title = track.name or "未知曲目"
            props.artist = track.display_artist or "未知歌手"
            props.album_title = track.album or ""
            if track.pic:
                self._set_thumbnail(updater, track.pic)
            updater.update()
            self._set_status(playing)
            self._set_timeline(position, duration)
        except Exception:
            # 通知栏偶发失败不影响播放
            pass

    def _set_thumbnail(self, updater, url: str) -> None:
        """封面：系统只吃流引用，直接把 CDN 地址包成 URI 引用即可。"""
        key = (id(updater), url)
        if getattr(self, "_thumb_key", None) == key:
            return
        try:
            from winsdk.windows.foundation import Uri
            from winsdk.windows.storage.streams import RandomAccessStreamReference

            updater.thumbnail = RandomAccessStreamReference.create_from_uri(Uri(url))
            self._thumb_key = key
        except Exception:
            self._thumb_key = None

    def _set_status(self, playing: bool) -> None:
        from winsdk.windows.media import MediaPlaybackStatus

        want = MediaPlaybackStatus.PLAYING if playing else MediaPlaybackStatus.PAUSED
        if self._smtc.playback_status != want:
            self._smtc.playback_status = want

    def _set_timeline(self, position: float, duration: float) -> None:
        from winsdk.windows.media import SystemMediaTransportControlsTimelineProperties as TL

        if not duration or duration <= 0:
            return
        # 每秒刷几次就够了，没必要每帧都推
        key = int(position)
        if getattr(self, "_timeline_key", None) == key:
            return
        self._timeline_key = key
        tl = TL()
        tl.start_time = datetime.timedelta(0)
        tl.position = datetime.timedelta(seconds=max(0.0, position))
        tl.min_seek_time = datetime.timedelta(0)
        tl.end_time = datetime.timedelta(seconds=duration)
        tl.max_seek_time = datetime.timedelta(seconds=duration)
        self._smtc.update_timeline_properties(tl)

    def clear(self) -> None:
        if not self.enabled or self._smtc is None:
            return
        try:
            self._smtc.display_updater.clear_all()
            self._smtc.playback_status = 0      # CLOSED
        except Exception:
            pass

    # ------------------------------------------------------------------ 消息泵
    def pump(self) -> None:
        """派发 Windows 消息，让系统媒体键的回调能进来。

        WinRT 的事件走窗口消息队列，主循环只 sleep 的话回调永远不触发。
        用 PeekMessage 非阻塞地抽干队列，不阻塞界面。
        """
        if not self.enabled:
            return
        try:
            user32 = ctypes.windll.user32
            msg = ctypes.wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self.enabled = False

    def close(self) -> None:
        if self._smtc is not None:
            try:
                self._smtc.display_updater.clear_all()
                self._smtc.is_enabled = False
            except Exception:
                pass
        self._player = None
        self._smtc = None
        self.enabled = False
