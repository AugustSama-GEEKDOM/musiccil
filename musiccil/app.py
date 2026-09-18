"""播放器主循环：键盘控制、主题跟随封面、mpv 状态同步。"""

from __future__ import annotations

import random
import os
import sys
import time
from typing import Callable, List, Optional

from . import keys, media, term, window
from .api import Track
from .art import CoverCache, open_cover, palette
from .lrc import parse
from .player import Mpv
from .theme import DEFAULT_SCENE, SCENES, Theme, blend, preset, theme_from_cover
from .ui import MODES, Renderer, Scene

#: 唱片转速（度/秒）。一圈 360°：55°/s ≈ 6.5 秒一圈（太急），25°/s ≈ 14 秒，
#: 15°/s ≈ 24 秒——接近真实黑胶的从容感，外圈光泽扫过时也看得清。
SPIN_PER_SEC = 15.0
#: 换曲时主题配色的过渡时长（秒）。直接切换的话整屏颜色会"啪"一下跳过去；
#: 给颜色留出这点时间，视觉上是"染"过去的，但也不能拖太久显得迟钝。
THEME_FADE = 0.9
#: 切歌/跳转时的音频淡入淡出时长（秒）。有它声音就不会被硬切，
#: 但也得短——用户按了下一首还要等半秒才开始放新歌。
AUDIO_FADE = 0.22
SEEK_STEP = 5.0
TICK = 1.0 / 30.0


class App:
    def __init__(
        self,
        tracks: List[Track],
        renderer: Optional[Renderer] = None,
        no_audio: bool = False,
        offline: bool = False,
        volume: int = 80,
        favorites: Optional[set] = None,
        spin: Optional[float] = None,
        theme_fade: Optional[float] = None,
        media_controls: bool = True,
        audio_fade: Optional[float] = None,
    ):
        self.tracks = tracks
        self.index = 0
        self.no_audio = no_audio
        self.offline = offline
        self.renderer = renderer or Renderer()
        self.cache = CoverCache(offline=offline)
        self.favorites = favorites if favorites is not None else set()
        self.spin = float(spin) if spin else SPIN_PER_SEC
        # 允许 0：那就是"不要过渡"，直接换色
        self.theme_fade = THEME_FADE if theme_fade is None else max(0.0, float(theme_fade))
        # 允许 0：那就是不要音频过渡，切歌立刻切换
        self.audio_fade = AUDIO_FADE if audio_fade is None else max(0.0, float(audio_fade))
        self.scene = Scene(volume=volume)
        self.scene.queue = [(t.name or "—", t.display_artist) for t in tracks]
        self.scene.theme = preset(DEFAULT_SCENE)
        self._auto_theme = preset(DEFAULT_SCENE)
        self._custom_theme = None
        self._theme_key = None
        #: 配色过渡状态：从 _fade_from 染到 _fade_to，_fade_progress 走 0→1
        self._fade_from = self._auto_theme
        self._fade_to = self._auto_theme
        self._fade_progress = 1.0
        self._fade_elapsed = 0.0
        #: 封面交叉淡入的进度（与配色各走各的：主题可能没变但封面换了）
        self._cover_fade_progress = 1.0
        self._cover_fade_elapsed = 0.0
        self._cover_img = None
        self._art_ready = False
        self._title_state = None
        self.player: Optional[Mpv] = None
        if not no_audio:
            # 我们自己发系统媒体通知，就让 mpv 别抢：否则会出现两个会话
            self.player = Mpv(volume=volume, media_controls=not media_controls)
        # 系统媒体通知/媒体键：拿不到就自动降级，不影响播放
        # --no-audio 没有真实播放源，往系统通知栏发"正在播放"是误导，也一并关掉
        use_media = media_controls and not no_audio
        self.media = media.MediaSession(self._on_media_action) if use_media else None
        self._media_key = None
        self._media_beat = 0.0
        self._want_quit = False
        #: 音频过渡状态机：idle（无过渡）/ out（正在淡出）/ in（切换完正在淡入）
        self._audio_fade_state = "idle"
        self._audio_fade_pending: Optional[Callable[[], None]] = None
        self._audio_fade_elapsed = 0.0

    @property
    def track(self) -> Track:
        if not self.tracks:
            return Track()
        self.index = max(0, min(self.index, len(self.tracks) - 1))
        return self.tracks[self.index]

    # ------------------------------------------------------------ 主题 / 封面
    def _sync_theme(self) -> None:
        track = self.track
        key = "%s:%s:%s" % (track.type, track.id, track.pic)
        if key == self._theme_key:
            return
        self._theme_key = key
        self._cover_img = None
        self._art_ready = False
        data = self.cache.get(key, track.pic)
        theme = preset(DEFAULT_SCENE)
        if data:
            try:
                img = open_cover(data)
            except Exception:
                img = None
            if img is not None:
                self._cover_img = img
                self._art_ready = True
                try:
                    theme = theme_from_cover(track.name or key, palette(img))
                except Exception:
                    theme = preset(DEFAULT_SCENE)
        # 封面换了：启动一次交叉淡入，让旧封面渐渐溶进新封面
        self._cover_fade_progress = 0.0
        self._cover_fade_elapsed = 0.0
        self._auto_theme = theme
        self._apply_theme()

    @property
    def _theme_target(self) -> Theme:
        """当前应该用的主题：手动按 R 选的优先，否则跟随封面。"""
        return self._custom_theme or self._auto_theme

    def _apply_theme(self) -> None:
        """目标主题变了：从当前屏幕上的配色开始渐变过去，而不是直接跳变。"""
        target = self._theme_target
        if target == self.scene.theme:
            return
        self._fade_from = self.scene.theme
        self._fade_to = target
        self._fade_progress = 0.0
        self._fade_elapsed = 0.0
        self.renderer.invalidate()

    def _advance_theme_fade(self, dt: float) -> None:
        """主循环里按时间推进配色过渡；过渡结束后就固定在目标主题上。"""
        if self._fade_progress >= 1.0:
            return
        if self.theme_fade <= 0.0:
            self.snap_theme()
            return
        self._fade_elapsed += dt
        self._fade_progress = min(1.0, self._fade_elapsed / self.theme_fade)
        t = self._fade_progress
        # 缓入缓出（smoothstep）：头尾都慢、中段快，比线性更有"渐染"的感觉
        eased = t * t * (3.0 - 2.0 * t)
        self.scene.theme = blend(self._fade_from, self._fade_to, eased)
        term.set_term_bg(self.scene.theme.bg_main)

    def snap_theme(self) -> None:
        """立刻跳到目标配色，不做过渡。

        给 ``--render-once`` 这类只出一帧的场景用：没有后续帧可以推进渐变，
        停在中间色上就等于渲染错了。这里必须重新取一次目标主题，不能直接用
        ``_fade_to``——目标没变时 ``_apply_theme`` 会提前返回，那个字段可能是旧的。
        """
        self._fade_to = self._theme_target
        self._fade_from = self._fade_to
        self._fade_progress = 1.0
        self.scene.theme = self._fade_to
        term.set_term_bg(self.scene.theme.bg_main)

    def _advance_cover_fade(self, dt: float) -> float:
        """推进封面交叉淡入，返回当前进度（0=上一张封面，1=当前曲目）。"""
        if self._cover_fade_progress >= 1.0:
            return 1.0
        if self.theme_fade <= 0.0:
            # 用户把过渡关掉了（--theme-fade 0）：封面也一起直接换
            self._cover_fade_progress = 1.0
            return 1.0
        self._cover_fade_elapsed += dt
        self._cover_fade_progress = min(1.0, self._cover_fade_elapsed / self.theme_fade)
        t = self._cover_fade_progress
        # 和配色用同一条 smoothstep，两者的节奏才对得上
        return t * t * (3.0 - 2.0 * t)

    # ---------------------------------------------------------------- 播放
    def load_current(self, notice: bool = True) -> None:
        track = self.track
        self._sync_theme()
        self.scene.title = track.name or "未知曲目"
        self.scene.artist = track.display_artist or "未知歌手"
        self.scene.album = track.album or ""
        self.scene.source = track.platform if track.type else "本地"
        self.scene.position = 0.0
        self.scene.duration = track.duration or 0.0
        self.scene.favorite = bool(track.id) and track.id in self.favorites
        self.scene.queue_index = self.index
        self.scene.lyrics = parse(track.lrc) if track.lrc else []
        self._update_window_title()
        if self.player is None:
            return
        if not track.url:
            if notice:
                self.notice("这首没有可用的音频直链")
            return
        try:
            self.player.load(track.url)
        except OSError as exc:
            self.notice("播放失败：%s" % exc)
            return
        if track.duration:
            self.scene.duration = track.duration
        if notice:
            self.notice("正在播放：%s" % (track.name or track.url), 2.0)

    def next_track(self) -> None:
        if not self.tracks:
            return
        if self.scene.mode == 1 and len(self.tracks) > 1:
            nxt = random.randrange(len(self.tracks))
            if nxt == self.index:
                nxt = (nxt + 1) % len(self.tracks)
            target = nxt
        else:
            target = (self.index + 1) % len(self.tracks)
        self._switch_to(target)

    def prev_track(self) -> None:
        if not self.tracks:
            return
        if self.player is not None and self.player.position > 4.0:
            # 回开头也走淡入淡出，免得"啪"一下跳回
            self.with_audio_fade(lambda: self.player and self.player.seek_absolute(0.0))
            self.notice("回到开头", 1.2)
            return
        self._switch_to((self.index - 1) % len(self.tracks))

    def _switch_to(self, index: int) -> None:
        """换到指定下标的曲目，并给音频加上淡出淡入。"""
        # 逻辑位置立刻更新、只有"加载音频"这一步被推迟：
        # 否则连按下一首时 index 还没动，第二次又会算出同一个目标，两下只走一首。
        self.index = index
        self.with_audio_fade(self.load_current)

    def notice(self, message: str, seconds: float = 2.5) -> None:
        self.scene.notice = message
        self.scene.notice_until = time.time() + seconds if message else 0.0

    # ------------------------------------------------------------ 音频过渡
    def _fade_gain_out(self) -> None:
        """立刻把增益降到静音（配合下面的淡出步进使用）。"""
        if self.player is not None:
            self.player.set_gain(self.player.SILENT_GAIN)

    def with_audio_fade(self, action: Callable[[], None]) -> None:
        """先淡出、再执行 ``action``、然后淡入。

        换歌和快进快退都是瞬间切音频，直接做会有"啪"的一声断口。这里不阻塞界面：
        记下待执行的动作，由主循环按帧推进，淡出走完才真正切，切完再淡入。

        过渡期间重复触发（用户连按下一首）就直接执行新的动作并重启淡入，
        不会排起长队——连按时用户要的是"立刻换"，不是慢慢排队。
        """
        if self.player is None or self.audio_fade <= 0.0:
            action()
            return
        if self._audio_fade_state == "out":
            # 上一轮还没切就又被按下。已经静音了，所以别再执行未完成的那次切换
            # （那会把用户已经越过的那首重新加载一遍），直接换成新的目标。
            self._audio_fade_pending = None
        self._audio_fade_state = "out"
        self._audio_fade_pending = action
        self._audio_fade_elapsed = 0.0
        self.player.set_gain(self.player.SILENT_GAIN)

    def _advance_audio_fade(self, dt: float) -> None:
        """按帧推进音频过渡：静音 → 执行切换 → 淡回原增益。"""
        if self.player is None or self.audio_fade <= 0.0:
            return
        half = self.audio_fade / 2.0
        if self._audio_fade_state == "out":
            self._audio_fade_elapsed += dt
            if self._audio_fade_elapsed >= half:
                # 淡出到这里就算静了，此时切换不会再听到断口
                action, self._audio_fade_pending = self._audio_fade_pending, None
                if action is not None:
                    action()
                self._audio_fade_state = "in"
                self._audio_fade_elapsed = 0.0
        elif self._audio_fade_state == "in":
            self._audio_fade_elapsed += dt
            t = min(1.0, self._audio_fade_elapsed / half)
            self.player.set_gain(self.player.SILENT_GAIN * (1.0 - t))
            if t >= 1.0:
                self.player.set_gain(0.0)
                self._audio_fade_state = "idle"

    def _update_window_title(self) -> None:
        """把窗口标题设成当前曲目，独立窗口/任务栏里一眼能看到在放什么。"""
        if self._title_state == (self.scene.title, self.scene.artist):
            return
        self._title_state = (self.scene.title, self.scene.artist)
        label = self.scene.title or "musiccil"
        if self.scene.artist and self.scene.artist != "—":
            label = "%s · %s" % (label, self.scene.artist)
        term.set_title("♪ %s  —  musiccil" % label)

    def cleanup(self) -> None:
        if self.player is not None:
            self.player.quit()
        if self.media is not None:
            self.media.close()

    # ---------------------------------------------------- 系统媒体控制（SMTC）
    def _on_media_action(self, action: str) -> None:
        """系统媒体通知/键盘媒体键按下时进来，映射成和界面按键一样的行为。"""
        if action == "play":
            if self.player is not None and self.player.paused:
                self.player.toggle_pause()
        elif action == "pause":
            if self.player is not None and not self.player.paused:
                self.player.toggle_pause()
        elif action == "stop":
            self._want_quit = True
        elif action == "next":
            self.next_track()
        elif action == "previous":
            self.prev_track()
        elif action == "forward":
            if self.player is not None:
                self.with_audio_fade(lambda: self.player and self.player.seek(SEEK_STEP))
        elif action == "rewind":
            if self.player is not None:
                self.with_audio_fade(lambda: self.player and self.player.seek(-SEEK_STEP))

    def _sync_media_session(self, force: bool = False) -> None:
        """把当前曲目和播放状态同步到系统媒体通知。

        只在曲目/播放状态/整秒进度变化时才写，避免每帧都戳一遍系统接口。
        """
        if self.media is None or not self.media.enabled:
            return
        track = self.track
        key = (track.type, track.id, track.name, self.scene.playing, int(self.scene.position))
        if not force and key == self._media_key:
            return
        self._media_key = key
        self.media.update(
            track, self.scene.playing, self.scene.position, self.scene.duration
        )

    # ---------------------------------------------------------------- 按键
    def handle(self, key: Optional[str]) -> bool:
        scene = self.scene
        if key is None:
            return True
        if key in ("Q", "ESC"):
            return False
        if key == "SPACE":
            if self.player is not None:
                self.player.toggle_pause()
        elif key == "LEFT":
            if self.player is not None:
                self.with_audio_fade(lambda: self.player and self.player.seek(-SEEK_STEP))
                self.notice("快退 %ds" % int(SEEK_STEP), 1.0)
        elif key == "RIGHT":
            if self.player is not None:
                self.with_audio_fade(lambda: self.player and self.player.seek(SEEK_STEP))
                self.notice("快进 %ds" % int(SEEK_STEP), 1.0)
        elif key == "UP":
            if self.player is not None:
                self.notice("音量 %d%%" % self.player.adjust_volume(5), 1.0)
        elif key == "DOWN":
            if self.player is not None:
                self.notice("音量 %d%%" % self.player.adjust_volume(-5), 1.0)
        elif key == "M":
            if self.player is not None:
                self.player.set_mute(not self.player.muted)
                self.notice("已静音" if self.player.muted else "取消静音", 1.2)
        elif key == "N":
            self.next_track()
        elif key == "P":
            self.prev_track()
        elif key == "H":
            track = self.track
            if not track.id:
                return True
            if track.id in self.favorites:
                self.favorites.discard(track.id)
                scene.favorite = False
                self.notice("已取消红心")
            else:
                self.favorites.add(track.id)
                scene.favorite = True
                self.notice("已加入红心")
        elif key == "S":
            scene.mode = (scene.mode + 1) % len(MODES)
            self.notice("播放模式：%s" % MODES[scene.mode], 1.2)
        elif key == "L":
            scene.view = "lyrics" if scene.view != "lyrics" else "player"
        elif key == "T":
            scene.view = "list" if scene.view != "list" else "player"
        elif key == "R":
            order = ["自动"] + list(SCENES)
            current = self._custom_theme.name if self._custom_theme else "自动"
            nxt = order[(order.index(current) + 1) % len(order)]
            if nxt == "自动":
                self._custom_theme = None
                self.notice("主题：跟随封面")
            else:
                self._custom_theme = preset(nxt)
                self.notice("主题：%s" % nxt)
            self._apply_theme()
        return True

    # ------------------------------------------------------------ 主循环
    def run(self) -> int:
        term.init_console()
        # 由 --window 启动时，若走的是裸控制台（没有 Windows Terminal），
        # 在这里把窗口调成父进程要求的竖屏尺寸
        if os.environ.get(window.APPLY_SIZE_ENV):
            window.apply_console_size()
        restore = keys.raw_mode()
        cols, rows = term.term_size()
        self.renderer.resize(cols, rows)
        term.enter_screen(self.scene.theme.bg_main)
        last = time.time()
        last_size = (cols, rows)
        try:
            if self.tracks:
                self.load_current()
                if not self.scene.notice:
                    self.notice("就绪  ·  按 Q 退出", 2.0)
            else:
                self.notice("没有可播放的曲目，按 Q 退出", 9999)
            while True:
                now = time.time()
                dt = min(0.25, max(0.0, now - last))
                last = now

                if self.player is not None:
                    self.player.poll()
                    if self.player.ended:
                        err = self.player.last_file_error()
                        self.player.take_events()
                        if err:
                            self.notice("播放出错：%s" % err, 3.0)
                        self.next_track()
                    if self.player.position:
                        self.scene.position = self.player.position
                    if self.player.duration:
                        self.scene.duration = self.player.duration
                    self.scene.playing = not self.player.paused and not self.player.idle
                    self.scene.volume = self.player.volume
                    self.scene.muted = self.player.muted
                    self.scene.queue_index = self.index

                if self.scene.playing:
                    self.scene.angle = (self.scene.angle + self.spin * dt) % 360.0
                elif self.player is None and self.tracks:
                    # --no-audio：没有真实播放源，用模拟进度驱动界面，便于演示与截图
                    self.scene.playing = True
                    self.scene.angle = (self.scene.angle + self.spin * dt) % 360.0
                    total = self.scene.duration or 0.0
                    if total:
                        self.scene.position = (self.scene.position + dt) % total
                    else:
                        self.scene.position += dt

                size = term.term_size()
                if size != last_size:
                    last_size = size
                    self.renderer.resize(*size)

                self._advance_theme_fade(dt)
                cover_fade = self._advance_cover_fade(dt)
                self._advance_audio_fade(dt)
                # 系统媒体键的回调靠 Windows 消息队列派发，必须周期性泵消息
                if self.media is not None:
                    self.media.pump()
                self._sync_media_session()
                self.renderer.vinyl(
                    self.scene.theme,
                    self._cover_img if self._art_ready else None,
                    self.scene.theme.accent,
                    (self._theme_key, self.renderer.cells),
                    cover_fade=cover_fade,
                )
                sys.stdout.write("\x1b[H" + self.renderer.render(self.scene) + "\x1b[J")
                sys.stdout.flush()

                if self._want_quit or not self.handle(keys.get_key()):
                    break
                time.sleep(TICK)
        except KeyboardInterrupt:
            pass
        finally:
            restore()
            self.cleanup()
            term.exit_screen()
        return 0
