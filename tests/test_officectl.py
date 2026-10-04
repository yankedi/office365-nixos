import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("officectl", Path(__file__).parents[1] / "src/officectl.py")
officectl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(officectl)


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = {
            "HOME": str(self.root), "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"), "XDG_CACHE_HOME": str(self.root / "cache"),
            "PATH": "/usr/bin", "DISPLAY": ":0",
            "WINEPREFIX": "/wrong/prefix", "LD_LIBRARY_PATH": "/wrong/libs",
            "WINEDLLOVERRIDES": "wrong=n", "WINEBOOTSTRAPMODE": "1", "LOCPATH": "/wrong/locales",
            "LOCALE_ARCHIVE_2_27": "/wrong/host-archive",
            "WAYLAND_DISPLAY": "wayland-1",
            "FONTCONFIG_FILE": "/etc/fonts/fonts.conf",
            "FONTCONFIG_PATH": "/etc/fonts",
        }
        self.resources = {
            "wine": "/wine/bin/wine", "wineserver": "/wine/bin/wineserver", "broker": "/broker.exe",
            "odt": "/odt/setup.exe", "shadows": "/office-shadows.exe", "localeArchive": "/locales/locale-archive",
            "libraryPath": "/runtime/libs", "executablePath": "/runtime/bin", "notify": "/notify-send",
            "nix": "/nix", "fontFlake": "github:example/fonts/pinned", "runnerVersion": "test",
            "applications": {"word": {"name": "Word", "executable": "WINWORD.EXE"}},
        }
        self.office = officectl.Office(self.resources, self.env)

    def installed_prefix(self):
        for language in ("1033", "2052"):
            (self.office.office / language).mkdir(parents=True)
        (self.office.office / "WINWORD.EXE").write_text("test executable")

    def test_default_prefix_does_not_use_ambient_wineprefix(self):
        self.assertEqual(self.office.prefix, self.root / "data/wineprefixes/office365")

    def test_install_environment_cannot_inherit_other_runners_or_overrides(self):
        env = self.office.environment()
        self.assertEqual(env["WINEPREFIX"], str(self.office.prefix))
        for key in ("LD_LIBRARY_PATH", "WINEBOOTSTRAPMODE", "LOCPATH", "WAYLAND_DISPLAY"):
            self.assertNotIn(key, env)
        self.assertEqual(env["WINEDLLOVERRIDES"], "winemenubuilder.exe=d")
        self.assertEqual(env["FONTCONFIG_FILE"], "/etc/fonts/fonts.conf")
        self.assertEqual(env["FONTCONFIG_PATH"], "/etc/fonts")

    def test_application_environment_is_pinned_and_chinese(self):
        env = self.office.environment(application=True)
        self.assertEqual(env["LC_ALL"], "zh_CN.UTF-8")
        self.assertEqual(env["LOCALE_ARCHIVE"], "/locales/locale-archive")
        self.assertEqual(env["LOCALE_ARCHIVE_2_27"], "/locales/locale-archive")
        self.assertEqual(env["LD_LIBRARY_PATH"], "/runtime/libs:/run/opengl-driver/lib")
        self.assertIn("winedbg.exe=d", env["WINEDLLOVERRIDES"])

    def test_missing_app_does_not_initialize_or_create_prefix(self):
        with patch.object(officectl.subprocess, "run") as run, patch.object(officectl.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(officectl.OfficeError, "officectl init"):
                self.office.launch("word", [])
            run.assert_not_called()
            popen.assert_not_called()
            self.assertFalse(self.office.prefix.exists())

    def test_shadow_supervisor_does_not_initialize_a_missing_prefix(self):
        with patch.object(officectl.subprocess, "run") as run:
            self.office.watch_shadows()
            run.assert_not_called()
        self.assertFalse(self.office.prefix.exists())

    def test_existing_successful_prefix_is_adopted_without_running_wine(self):
        self.installed_prefix()
        with patch.object(officectl.subprocess, "run") as run:
            self.office.init()
            self.office.init()
            run.assert_not_called()
        self.assertTrue(self.office.marker.is_file())

    def test_partial_prefix_is_preserved_even_with_stale_success_marker(self):
        self.office.prefix.mkdir(parents=True)
        data = self.office.prefix / "system.reg"
        data.write_text("registry to preserve")
        self.office.marker.write_text("old success")
        with patch.object(officectl.subprocess, "run") as run:
            with self.assertRaisesRegex(officectl.OfficeError, "--reset"):
                self.office.init()
            run.assert_not_called()
        self.assertEqual(data.read_text(), "registry to preserve")

    def test_reset_refuses_a_symlink_prefix(self):
        destination = self.root / "other"
        destination.mkdir()
        (destination / "keep").write_text("keep")
        self.office.prefix.parent.mkdir(parents=True)
        self.office.prefix.symlink_to(destination)
        with self.assertRaisesRegex(officectl.OfficeError, "符号链接"):
            self.office.init(reset=True)
        self.assertEqual((destination / "keep").read_text(), "keep")

    def test_reset_without_graphical_session_preserves_existing_installation(self):
        self.installed_prefix()
        self.office.environ.pop("DISPLAY")
        with patch.object(self.office, "stop_server") as stop:
            with self.assertRaisesRegex(officectl.OfficeError, "DISPLAY"):
                self.office.init(reset=True)
            stop.assert_not_called()
        self.assertTrue(self.office.installed())

    def test_uninstall_removes_only_default_prefix_and_stops_its_server(self):
        self.installed_prefix()
        fonts = self.office.prefix / "drive_c/windows/Fonts"
        fonts.mkdir(parents=True)
        (fonts / "simsun.ttc").write_text("font")
        other = self.root / "data/wineprefixes/other"
        other.mkdir()
        (other / "keep").write_text("keep")
        with patch.object(self.office, "stop_server") as stop:
            self.office.uninstall()
            stop.assert_called_once()
        self.assertFalse(self.office.prefix.exists())
        self.assertEqual((other / "keep").read_text(), "keep")

    def test_uninstall_refuses_prefix_symlink(self):
        destination = self.root / "other"
        destination.mkdir()
        (destination / "keep").write_text("keep")
        self.office.prefix.parent.mkdir(parents=True)
        self.office.prefix.symlink_to(destination)
        with self.assertRaisesRegex(officectl.OfficeError, "符号链接"):
            self.office.uninstall()
        self.assertEqual((destination / "keep").read_text(), "keep")

    def test_prefix_modification_is_locked_while_an_application_is_running(self):
        with self.office.lock(shared=True):
            with self.assertRaisesRegex(officectl.OfficeError, "正在使用"):
                with self.office.lock():
                    self.fail("exclusive lock unexpectedly acquired")

    def test_multiple_unicode_paths_remain_individual_arguments(self):
        self.installed_prefix()
        files = [str(self.root / "中文 空格/文档 '1'.docx"), str(self.root / "第二个.docx")]
        app_process = Mock()
        app_process.poll.return_value = 0
        app_process.wait.return_value = 0
        with patch.object(officectl.subprocess, "Popen", side_effect=[Mock(), app_process]) as popen, \
                patch.object(officectl.subprocess, "run") as run, \
                patch.object(self.office, "application_process_running", return_value=False):
            run.return_value.returncode = 0
            self.office.launch("word", files)
            command = popen.call_args_list[1].args[0]
            self.assertEqual(command[0], "/wine/bin/wine")
            self.assertTrue(command[1].endswith("WINWORD.EXE"))
            self.assertEqual(command[2:], [officectl.windows_path(file) for file in files])
            self.assertNotIn("shell", run.call_args.kwargs)
            configuration = run.call_args_list[0].args[0]
            self.assertEqual(configuration[1:4], ["reg", "add", r"HKCU\Software\Wine\X11 Driver"])
            self.assertIn("UseEGL", configuration)

    def test_word_exit_code_three_is_normal_after_process_was_seen(self):
        self.installed_prefix()
        app_process = Mock()
        app_process.poll.return_value = 3
        app_process.wait.return_value = 3
        with patch.object(officectl.subprocess, "Popen", side_effect=[Mock(), app_process]), \
                patch.object(officectl.subprocess, "run") as run, \
                patch.object(self.office, "application_process_running", return_value=True):
            run.return_value.returncode = 0
            self.office.launch("word", [])

    def test_word_exit_code_three_is_failure_if_process_never_appeared(self):
        self.installed_prefix()
        app_process = Mock()
        app_process.poll.return_value = 3
        app_process.wait.return_value = 3
        with patch.object(officectl.subprocess, "Popen", side_effect=[Mock(), app_process]), \
                patch.object(officectl.subprocess, "run") as run, \
                patch.object(self.office, "application_process_running", return_value=False):
            run.return_value.returncode = 0
            with self.assertRaisesRegex(officectl.OfficeError, "启动失败（3）"):
                self.office.launch("word", [])

    def test_file_uri_is_decoded_once(self):
        path = self.root / "中文 空格/百分号%20.docx"
        self.assertEqual(officectl.windows_path(path.as_uri()), "Z:" + str(path).replace("/", "\\"))

    def test_remote_file_uri_is_not_treated_as_a_local_document(self):
        with self.assertRaises(officectl.OfficeError):
            officectl.windows_path("file://remote-host/shared/document.docx")

    def test_fonts_require_installed_office_before_any_download(self):
        with patch.object(officectl.subprocess, "run") as run:
            with self.assertRaises(officectl.OfficeError):
                self.office.install_fonts()
            run.assert_not_called()

    def test_c2r_progress_reads_appended_utf16_log_and_skips_old_attempts(self):
        log = (self.office.prefix / "drive_c/users/test/AppData/Local/Temp/NIXOS-20261004-1700.log")
        log.parent.mkdir(parents=True)
        log.write_bytes(
            "ScenarioController::UpdateScenarioProgress - total progress is now 12.\n".encode("utf-16le")
        )
        progress = officectl.InstallProgress(self.office)
        task_id = "4CCD26FB-A773-42FB-8E44-CD53798BC0E7"
        with log.open("ab") as stream:
            stream.write(
                f'Task::Execute {{"TaskType":"STREAM:{{{task_id}}}","Scenario":"INSTALL"}}\n'
                f"ScenarioController::UpdateScenarioProgress - {{{task_id}}}=1\n"
                f"ScenarioController::UpdateScenarioProgress - {{{task_id}}}=7\n"
                f"ScenarioController::UpdateScenarioProgress - {{{task_id}}}=15\n"
                f"ScenarioController::UpdateScenarioProgress - {{{task_id}}}=35\n"
                f"ScenarioController::UpdateScenarioProgress - {{{task_id}}}=67\n"
                f"ScenarioController::UpdateScenarioProgress - {{{task_id}}}=100\n"
                f'Task::DoHandleWorkerSuccessEvent {{"TaskType":"STREAM:{{{task_id}}}"}}\n'
                .encode("utf-16le")
            )
        self.assertEqual(progress.poll(), [
            ("start", "STREAM", None),
            ("progress", "STREAM", 1),
            ("progress", "STREAM", 7),
            ("progress", "STREAM", 15),
            ("progress", "STREAM", 35),
            ("progress", "STREAM", 67),
            ("complete", "STREAM", None),
        ])
        self.assertEqual(progress.poll(), [])
        interaction_id = "8F40ABF1-88C8-49F2-9B8C-FAF494FB3F43"
        with log.open("ab") as stream:
            stream.write(
                f'Task::Execute {{"TaskType":"ONLINEINTERACTION:{{{interaction_id}}}",'
                '"Scenario":"RICHINTERACTION"}\n'
                f'Task::DoHandleWorkerSuccessEvent {{"TaskType":"ONLINEINTERACTION:{{{interaction_id}}}",'
                '"Scenario":"RICHINTERACTION"}\n'.encode("utf-16le")
            )
        self.assertEqual(progress.poll(), [])
        with log.open("ab") as stream:
            stream.write(f"ScenarioController::UpdateScenarioProgress - {{{task_id}}}=72.".encode("utf-16le"))
        self.assertEqual(progress.poll(), [])
        with log.open("ab") as stream:
            stream.write("\n".encode("utf-16le"))
        self.assertEqual(progress.poll(), [])

    def test_c2r_small_increases_and_partial_records_are_not_hidden(self):
        log = self.office.prefix / "drive_c/windows/NIXOS-progress.log"
        log.parent.mkdir(parents=True)
        progress = officectl.InstallProgress(self.office)
        task_id = "4CCD26FB-A773-42FB-8E44-CD53798BC0E7"
        log.write_bytes((
            f'Task::Execute {{"TaskType":"STREAM:{{{task_id}}}","Scenario":"INSTALL"}}\n'
            f'ScenarioController::UpdateScenarioProgress - {{{task_id}}}=1\n'
            f'ScenarioController::UpdateScenarioProgress - {{{task_id}}}=1\n'
            f'ScenarioController::UpdateScenarioProgress - {{{task_id}}}=6\n'
            f'ScenarioController::UpdateScenarioProgress - {{{task_id}}}=5\n'
            f'ScenarioController::UpdateScenarioProgress - {{{task_id}}}=9'
        ).encode("utf-16le"))
        self.assertEqual(progress.poll(), [
            ("start", "STREAM", None), ("progress", "STREAM", 1), ("progress", "STREAM", 6),
        ])
        with log.open("ab") as stream:
            stream.write("\n".encode("utf-16le"))
        self.assertEqual(progress.poll(), [("progress", "STREAM", 9)])

    def test_c2r_download_telemetry_is_deduplicated_and_scoped_to_stream(self):
        log = self.office.prefix / "drive_c/windows/NIXOS-download.log"
        log.parent.mkdir(parents=True)
        progress = officectl.InstallProgress(self.office)
        task_id = "4CCD26FB-A773-42FB-8E44-CD53798BC0E7"
        start = 'C2R::Transport::BGTransportJob::StartDownload::<lambda_1>::operator () ' + json.dumps({
            "ContextData": json.dumps({"message": "Time to start job", "FileName": "stream.x64.en-us.dat"}),
        }) + "\n"
        complete = "ActivityEnded " + json.dumps({
            "Name": "Office.ClickToRun.Transport2", "CV": "transfer.1", "Success": True,
            "Data.SourcePathNoFilePath": "http://officecdn.microsoft.com/Office/Data/stream.x64.en-us.dat",
            "Data.TransferredBytes": "1048576",
        }) + "\n"
        log.write_text(start + complete)
        self.assertEqual(progress.poll(), [])
        with log.open("a") as stream:
            stream.write(
                f'Task::Execute {{"TaskType":"STREAM:{{{task_id}}}","Scenario":"INSTALL"}}\n'
                + start + start + complete + complete
                + 'ActivityEnded {"Name":"Office.ClickToRun.Transport2","Success":true,"Data.TransferredBytes":[]}\n'
                + f'Task::DoHandleWorkerSuccessEvent {{"TaskType":"STREAM:{{{task_id}}}"}}\n'
                + start + complete
            )
        self.assertEqual(progress.poll(), [
            ("start", "STREAM", None), ("download", "stream.x64.en-us.dat", None),
            ("transfer", "stream.x64.en-us.dat", 1048576), ("complete", "STREAM", None),
        ])

    def test_c2r_reports_waiting_status_when_percentage_does_not_change(self):
        progress = officectl.InstallProgress(self.office)
        task_id = "4CCD26FB-A773-42FB-8E44-CD53798BC0E7"
        progress.task_types[task_id] = "STREAM"
        progress.started.add(task_id)
        progress.last_task_percent[task_id] = 1
        stop = Mock()
        stop.is_set.side_effect = [False, False, False, True]
        with patch.object(progress, "poll", return_value=[]), patch.object(progress, "report") as report, \
                patch.object(officectl.time, "monotonic", side_effect=[0, 19, 20, 21]):
            progress.run(stop)
        report.assert_any_call([("status", "STREAM", 1)])
        self.assertEqual(sum(bool(call.args[0]) for call in report.call_args_list), 1)

    def test_c2r_task_and_progress_events_are_merged_by_log_timestamp(self):
        logs = self.office.prefix / "drive_c/users/test/AppData/Local/Temp"
        logs.mkdir(parents=True)
        task_log = logs / "NIXOS-20261004-task.log"
        progress_log = logs / "NIXOS-20261004-progress.log"
        task_log.touch()
        progress_log.touch()
        progress = officectl.InstallProgress(self.office)
        task_id = "4CCD26FB-A773-42FB-8E44-CD53798BC0E7"
        progress_line = (
            f'10/04/2026 17:00:02.000\tOFFICECL\t0x100\tClick-To-Run\t'
            f'ScenarioController::UpdateScenarioProgress - {{{task_id}}}=20\n'
        )
        task_line = (
            f'10/04/2026 17:00:01.000\tOFFICECL\t0x100\tClick-To-Run\tTask::Execute '
            f'{{"TaskType":"STREAM:{{{task_id}}}","Scenario":"INSTALL"}}\n'
        )
        progress_log.write_bytes(progress_line.encode("utf-16le"))
        task_log.write_bytes(task_line.encode("utf-16le"))
        os.utime(task_log, (2, 2))
        os.utime(progress_log, (1, 1))
        self.assertEqual(progress.poll(), [
            ("start", "STREAM", None),
            ("progress", "STREAM", 20),
        ])

    def simulate_install(self, fail=False):
        events = []
        broker = Mock()
        broker.stdin.close.side_effect = lambda: events.append("broker-closed")
        broker.wait.side_effect = lambda **kwargs: events.append("broker-waited")
        watcher = Mock()
        watcher.start.side_effect = lambda: events.append("watcher-started")
        watcher.join.side_effect = lambda **kwargs: events.append("watcher-joined")
        progress_watcher = Mock()
        progress_watcher.start.side_effect = lambda: events.append("progress-started")
        progress_watcher.join.side_effect = lambda **kwargs: events.append("progress-joined")

        def start_broker(command, **kwargs):
            self.assertEqual(command, ["/wine/bin/wine", "/broker.exe"])
            self.assertEqual(kwargs["stdin"], subprocess.PIPE)
            kwargs["stdout"].write("READY broker\n")
            kwargs["stdout"].flush()
            events.append("broker-started")
            return broker

        def run_wine(args, env, log, **kwargs):
            if args[0] == "wineboot":
                self.office.prefix.mkdir(parents=True)
                (self.office.prefix / "system.reg").write_text("fresh prefix")
                events.append("wineboot")
            else:
                self.assertEqual(args[:2], ["/odt/setup.exe", "/configure"])
                events.append("odt")
                if fail:
                    raise officectl.OfficeError("installation failed", 17)
                self.installed_prefix()

        with patch.object(self.office, "run_wine", side_effect=run_wine), \
                patch.object(self.office, "stop_server", side_effect=lambda env: events.append("server-stopped")), \
                patch.object(officectl.subprocess, "Popen", side_effect=start_broker), \
                patch.object(officectl.threading, "Thread", side_effect=[watcher, progress_watcher]), \
                patch.object(self.office, "install_fonts") as fonts, \
                patch.object(officectl.subprocess, "run") as run:
            if fail:
                with self.assertRaisesRegex(officectl.OfficeError, "installation failed"):
                    self.office.init()
            else:
                self.office.init()
            fonts.assert_not_called()
            run.assert_not_called()
        return events

    def test_install_restarts_services_before_helpers_and_keeps_broker_stdin(self):
        events = self.simulate_install()
        self.assertEqual(events, ["wineboot", "server-stopped", "broker-started", "watcher-started",
                                  "progress-started", "odt", "progress-joined", "broker-closed",
                                  "watcher-joined", "server-stopped", "broker-waited"])
        self.assertTrue(self.office.marker.is_file())

    def test_failed_install_preserves_prefix_and_cleans_up_helpers(self):
        events = self.simulate_install(fail=True)
        self.assertEqual(events[-4:], ["broker-closed", "watcher-joined", "server-stopped", "broker-waited"])
        self.assertTrue((self.office.prefix / "system.reg").is_file())
        self.assertFalse(self.office.marker.exists())
        log = next(self.office.logs.glob("init-*"))
        self.assertEqual((log / "install.exit").read_text(), "17\n")


if __name__ == "__main__":
    unittest.main()
