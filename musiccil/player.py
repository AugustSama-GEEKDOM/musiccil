"""基于 mpv 的后台播放器：通过 JSON IPC 控制，UI 自己画。

Windows 上用 ``PeekNamedPipe`` 非阻塞读，避免和多线程读写同一管道时的 EINVAL。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from typing import Any, Dict, List, Optional

#: 注意：Windows 上 ``mpv.com`` 是个控制台包装器，会再拉起真正的 ``mpv.exe``，
#: 对它调用 terminate/kill 只会杀掉包装器、把真正的播放器变成孤儿进程。
#: 因此必须优先使用 ``mpv.exe``。
MPV_EXE = "mpv.exe" if os.name == "nt" else "mpv"


def find_mpv() -> Optional[str]:
    env = os.environ.get("MUSICCIL_MPV")
    if env and os.path.exists(env):
        return env
    found = shutil.which(MPV_EXE)
    if found:
        return found
    if os.name == "nt":
        # which("mpv") 会先命中 mpv.com，这里显式补一次 exe
        for name in ("mpv.exe",):
            alt = shutil.which(name)
            if alt:
                return alt
    candidates = [
        r"C:\Program Files\MPV Player\mpv.exe",
        r"C:\Program Files\mpv\mpv.exe",
        r"C:\Program Files (x86)\mpv\mpv.exe",
        os.path.expanduser(r"~\scoop\apps\mpv\current\mpv.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


class MpvError(RuntimeError):
    pass


if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
    ]
    _k32.PeekNamedPipe.restype = wintypes.BOOL
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _INVALID_HANDLE = wintypes.HANDLE(-1).value

    # --- Job Object：让 mpv 的命和播放器绑在一起 ---
    # 正常退出走 quit()，但用户直接关窗口、或在任务管理器里结束 python，
    # 都轮不到 Python 的清理代码执行，mpv 就会变成孤儿继续放歌。
    # 把子进程放进 job 并设 KILL_ON_JOB_CLOSE，句柄一关（进程无论怎么死）
    # 系统就会连它一起收掉。句柄刻意不在退出时关闭——让内核来关。
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE

    #: 进程级共享一个 job：所有 mpv 子进程都挂在它下面
    _shared_job = None


def _attach_to_job(pid: int) -> None:
    """把子进程挂进共享 job，父进程一死它就跟着结束。"""
    global _shared_job
    if os.name != "nt":
        return
    try:
        if _shared_job is None:
            job = _k32.CreateJobObjectW(None, None)
            if not job:
                return
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _k32.SetInformationJobObject(
                job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
            ):
                _k32.CloseHandle(job)
                return
            _shared_job = job
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        handle = _k32.OpenProcess(0x0100 | 0x0001, False, int(pid))
        if not handle:
            return
        try:
            _k32.AssignProcessToJobObject(_shared_job, handle)
        finally:
            _k32.CloseHandle(handle)
    except Exception:
        # 挂不上 job 也不该影响播放，正常退出路径照样会收掉 mpv
        pass


class Mpv:
    """mpv 子进程 + JSON IPC 客户端。

    属性缓存由 ``observe_property`` 事件维护，``poll()`` 时刷新。
    """

    def __init__(
        self,
        mpv_path: Optional[str] = None,
        volume: int = 80,
        muted: bool = False,
        audio_device: Optional[str] = None,
        start: float = 0.0,
        extra_args: Optional[List[str]] = None,
        media_controls: bool = True,
    ):
        self.path = mpv_path or find_mpv()
        if not self.path:
            raise MpvError("找不到 mpv，请先安装并确保 mpv 在 PATH 中（或用 MUSICCIL_MPV 指定路径）")
        self.pipe_name = r"\\.\pipe\musiccil_%d_%d" % (os.getpid(), int(time.time() * 1000) % 100000)
        self.props: Dict[str, Any] = {
            "time-pos": None, "duration": None, "pause": False,
            "volume": volume, "mute": muted, "media-title": None,
            "playlist-pos": None, "playlist-count": None, "idle-active": True,
        }
        self.events: List[Dict[str, Any]] = []
        self.started_at = start
        args = [
            self.path,
            "--no-video",
            "--no-terminal",
            "--idle=yes",
            "--keep-open=no",
            "--gapless-audio=yes",
            "--input-ipc-server=" + self.pipe_name,
            "--volume=%d" % int(volume),
            "--no-config",
        ]
        if muted:
            args.append("--mute=yes")
        if not media_controls:
            # 播放器自己已经接管了系统媒体通知（见 media.py）。mpv 默认也会注册
            # 一个只带标题、没有歌手/封面的会话，而且会一起抢媒体键，
            # 两个会话并存时按哪边都不确定，所以让 mpv 让出来。
            args.append("--no-media-controls")
            args.append("--input-media-keys=no")
        if audio_device:
            args.append("--audio-device=" + audio_device)
        if extra_args:
            args.extend(extra_args)
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _attach_to_job(self._proc.pid)
        self._handle: Optional[int] = None
        try:
            self._connect()
        except Exception:
            # 连不上 IPC 就别把 mpv 留在后台
            self.quit()
            raise
        self._observe()

    #: 需要持续同步的属性 -> 事件 id
    OBSERVED = (
        "time-pos", "duration", "pause", "volume", "mute",
        "idle-active", "playlist-pos", "playlist-count", "media-title",
    )

    def _observe(self) -> None:
        for ident, prop in enumerate(self.OBSERVED, start=1):
            try:
                self.command("observe_property", ident, prop)
            except OSError:
                pass

    # ------------------------------------------------------------ 管道底层
    def _connect(self, timeout: float = 10.0) -> None:
        if os.name != "nt":
            self._connect_posix(timeout)
            return
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            handle = _k32.CreateFileW(self.pipe_name, 0xC0000000, 0, None, 3, 0, None)
            if handle == _INVALID_HANDLE:
                time.sleep(0.05)
                continue
            # 建连后立刻探一次写入，确认管道真的就绪
            try:
                self._handle = handle
                self._write({"command": ["get_property", "mpv-version"]})
                return
            except OSError as exc:
                last = exc
                self._handle = None
                time.sleep(0.05)
        raise MpvError(f"无法连接 mpv IPC 管道：{last}")

    def _connect_posix(self, timeout: float) -> None:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                self._sock = None  # type: ignore[attr-defined]
                import socket

                path = self.pipe_name
                if path.startswith("\\\\.\\pipe\\"):
                    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), os.path.basename(path))
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(path)
                self._sock = sock  # type: ignore[attr-defined]
                self._sock.setblocking(False)  # type: ignore[attr-defined]
                return
            except OSError as exc:
                last = exc
                time.sleep(0.05)
        raise MpvError(f"无法连接 mpv IPC 套接字：{last}")

    def _write(self, payload: Dict[str, Any]) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        if os.name == "nt":
            written = wintypes.DWORD()
            if not _k32.WriteFile(self._handle, data, len(data), ctypes.byref(written), None):
                raise OSError(ctypes.get_last_error(), "WriteFile failed")
        else:
            try:
                self._sock.sendall(data)  # type: ignore[attr-defined]
            except OSError as exc:
                raise OSError(getattr(exc, "errno", 0), str(exc)) from exc

    def _read_available(self) -> bytes:
        if os.name == "nt":
            chunks = []
            while True:
                avail = wintypes.DWORD()
                if not _k32.PeekNamedPipe(
                    self._handle, None, 0, None, ctypes.byref(avail), None
                ):
                    break
                if avail.value == 0:
                    break
                buf = ctypes.create_string_buffer(min(avail.value, 1 << 16))
                read = wintypes.DWORD()
                if not _k32.ReadFile(
                    self._handle, buf, len(buf), ctypes.byref(read), None
                ):
                    break
                chunks.append(buf.raw[: read.value])
            return b"".join(chunks)
        try:
            return self._sock.recv(1 << 16)  # type: ignore[attr-defined]
        except (BlockingIOError, InterruptedError):
            return b""
        except OSError:
            return b""

    def poll(self) -> List[Dict[str, Any]]:
        """读取所有可用事件，更新属性缓存。"""
        data = self._read_available()
        if not data:
            return []
        self._buf = getattr(self, "_buf", b"") + data
        out: List[Dict[str, Any]] = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            out.append(event)
            if event.get("event") == "property-change" and event.get("name"):
                if "data" in event:
                    self.props[event["name"]] = event["data"]
            elif event.get("event") == "end-file":
                self.props["idle-active"] = True
            elif event.get("event") == "start-file":
                self.props["idle-active"] = False
        self.events.extend(out)
        return out

    def command(self, *args) -> None:
        self._write({"command": list(args)})

    # ------------------------------------------------------------ 播放控制
    def load(self, url: str, mode: str = "replace") -> None:
        self.command("loadfile", url, mode)
        self.props["idle-active"] = False
        self.props["time-pos"] = None
        self.props["duration"] = None

    def play_index(self, index: int) -> None:
        self.command("set_property", "playlist-pos", int(index))
        self.command("set_property", "pause", False)

    def toggle_pause(self) -> bool:
        paused = not bool(self.props.get("pause"))
        self.command("set_property", "pause", paused)
        self.props["pause"] = paused
        return paused

    def set_paused(self, paused: bool) -> None:
        self.command("set_property", "pause", bool(paused))
        self.props["pause"] = bool(paused)

    def seek(self, seconds: float, mode: str = "relative") -> None:
        self.command("seek", float(seconds), mode)

    def seek_absolute(self, seconds: float) -> None:
        self.command("seek", max(0.0, float(seconds)), "absolute")

    def set_volume(self, volume: float) -> int:
        v = int(max(0, min(100, round(volume))))
        self.command("set_property", "volume", v)
        self.props["volume"] = v
        self.props["mute"] = False
        return v

    def adjust_volume(self, delta: float) -> int:
        current = self.props.get("volume")
        if not isinstance(current, (int, float)):
            current = 80
        return self.set_volume(current + delta)

    def set_mute(self, muted: bool) -> None:
        self.command("set_property", "mute", bool(muted))
        self.props["mute"] = bool(muted)

    # ---------------------------------------------------------- 音量增益（过渡用）
    #: 用 ``volume-gain`` 而不是 ``volume``：前者是独立的增益层（dB，-96 近似静音），
    #: 调它不会污染用户设的音量，界面上的音量条也不会跟着抖。
    #: 换歌/跳转前后做一次淡出淡入，声音就不会"啪"地切断或突然接上。
    SILENT_GAIN = -60.0

    def set_gain(self, gain: float) -> None:
        self.command("set_property", "volume-gain", float(gain))
        self.props["volume-gain"] = float(gain)

    @property
    def gain(self) -> float:
        v = self.props.get("volume-gain")
        return float(v) if isinstance(v, (int, float)) else 0.0

    def has_playback(self) -> bool:
        """是否已经装载了媒体（而非停在 idle 等待）。"""
        return not self.idle and self.props.get("playlist-pos") is not None

    def media_title(self) -> str:
        return str(self.props.get("media-title") or "")

    # ------------------------------------------------------------ 状态查询
    @property
    def position(self) -> float:
        pos = self.props.get("time-pos")
        return float(pos) if isinstance(pos, (int, float)) else 0.0

    @property
    def duration(self) -> float:
        dur = self.props.get("duration")
        return float(dur) if isinstance(dur, (int, float)) else 0.0

    @property
    def paused(self) -> bool:
        return bool(self.props.get("pause"))

    @property
    def muted(self) -> bool:
        return bool(self.props.get("mute"))

    @property
    def volume(self) -> int:
        v = self.props.get("volume")
        return int(v) if isinstance(v, (int, float)) else 80

    @property
    def idle(self) -> bool:
        return bool(self.props.get("idle-active"))

    @property
    def playlist_pos(self) -> int:
        pos = self.props.get("playlist-pos")
        return int(pos) if isinstance(pos, (int, float)) else 0

    @property
    def ended(self) -> bool:
        """上一首播放结束（end-file 且不是我们主动换歌）。"""
        for event in self.events:
            if event.get("event") == "end-file" and event.get("reason") == "eof":
                return True
        return False

    def last_file_error(self) -> Optional[str]:
        for event in reversed(self.events):
            if event.get("event") == "end-file" and event.get("file_error"):
                return str(event["file_error"])
        return None

    def take_events(self) -> List[Dict[str, Any]]:
        events, self.events = self.events, []
        return events

    # ---------------------------------------------------------------- 生命周期
    def quit(self, timeout: float = 3.0) -> None:
        if self._proc.poll() is None:
            try:
                self._write({"command": ["quit"]})
            except Exception:
                pass
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=1.5)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        self._proc.kill()
                        self._proc.wait(timeout=1.5)
                    except (subprocess.TimeoutExpired, OSError):
                        pass
        self._close_handle()

    def _close_handle(self) -> None:
        """管道句柄必须显式关闭，否则 Windows 上会拖住 mpv 进程。"""
        if getattr(self, "_handle", None) is not None and os.name == "nt":
            try:
                _k32.CloseHandle(self._handle)
            except Exception:
                pass
        self._handle = None
        sock = getattr(self, "_sock", None)
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
            self._sock = None
            self._handle = None

    def __enter__(self) -> "Mpv":
        return self

    def __exit__(self, *exc) -> None:
        self.quit()
