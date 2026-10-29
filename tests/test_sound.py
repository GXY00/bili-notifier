"""Regression checks for sound configuration, controls, and notification routing."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from config_store import load_config, normalise_config, normalise_sound_files, save_config

# Importing the backend normally installs process-wide exception hooks.
with patch("crash_logging.install_crash_logging"):
    import gui

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton


class SoundConfigTests(unittest.TestCase):
    def test_legacy_file_migrates_and_is_saved_without_old_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"sound_file": " C:/声音/提示.wav "}), encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config["sound_files"], ["C:/声音/提示.wav"])
            self.assertNotIn("sound_file", config)
            save_config(path, config)
            self.assertEqual(load_config(path)["sound_files"], config["sound_files"])
            self.assertNotIn("sound_file", json.loads(path.read_text(encoding="utf-8")))

    def test_explicit_empty_list_does_not_restore_legacy_file(self):
        config = normalise_config({"sound_files": [], "sound_file": "old.wav"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config(path, config)
            self.assertEqual(load_config(path)["sound_files"], [])

    def test_windows_path_deduplication_preserves_order_and_distinct_directories(self):
        paths = [" C:/声音/提示.wav ", "c:\\声音\\提示.WAV", "C:/声音/./提示.wav",
                 "D:/声音/提示.wav", None, "", 42]
        self.assertEqual(normalise_sound_files(paths), ["C:/声音/提示.wav", "D:/声音/提示.wav"])

    def test_invalid_lists_and_empty_legacy_values_are_normalised(self):
        for value in (None, "one.wav", 42, {}, [None, "", "  ", 42]):
            with self.subTest(value=value):
                self.assertEqual(normalise_config({"sound_files": value, "sound_file": "old.wav"})["sound_files"], [])
        self.assertEqual(normalise_config({})["sound_files"], [])


class SoundGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.files = []
        for relative in ("声音/提示.wav", "另一个目录/提示.WAV"):
            path = self.directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\0\0" * 80)
            self.files.append(str(path))
        self.stack.enter_context(patch.object(gui, "load_config", side_effect=lambda: normalise_config({})))
        self.stack.enter_context(patch.object(gui, "BilibiliClient"))
        self.stack.enter_context(patch.object(gui, "is_startup_enabled", return_value=False))
        self.stack.enter_context(patch.object(gui.QTimer, "singleShot"))
        self.audio = SimpleNamespace(
            PlaySound=Mock(), MessageBeep=Mock(), SND_FILENAME=0x20000,
            SND_ASYNC=1, SND_NODEFAULT=2, MB_ICONASTERISK=64,
            MB_ICONEXCLAMATION=48, MB_ICONQUESTION=32, MB_ICONHAND=16,
        )
        self.stack.enter_context(patch.object(gui, "winsound", self.audio))
        self.window = gui.BilibiliMonitorApp()
        self.window.event_timer.stop()
        self.window.sound_mode_combo.setCurrentIndex(1)
        self.addCleanup(self.dispose_window)

    def dispose_window(self):
        self.window.closed = True
        self.window.check_timer.stop()
        self.window.event_timer.stop()
        self.window.flash_timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def config(self, **overrides):
        return normalise_config({"sound_mode": "wav", "sound_files": self.files, **overrides})

    def assert_wav_played(self, path):
        self.audio.PlaySound.assert_called_with(
            path, self.audio.SND_FILENAME | self.audio.SND_ASYNC | self.audio.SND_NODEFAULT,
        )
        self.audio.MessageBeep.assert_not_called()

    def test_multiselect_append_deduplicate_and_remove_without_deleting(self):
        with patch.object(gui.QFileDialog, "getOpenFileNames", return_value=(self.files, "")):
            self.window.sound_browse_button.click()
        self.window.add_sound_files([self.files[0].upper(), str(self.directory / "extra.wav")])
        self.assertEqual(self.window.sound_files_from_ui(), self.files + [str(self.directory / "extra.wav")])
        self.assertEqual(self.window.config["sound_files"], [])
        item = self.window.sound_file_list.item(1)
        self.assertEqual(item.toolTip(), self.files[1])
        buttons = self.window.sound_file_list.itemWidget(item).findChildren(QPushButton)
        buttons[1].click()
        self.assertTrue(Path(self.files[1]).is_file())
        self.assertEqual(self.window.sound_files_from_ui(), [self.files[0], str(self.directory / "extra.wav")])

    def test_cancel_add_preserves_list_and_removing_last_disables_random(self):
        self.assertFalse(self.window.random_sound_test_button.isEnabled())
        self.window.add_sound_files(self.files[:1])
        with patch.object(gui.QFileDialog, "getOpenFileNames", return_value=([], "")):
            self.window.sound_browse_button.click()
        self.assertEqual(self.window.sound_files_from_ui(), self.files[:1])
        self.assertTrue(self.window.random_sound_test_button.isEnabled())
        self.window.remove_sound_file(self.window.sound_file_list.item(0))
        self.assertFalse(self.window.random_sound_test_button.isEnabled())

    def test_row_play_uses_its_file_while_muted_and_unsaved(self):
        self.window.add_sound_files(self.files)
        self.window.sound_enabled_check.setChecked(False)
        for index, path in enumerate(self.files):
            item = self.window.sound_file_list.item(index)
            self.window.sound_file_list.itemWidget(item).findChildren(QPushButton)[0].click()
            self.assert_wav_played(path)
        self.assertEqual(self.window.config["sound_files"], [])

    def test_random_preview_uses_unsaved_list_even_when_muted(self):
        self.window.add_sound_files(self.files)
        self.window.sound_enabled_check.setChecked(False)
        with patch.object(gui.random, "choice", return_value=self.files[1]) as choose:
            self.window.random_sound_test_button.click()
        choose.assert_called_once_with(self.files)
        self.assert_wav_played(self.files[1])
        self.assertEqual(self.window.config["sound_files"], [])

    def test_system_mode_controls_and_test_sound(self):
        self.window.add_sound_files(self.files)
        self.window.sound_mode_combo.setCurrentIndex(0)
        self.assertFalse(self.window.sound_file_list.isEnabled())
        self.assertFalse(self.window.sound_browse_button.isEnabled())
        self.assertFalse(self.window.random_sound_test_button.isEnabled())
        self.assertTrue(self.window.system_sound_test_button.isEnabled())
        self.window.sound_enabled_check.setChecked(False)
        self.window.system_sound_combo.setCurrentText(gui.SYSTEM_SOUND_LABELS["question"])
        self.window.system_sound_test_button.click()
        self.audio.PlaySound.assert_called_once_with(None, 0)
        self.audio.MessageBeep.assert_called_once_with(self.audio.MB_ICONQUESTION)
        self.window.sound_mode_combo.setCurrentIndex(1)
        self.assertFalse(self.window.system_sound_test_button.isEnabled())
        self.assertTrue(self.window.random_sound_test_button.isEnabled())

    def test_random_filters_missing_files_and_other_formats_and_allows_repeats(self):
        non_wav = self.directory / "not-a-wave.mp3"
        non_wav.touch()
        config = self.config(sound_files=[self.files[0], str(non_wav), str(self.directory / "missing.wav"), self.files[1]])
        with patch.object(gui.random, "choice", return_value=self.files[0]) as choose:
            self.window.play_notification_sound(config)
            self.window.play_notification_sound(config)
        self.assertEqual(choose.call_count, 2)
        for args in choose.call_args_list:
            self.assertEqual(args.args[0], self.files)
        self.assertEqual(self.audio.PlaySound.call_count, 2)
        self.assert_wav_played(self.files[0])

    def test_one_file_random_works(self):
        self.window.play_notification_sound(self.config(sound_files=self.files[:1]))
        self.assert_wav_played(self.files[0])

    def test_empty_or_all_missing_falls_back_and_keeps_list_entries(self):
        missing = str(self.directory / "missing.wav")
        self.window.add_sound_files([missing])
        for files in ([], [missing]):
            with self.subTest(files=files):
                self.audio.MessageBeep.reset_mock()
                self.window.play_notification_sound(self.config(sound_files=files, system_sound="hand"))
                self.audio.MessageBeep.assert_called_once_with(self.audio.MB_ICONASTERISK)
        self.assertEqual(self.window.sound_files_from_ui(), [missing])
        self.assertIn("没有可用的 WAV", self.window.log_text.toPlainText())

    def test_missing_single_file_does_not_choose_another(self):
        missing = str(self.directory / "missing.wav")
        with patch.object(gui.random, "choice") as choose:
            self.window.play_notification_sound(self.config(), sound_file=missing)
        choose.assert_not_called()
        self.audio.MessageBeep.assert_called_once_with(self.audio.MB_ICONASTERISK)
        self.assertIn(missing, self.window.log_text.toPlainText())

    def test_failed_playback_logs_path_and_falls_back(self):
        def fail_file(path, flags):
            if path is not None:
                raise RuntimeError("invalid WAV")
        self.audio.PlaySound.side_effect = fail_file
        self.window.play_notification_sound(self.config(), sound_file=self.files[0])
        self.audio.MessageBeep.assert_called_once_with(self.audio.MB_ICONASTERISK)
        self.assertIn(self.files[0], self.window.log_text.toPlainText())

    def test_disabled_notification_is_silent(self):
        self.window.play_notification_sound(self.config(sound_enabled=False))
        self.audio.PlaySound.assert_not_called()
        self.audio.MessageBeep.assert_not_called()

    def test_save_reload_restores_files_then_persists_clear(self):
        path = self.directory / "config.json"
        self.window.add_sound_files(self.files)
        with patch.object(gui, "save_config", side_effect=lambda config: save_config(path, config)):
            self.assertTrue(self.window.save_settings(show_message=False))
            loaded = load_config(path)
            self.assertEqual(loaded["sound_files"], self.files)
            self.assertEqual(loaded["sound_mode"], "wav")
            while self.window.sound_file_list.count():
                self.window.remove_sound_file(self.window.sound_file_list.item(0))
            self.assertTrue(self.window.save_settings(show_message=False))
        self.assertEqual(load_config(path)["sound_files"], [])

    def test_one_random_sound_per_nonempty_round_visible_or_hidden(self):
        self.window.config = self.config()
        with patch.object(self.window, "show_results"), patch.object(self.window, "notify_tray") as notify:
            for hidden in (False, True):
                with self.subTest(hidden=hidden):
                    self.audio.PlaySound.reset_mock()
                    notify.reset_mock()
                    self.window.hidden = hidden
                    self.window.events.put(("result", [{}, {}, {}]))
                    self.window.events.put(("result", []))
                    self.window.process_events()
                    self.audio.PlaySound.assert_called_once()
                    self.assertIn(self.audio.PlaySound.call_args.args[0], self.files)
                    self.assertEqual(notify.call_count, int(hidden))

    def test_many_files_scroll_and_save_stays_visible_at_minimum_size(self):
        self.window.add_sound_files([str(self.directory / f"提示音{index}.wav") for index in range(30)])
        self.window.resize(960, 660)
        self.window.switch_page(1)
        self.window.show()
        self.app.processEvents()
        self.assertGreater(self.window.sound_file_list.verticalScrollBar().maximum(), 0)
        item = self.window.sound_file_list.item(0)
        row = self.window.sound_file_list.itemWidget(item)
        for button in row.findChildren(QPushButton):
            self.assertGreaterEqual(button.height(), button.sizeHint().height())
            self.assertTrue(row.rect().contains(button.geometry()))
        save = next(button for button in self.window.pages.widget(1).findChildren(QPushButton)
                    if button.text() == "保存设置")
        self.assertTrue(save.isVisible())
        self.assertTrue(self.window.rect().contains(save.mapTo(self.window, save.rect().bottomRight())))
        # Asynchronous playback must not stop Qt from handling the next event.
        tick = Mock()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(tick)
        timer.start(0)
        for _ in range(5):
            self.window.test_sound_file(self.files[0])
        self.app.processEvents()
        tick.assert_called_once()


if __name__ == "__main__":
    unittest.main()
