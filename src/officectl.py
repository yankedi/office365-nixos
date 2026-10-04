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


class OfficeError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


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
        env.update(FONTCONFIG_FILE=self.resources["fontConfig"], FONTCONFIG_PATH="")
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

    def verify(self):
        if not self.installed():
            raise OfficeError("Office 安装验收失败：需要 WINWORD.EXE、1033 和 2052。")

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
                    try:
                        self.run_wine([self.resources["odt"], "/configure", windows_path(str(xml))],
                                      install_env, log, timeout=1800, cwd=logs)
                    except OfficeError as error:
                        (logs / "install.exit").write_text(f"{error.code}\n")
                        raise
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
                result = subprocess.run([self.resources["wine"],
                                         "C:\\Program Files\\Microsoft Office\\root\\Office16\\" + app["executable"],
                                         *arguments], env=env, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise OfficeError(f"{app['name']} 启动失败（{result.returncode}），日志：{logs}",
                                  result.returncode if result.returncode > 0 else 128 - result.returncode)

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
