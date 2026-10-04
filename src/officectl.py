#!/usr/bin/env python3
"""Explicit per-user Office installation and shared application launch support."""

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit


FONT_FAMILIES = {
    "simsun.ttc": "SimSun & NSimSun",
    "simsunb.ttf": "SimSun-ExtB",
    "simhei.ttf": "SimHei",
    "simkai.ttf": "KaiTi",
    "simfang.ttf": "FangSong",
    "Deng.ttf": "DengXian",
    "Dengb.ttf": "DengXian Bold",
    "Dengl.ttf": "DengXian Light",
    "msyh.ttc": "Microsoft YaHei & Microsoft YaHei UI",
    "msyhbd.ttc": "Microsoft YaHei Bold & Microsoft YaHei UI Bold",
    "msyhl.ttc": "Microsoft YaHei Light & Microsoft YaHei UI Light",
}
FONT_REPLACEMENTS = {
    "宋体": "SimSun", "黑体": "SimHei", "楷体": "KaiTi", "仿宋": "FangSong",
    "等线": "DengXian", "等线 Light": "DengXian Light", "微软雅黑": "Microsoft YaHei",
}
CONFIGURATION = """<?xml version="1.0" encoding="utf-8"?>
<Configuration>
  <Add OfficeClientEdition="64" Channel="Current">
    <Product ID="O365BusinessRetail">
      <Language ID="zh-cn" />
      <Language ID="en-us" />
    </Product>
  </Add>
  <Display Level="Full" />
</Configuration>
"""
C2R_TASK_TYPE = re.compile(r'"TaskType"\s*:\s*"([A-Za-z_]+):\{([0-9A-Fa-f-]{36})\}"')
C2R_TASK_SCENARIO = re.compile(r'"Scenario"\s*:\s*"([^"]+)"')
C2R_TASK_PROGRESS = re.compile(
    r"ScenarioController::UpdateScenarioProgress\s*-\s*\{([0-9A-Fa-f-]{36})\}=(\d+)",
    re.IGNORECASE,
)
C2R_TASK_LABELS = {
    "PROMPTUSER": "等待安装器准备",
    "CREATEWORKINGCONFIGURATION": "生成 Office 安装计划",
    "STREAM": "下载并展开 O365BusinessRetail（zh-cn + en-us）数据",
    "STAGEREGISTRY": "暂存 Office 注册表配置",
    "APPLYCONFIGURATION": "应用 Office 程序和 zh-cn/en-us 语言组件",
    "INITUPDATES": "初始化 Click-to-Run 更新组件",
    "INTEGRATE_INSTALL": "集成并注册 Office 应用",
    "UNINSTALLCENTENNIAL": "检查旧版 Store Office",
    "MIGRATE": "迁移 Office 配置",
    "FONTS": "部署 Office 自带的字体组件",
    "LASTRUN": "完成安装收尾",
    "ONLINEINTERACTION": "完成 Click-to-Run 在线交互初始化",
}


class OfficeError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class InstallProgress:
    """Read Click-to-Run task names and per-task progress from native ULS logs."""

    def __init__(self, office):
        self.office = office
        self.offsets = {}
        self.pending = {}
        self.task_types = {}
        self.task_scenarios = {}
        self.started = set()
        self.finished = set()
        self.last_task_percent = {}
        self.downloads = set()
        self.transfers = set()
        # Existing logs belong to prior attempts; only report data appended
        # after this installation starts, plus newly-created log files.
        for path in office.c2r_log_paths():
            try:
                self.offsets[path] = path.stat().st_size
            except OSError:
                continue

    @staticmethod
    def _decode(data):
        if data.startswith(b"\xff\xfe"):
            payload = data[2:]
            usable = len(payload) & ~1
            return payload[:usable].decode("utf-16le", errors="replace"), 2 + usable
        if data.startswith(b"\xfe\xff"):
            payload = data[2:]
            usable = len(payload) & ~1
            return payload[:usable].decode("utf-16be", errors="replace"), 2 + usable
        if b"\0" in data[:128]:
            usable = len(data) & ~1
            return data[:usable].decode("utf-16le", errors="replace"), usable
        return data.decode("utf-8", errors="replace"), len(data)

    def poll(self):
        events = []
        log_lines = []
        for path in self.office.c2r_log_paths():
            try:
                size = path.stat().st_size
                offset = self.offsets.get(path, 0)
                if size < offset:
                    offset = 0
                    self.pending.pop(path, None)
                with path.open("rb") as stream:
                    stream.seek(offset)
                    data = stream.read()
            except OSError:
                continue
            if not data:
                continue

            text, consumed = self._decode(data)
            self.offsets[path] = offset + consumed
            lines = (self.pending.pop(path, "") + text).splitlines(keepends=True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                self.pending[path] = lines.pop()
            log_lines.extend(lines)

        def timestamp(line):
            try:
                return datetime.strptime(line[:23], "%m/%d/%Y %H:%M:%S.%f").timestamp()
            except ValueError:
                return 0

        # Click-to-Run writes concurrently to several ULS files; process their
        # newly appended records in timestamp order so task IDs are known before
        # their per-task progress records arrive.
        for line in sorted(log_lines, key=timestamp):
            if "Task::Execute " in line:
                match = C2R_TASK_TYPE.search(line)
                if match:
                    task, task_id = match.group(1).upper(), match.group(2).upper()
                    self.task_types[task_id] = task
                    scenario = C2R_TASK_SCENARIO.search(line)
                    self.task_scenarios[task_id] = scenario.group(1).upper() if scenario else "INSTALL"
                    if self.task_scenarios[task_id] != "INSTALL":
                        continue
                    if task in C2R_TASK_LABELS and task_id not in self.started:
                        self.started.add(task_id)
                        events.append(("start", task, None))

            progress = C2R_TASK_PROGRESS.search(line)
            if progress:
                task_id, percent = progress.group(1).upper(), int(progress.group(2))
                task = self.task_types.get(task_id)
                if (task in C2R_TASK_LABELS
                        and self.task_scenarios.get(task_id, "INSTALL") == "INSTALL"
                        and task_id not in self.finished
                        and 0 <= percent < 100):
                    previous = self.last_task_percent.get(task_id)
                    if previous is None or percent > previous:
                        self.last_task_percent[task_id] = percent
                        events.append(("progress", task, percent))

            # These records carry real filenames and completed-transfer byte
            # counts, not a live network percentage. Restrict them to an active
            # installation STREAM task and avoid repeated failover telemetry.
            if any(self.task_types[task_id] == "STREAM" for task_id in self.started - self.finished):
                events.extend(self.download_events(line))

            event_type = None
            if "Task::DoHandleWorkerSuccessEvent " in line:
                event_type = "complete"
            elif "Task::DoHandleWorkerExceptionEvent " in line:
                event_type = "error"
            if event_type:
                match = C2R_TASK_TYPE.search(line)
                if match:
                    task, task_id = match.group(1).upper(), match.group(2).upper()
                    self.task_types[task_id] = task
                    scenario = C2R_TASK_SCENARIO.search(line)
                    if scenario:
                        self.task_scenarios[task_id] = scenario.group(1).upper()
                    if self.task_scenarios.get(task_id, "INSTALL") != "INSTALL":
                        continue
                    if task not in C2R_TASK_LABELS or task_id in self.finished:
                        continue
                    self.finished.add(task_id)
                    if task_id not in self.started:
                        events.append(("start", task, None))
                    events.append((event_type, task, None))
        return events

    def download_events(self, line):
        start = "C2R::Transport::BGTransportJob::StartDownload" in line
        complete = "ActivityEnded " in line and '"Office.ClickToRun.Transport2"' in line
        if not (start or complete):
            return []
        try:
            data = json.loads(line[line.index("{"):].strip())
            if not isinstance(data, dict):
                return []
            if start:
                context = json.loads(data.get("ContextData", "{}"))
                if not isinstance(context, dict):
                    return []
                filename = context.get("FileName", "")
            else:
                if data.get("Success") is not True:
                    return []
                source = data.get("Data.SourcePathNoFilePath", "")
                filename = urlsplit(source).path.rsplit("/", 1)[-1] if isinstance(source, str) else ""
            if not isinstance(filename, str) or not re.fullmatch(r"[\w.-]{1,128}", filename):
                return []
            if start:
                if filename in self.downloads:
                    return []
                self.downloads.add(filename)
                return [("download", filename, None)]
            transfer_id = data.get("CV")
            size = int(data.get("Data.TransferredBytes", 0))
            if not isinstance(transfer_id, str) or not transfer_id or transfer_id in self.transfers or size <= 0:
                return []
            self.transfers.add(transfer_id)
            return [("transfer", filename, size)]
        except (ValueError, TypeError):
            return []

    def status(self):
        return [("status", task, self.last_task_percent.get(task_id))
                for task_id, task in self.task_types.items()
                if task_id in self.started and task_id not in self.finished] or [("status", "", None)]

    @staticmethod
    def report(events):
        for event, task, percent in events:
            if event == "download":
                print(f"  获取安装数据：{task}…", flush=True)
                continue
            if event == "transfer":
                print(f"  数据传输完成：{task}（本次传输 {percent / (1024 * 1024):.1f} MiB）。", flush=True)
                continue
            if event == "status":
                if task:
                    latest = f"（最近报告 {percent}%）" if percent is not None else ""
                    print(f"  等待安装器报告新进度：{C2R_TASK_LABELS[task]}{latest}…", flush=True)
                else:
                    print("  安装器仍在运行，等待下一条安装任务记录…", flush=True)
                continue
            label = C2R_TASK_LABELS[task]
            if event == "start":
                print(f"Office 安装阶段：{label}…", flush=True)
            elif event == "progress":
                print(f"  当前阶段进度：{label} {percent}%", flush=True)
            elif event == "complete":
                print(f"Office 阶段结束：{label}。", flush=True)
            elif event == "error":
                print(f"Office 阶段异常：{label}；详见 NIXOS 安装日志。", flush=True)

    def run(self, stop, interval=1.0):
        last_output = time.monotonic()
        while not stop.is_set():
            events = self.poll()
            now = time.monotonic()
            if events:
                self.report(events)
                last_output = now
            elif now - last_output >= 20:
                self.report(self.status())
                last_output = now
            stop.wait(interval)
        self.report(self.poll())


def windows_path(value):
    """Convert a native path or local file URI without shell interpolation."""
    if value.startswith("file:"):
        uri = urlsplit(value)
        if uri.netloc not in ("", "localhost"):
            raise OfficeError("仅支持本地 file:// 路径。")
        value = unquote(uri.path)
    path = Path(value).expanduser().resolve()
    return "Z:" + str(path).replace("/", "\\")


class Office:
    def __init__(self, resources, environ=None):
        self.resources = resources
        self.environ = dict(os.environ if environ is None else environ)
        home = Path(self.environ.get("HOME", str(Path.home())))
        data = Path(self.environ.get("XDG_DATA_HOME") or home / ".local/share")
        state = Path(self.environ.get("XDG_STATE_HOME") or home / ".local/state")
        cache = Path(self.environ.get("XDG_CACHE_HOME") or home / ".cache")
        if not all(path.is_absolute() for path in (data, state, cache)):
            raise OfficeError("HOME 和 XDG 目录必须为绝对路径。")
        self.prefix = data / "wineprefixes/office365"
        self.office = self.prefix / "drive_c/Program Files/Microsoft Office/root/Office16"
        self.marker = self.prefix / ".office365-install-complete"
        self.logs = state / "office365-nixos/logs"
        runtime = Path(self.environ.get("XDG_RUNTIME_DIR") or cache)
        digest = hashlib.sha256(str(self.prefix.resolve()).encode()).hexdigest()[:16]
        self.lock_dir = runtime / "office365-nixos" / digest

    def environment(self, application=False):
        env = self.environ.copy()
        for key in ("LD_LIBRARY_PATH", "WINEDLLOVERRIDES", "WINE_D3D_CONFIG", "WINELOADER",
                    "WINESERVER", "WINEDLLPATH", "WINEBOOTSTRAPMODE", "LOCPATH", "LOCALE_ARCHIVE",
                    "WAYLAND_DISPLAY"):
            env.pop(key, None)
        # Nix glibc prefers its ABI-specific variable over LOCALE_ARCHIVE.
        # A host archive from another generation can omit Chinese or be ABI
        # incompatible, so pin both variables to this package's matching data.
        for key in list(env):
            if key.startswith("LOCALE_ARCHIVE_"):
                env.pop(key)
        env.update(LOCALE_ARCHIVE=self.resources["localeArchive"],
                   LOCALE_ARCHIVE_2_27=self.resources["localeArchive"])
        env.update(WINEPREFIX=str(self.prefix), WINEARCH="win64",
                   WINEDEBUG=self.environ.get("WINEDEBUG", "-all"),
                   LANG="zh_CN.UTF-8", LC_ALL="zh_CN.UTF-8",
                   WINEDLLOVERRIDES="winemenubuilder.exe=d")
        env["PATH"] = self.resources["executablePath"] + ":" + env.get("PATH", "")
        if application:
            env.update(WINEDLLOVERRIDES="riched20=n;mshtml=b;winemenubuilder.exe=d;winedbg.exe=d", WINE_D3D_CONFIG="renderer=gl",
                       LANG="zh_CN.UTF-8", LC_ALL="zh_CN.UTF-8",
                       LOCALE_ARCHIVE=self.resources["localeArchive"])
            env["LD_LIBRARY_PATH"] = self.resources["libraryPath"] + ":/run/opengl-driver/lib"
        return env

    @contextmanager
    def lock(self, name="prefix", shared=False, quiet=False):
        self.lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.lock_dir / (name + ".lock")).open("a") as stream:
            try:
                fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                if quiet:
                    yield False
                    return
                raise OfficeError("该 prefix 正在使用中，请关闭 Office 或等待当前操作完成。")
            yield True

    def log_directory(self, operation):
        self.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = Path(tempfile.mkdtemp(prefix=f"{operation}-{datetime.now():%Y%m%d-%H%M%S}-", dir=self.logs))
        print(f"日志：{path}", flush=True)
        return path

    def installed(self):
        return ((self.office / "WINWORD.EXE").is_file()
                and all((self.office / language).is_dir() for language in ("1033", "2052")))

    def c2r_log_paths(self):
        prefix = self.prefix / "drive_c"
        roots = [prefix / "windows"]
        users = prefix / "users"
        try:
            roots.extend(user / "AppData/Local/Temp" for user in users.iterdir() if user.is_dir())
        except OSError:
            pass
        paths = set()
        for root in roots:
            try:
                paths.update(root.glob("NIXOS-*.log"))
            except OSError:
                continue
        def modified(path):
            try:
                return path.stat().st_mtime
            except OSError:
                return 0

        return sorted(paths, key=lambda path: (modified(path), str(path)))

    def verify(self):
        if not self.installed():
            raise OfficeError("Office 安装验收失败：需要 WINWORD.EXE、1033 和 2052。")

    def uninstall(self):
        with self.lock():
            if self.prefix.is_symlink():
                raise OfficeError(f"Prefix 不能是符号链接：{self.prefix}")
            if not self.prefix.exists():
                print(f"Office prefix 不存在，无需卸载：{self.prefix}")
                return
            if not self.prefix.is_dir():
                raise OfficeError(f"Office prefix 不是目录，拒绝删除：{self.prefix}")
            print(f"删除专用 Office prefix：{self.prefix}", flush=True)
            self.stop_server(self.environment())
            shutil.rmtree(self.prefix)
            print("Office 已卸载；账户、设置和 prefix 内字体也已删除。", flush=True)

    def write_marker(self, adopted=False):
        verification = "existing installation verified" if adopted else "ODT exit=0"
        self.marker.write_text(
            f"{verification}; WINWORD.EXE; languages=1033,2052; {datetime.now().astimezone().isoformat()}\n"
        )

    def run_wine(self, args, env, log, timeout=120, cwd=None):
        result = subprocess.run([self.resources["wine"], *map(str, args)], env=env,
                                stdout=log, stderr=subprocess.STDOUT, timeout=timeout, cwd=cwd)
        if result.returncode:
            raise OfficeError(f"Wine 命令失败（{result.returncode}）：{args[0]}",
                              result.returncode if result.returncode > 0 else 128 - result.returncode)

    def stop_server(self, env):
        subprocess.run([self.resources["wineserver"], "-k"], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        subprocess.run([self.resources["wineserver"], "-w"], env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)

    def application_process_running(self, executable):
        expected = executable.casefold()
        prefix = os.fsencode(f"WINEPREFIX={self.prefix}")
        try:
            processes = Path("/proc").iterdir()
            for process in processes:
                if not process.name.isdigit():
                    continue
                try:
                    if (process / "comm").read_text().strip().casefold() != expected:
                        continue
                    if prefix in (process / "environ").read_bytes().split(b"\0"):
                        return True
                except OSError:
                    continue
        except OSError:
            return False
        return False

    def run_application(self, command, env, log, executable):
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        seen = False
        while process.poll() is None:
            if not seen:
                seen = self.application_process_running(executable)
            time.sleep(0.2)
        returncode = process.wait()
        if not seen:
            seen = self.application_process_running(executable)
        return returncode, seen

    def init(self, reset=False):
        with self.lock():
            if self.prefix.is_symlink():
                raise OfficeError(f"Prefix 不能是符号链接：{self.prefix}")
            env = self.environment()
            if reset and not env.get("DISPLAY"):
                raise OfficeError("请在有 X11/XWayland DISPLAY 的图形会话中执行 officectl init --reset。")
            if reset and self.prefix.exists():
                print(f"重建 prefix：{self.prefix}", flush=True)
                self.stop_server(env)
                shutil.rmtree(self.prefix)
            if self.installed():
                self.write_marker(adopted=True)
                print(f"已核验现有 Office 安装：{self.prefix}")
                return
            if self.prefix.exists() and any(self.prefix.iterdir()):
                raise OfficeError("Prefix 非空且未通过验收。保留现有文件；使用 officectl init --reset 重建。")
            if not env.get("DISPLAY"):
                raise OfficeError("请在有 X11/XWayland DISPLAY 的图形会话中执行 officectl init。")
            logs = self.log_directory("init")
            xml = logs / "configuration.xml"
            xml.write_text(CONFIGURATION, encoding="utf-8")
            self.prefix.parent.mkdir(parents=True, exist_ok=True)
            broker = None
            watcher = None
            stop = threading.Event()
            with (logs / "install.log").open("w") as log, (logs / "voip-broker.log").open("w") as broker_log, \
                    (logs / "service-watchdog.log").open("w") as service_log:
                try:
                    print("初始化 Wine prefix…", flush=True)
                    self.run_wine(["wineboot", "-u"], env, log)
                    # services.exe caches an incomplete environment during the
                    # first wineboot. Restart BEFORE helpers or service queries.
                    self.stop_server(env)
                    broker = subprocess.Popen([self.resources["wine"], self.resources["broker"]],
                                              env=env, stdin=subprocess.PIPE,
                                              stdout=broker_log, stderr=subprocess.STDOUT)
                    deadline = time.monotonic() + 20
                    while "READY " not in (logs / "voip-broker.log").read_text(errors="replace"):
                        if broker.poll() is not None:
                            raise OfficeError(f"VoIP broker 提前退出，请检查 {logs}/voip-broker.log")
                        if time.monotonic() > deadline:
                            raise OfficeError("VoIP broker 未就绪。")
                        stop.wait(0.2)

                    def watch_service():
                        while not stop.is_set():
                            try:
                                subprocess.run([self.resources["wine"], "sc.exe", "start", "ClickToRunSvc"],
                                               env=env, stdout=service_log, stderr=subprocess.STDOUT, timeout=20)
                            except subprocess.TimeoutExpired:
                                pass
                            stop.wait(5)

                    watcher = threading.Thread(target=watch_service, daemon=True)
                    watcher.start()
                    install_env = env.copy()
                    install_env.pop("WAYLAND_DISPLAY", None)
                    install_env.update(WINEDLLOVERRIDES="riched20=n;mshtml=b;winemenubuilder.exe=d", WINE_D3D_CONFIG="renderer=gl")
                    print("安装 Microsoft 365（64 位，zh-cn + en-us）…", flush=True)
                    progress = InstallProgress(self)
                    progress_stop = threading.Event()
                    progress_thread = threading.Thread(
                        target=progress.run, args=(progress_stop,), daemon=True
                    )
                    print("跟踪 Click-to-Run 安装任务…", flush=True)
                    progress_thread.start()
                    try:
                        self.run_wine([self.resources["odt"], "/configure", windows_path(str(xml))],
                                      install_env, log, timeout=1800, cwd=logs)
                    except OfficeError as error:
                        (logs / "install.exit").write_text(f"{error.code}\n")
                        raise
                    finally:
                        progress_stop.set()
                        progress_thread.join(timeout=5)
                    (logs / "install.exit").write_text("0\n")
                    self.verify()
                    self.write_marker()
                    print(f"安装完成：{self.prefix}\n已验收：ODT exit=0、WINWORD.EXE、1033 + 2052", flush=True)
                finally:
                    stop.set()
                    # Closing stdin releases the broker; no global process kill.
                    if broker is not None and broker.stdin is not None:
                        broker.stdin.close()
                    if watcher is not None:
                        watcher.join(timeout=25)
                    self.stop_server(env)
                    if broker is not None:
                        broker.wait(timeout=10)

    def install_fonts(self):
        with self.lock():
            self.verify()
            logs = self.log_directory("chinese-fonts")
            print("获取中文字体资源（仅本命令触发字体提取构建）…", flush=True)
            flake = self.resources["fontFlake"]
            with (logs / "fonts.log").open("w") as log:
                build = subprocess.run(
                    [self.resources["nix"], "--extra-experimental-features", "nix-command flakes", "build",
                     "--no-link", "--print-out-paths", flake + "#ttf-ms-win11-auto-zh_cn",
                     flake + "#ttf-ms-win11-fod-auto-hans"],
                    env=self.environ, stdout=subprocess.PIPE, stderr=log, text=True,
                )
                if build.returncode:
                    raise OfficeError(f"字体资源构建失败，请检查 {logs}/fonts.log", build.returncode)
                roots = [Path(line) for line in build.stdout.splitlines() if line.startswith("/nix/store/")]
                if len(roots) != 2:
                    raise OfficeError("字体构建没有返回两个字体包。")
                available = {p.name.lower(): p for root in roots for p in root.rglob("*")
                             if p.is_file() and p.suffix.lower() in (".ttf", ".ttc", ".otf")}
                missing = [name for name in FONT_FAMILIES if name.lower() not in available]
                if missing:
                    raise OfficeError("字体包缺少文件：" + ", ".join(missing))
                fonts = self.prefix / "drive_c/windows/Fonts"
                fonts.mkdir(parents=True, exist_ok=True)
                env = self.environment(application=True)
                for name, family in FONT_FAMILIES.items():
                    shutil.copyfile(available[name.lower()], fonts / name)
                    self.run_wine(["reg", "add", r"HKLM\Software\Microsoft\Windows NT\CurrentVersion\Fonts",
                                   "/v", family + " (TrueType)", "/t", "REG_SZ", "/d", name, "/f"], env, log)
                for name, family in FONT_REPLACEMENTS.items():
                    self.run_wine(["reg", "add", r"HKCU\Software\Wine\Fonts\Replacements",
                                   "/v", name, "/t", "REG_SZ", "/d", family, "/f"], env, log)
                (self.prefix / ".officectl-chinese-fonts.json").write_text(json.dumps(
                    {"source": flake, "files": list(FONT_FAMILIES), "mappings": FONT_REPLACEMENTS},
                    ensure_ascii=False, indent=2,
                ), encoding="utf-8")
            print("中文字体安装完成：11 个字体文件、7 条中文名映射。")

    def launch(self, application, files):
        app = self.resources["applications"][application]
        if not (self.office / app["executable"]).is_file():
            raise OfficeError(f"{app['name']} 尚未安装，请先执行 officectl init。")
        if files and files[0] == "--":
            files = files[1:]
        arguments = [windows_path(file) for file in files]
        with self.lock(shared=True):
            env = self.environment(application=True)
            logs = self.log_directory(application)
            with (logs / "application.log").open("w") as log:
                # This runner's X11 EGL backend can fail to create Office's
                # Direct2D contexts. GLX works with the system GLVND drivers.
                self.run_wine(["reg", "add", r"HKCU\Software\Wine\X11 Driver",
                               "/v", "UseEGL", "/t", "REG_SZ", "/d", "N", "/f"], env, log)
                # A prefix-scoped supervisor prevents duplicate watchers when
                # several launchers open apps/documents at the same time.
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_shadows"], env=env,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
                returncode, process_seen = self.run_application(
                    [self.resources["wine"],
                     "C:\\Program Files\\Microsoft Office\\root\\Office16\\" + app["executable"],
                     *arguments],
                    env, log, app["executable"],
                )
            # Wine4Office's Word returns Windows exit code 3 on normal close.
            # Suppress it only when the matching app process actually ran.
            if returncode and not (returncode == 3 and process_seen):
                raise OfficeError(f"{app['name']} 启动失败（{returncode}），日志：{logs}",
                                  returncode if returncode > 0 else 128 - returncode)

    def watch_shadows(self):
        if not self.installed():
            return
        with self.lock("shadows", quiet=True) as acquired:
            if acquired:
                subprocess.run([self.resources["wine"], self.resources["shadows"], "--watch"],
                               env=self.environment(application=True), check=False)

    def notify(self, message):
        try:
            subprocess.run([self.resources["notify"], "--app-name=Microsoft Office", "Microsoft Office", message],
                           env=self.environ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="officectl", description="Microsoft 365 安装管理")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="安装默认的 64 位中文版 Office")
    init.add_argument("--reset", action="store_true", help="删除默认 prefix 并从零重新安装")
    commands.add_parser("uninstall", help="卸载 Office 并删除专用 prefix")
    install = commands.add_parser("install", help="安装可选资源")
    install.add_argument("component", choices=["chinese-fonts"])
    # Launchers use private dispatch; the public CLI is init/install only.
    internal = argv and argv[0] in ("_launch", "_shadows")
    if not internal:
        args = parser.parse_args(argv)
    office = None
    try:
        resources = json.loads(Path(__file__).with_name("resources.json").read_text())
        office = Office(resources)
        if internal:
            if argv[0] == "_shadows":
                office.watch_shadows()
            elif len(argv) < 2 or argv[1] not in resources["applications"]:
                raise OfficeError("未知的 Office 应用。", 2)
            else:
                office.launch(argv[1], argv[2:])
        else:
            if os.geteuid() == 0:
                raise OfficeError("请以普通用户执行 officectl，不要使用 sudo。")
            if args.command == "init":
                office.init(args.reset)
            elif args.command == "uninstall":
                office.uninstall()
            else:
                office.install_fonts()
        return 0
    except OfficeError as error:
        print(f"officectl: {error}", file=sys.stderr)
        if office is not None and argv and argv[0] == "_launch":
            office.notify(str(error))
        return error.code
    except (OSError, subprocess.SubprocessError) as error:
        print(f"officectl: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("officectl: 操作已中断。", file=sys.stderr)
        return 130


def terminated(_signum, _frame):
    raise OfficeError("操作已终止。", 143)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminated)
    sys.exit(main())
