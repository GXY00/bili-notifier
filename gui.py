"""PySide6 desktop interface for Bilibili followed-dynamics monitoring."""

import queue
import sys
import threading
import time
import webbrowser
try:
    import winsound
except ImportError:
    winsound = None
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
    QWidget, QMenu,
)

from bilibili_followed_dynamics import (
    BilibiliClient, CookieExpiredError, TaskCancelledError,
    load_config, save_config,
)
from dynamic_monitor import format_dynamic_result
from startup_manager import is_startup_enabled, set_startup_enabled


SYSTEM_SOUND_LABELS = {
    "asterisk": "默认提示音",
    "exclamation": "感叹提示音",
    "question": "询问提示音",
    "hand": "错误提示音",
}
SYSTEM_SOUND_KEYS = {label: key for key, label in SYSTEM_SOUND_LABELS.items()}


def resource_path(filename):
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / filename)
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.append(Path(bundle_dir) / filename)
    candidates.append(Path(__file__).resolve().parent / filename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


APP_STYLE = """
QMainWindow, QWidget { font-family: 'Segoe UI'; font-size: 10pt; color: #1f2937; }
QMainWindow { background: #f3f6fa; }
QFrame#header, QFrame#panel, QGroupBox#settingsGroup { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; }
QFrame#header { border: none; border-bottom: 1px solid #dbe3ed; border-radius: 0; }
QFrame#nav { background: #eef3f8; border-right: 1px solid #dbe3ed; }
QPushButton { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background: #f1f5f9; border-color: #94a3b8; }
QPushButton:pressed { background: #e2e8f0; }
QPushButton#primaryButton { color: #ffffff; background: #2563eb; border-color: #2563eb; font-weight: 600; }
QPushButton#primaryButton:hover { background: #1d4ed8; }
QPushButton#navButton { text-align: left; border: none; background: transparent; padding: 10px 14px; }
QPushButton#navButton:checked { color: #1d4ed8; background: #dbeafe; font-weight: 600; }
QLineEdit, QComboBox, QSpinBox { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px 8px; min-height: 18px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #2563eb; }
QListWidget, QPlainTextEdit, QScrollArea { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; }
QListWidget { outline: none; padding: 4px; }
QListWidget::item { padding: 9px 8px; border-radius: 6px; }
QListWidget::item:hover { background: #f1f5f9; }
QGroupBox { font-weight: 600; margin-top: 10px; padding: 18px 12px 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QCheckBox { spacing: 8px; }
QLabel#muted { color: #64748b; }
QLabel#pageTitle { font-size: 16pt; font-weight: 700; color: #0f172a; }
QLabel#accountLabel { font-size: 13pt; font-weight: 700; color: #0f172a; }
QLabel#statusLabel { color: #475569; }
QPushButton#linkButton { color: #2563eb; background: transparent; border: none; padding: 2px 0; text-align: left; text-decoration: underline; }
QPushButton#linkButton:hover { color: #1d4ed8; }
QFrame#dynamicCard { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; }
"""


class DynamicCard(QFrame):
    def __init__(self, dynamic, open_link, parent=None):
        super().__init__(parent)
        self.setObjectName("dynamicCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(5)
        body, link_text, url = format_dynamic_result(dynamic)
        for line in body.splitlines():
            label = QLabel(line)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if line.startswith("UP主："):
                label.setStyleSheet("font-weight: 700; color: #0f172a;")
            elif line.startswith("更新时间："):
                label.setObjectName("muted")
            layout.addWidget(label)
        link = QPushButton(link_text)
        link.setObjectName("linkButton")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.clicked.connect(lambda: open_link(url))
        link.setToolTip(url)
        layout.addWidget(link, alignment=Qt.AlignmentFlag.AlignLeft)


class BilibiliMonitorApp(QMainWindow):
    def __init__(self, startup=False):
        super().__init__()
        self.startup_requested = startup
        self.setWindowTitle("Bilibili 关注动态监控")
        self.setWindowIcon(QIcon(str(resource_path("app.ico"))))
        self.resize(1180, 780)
        self.setMinimumSize(960, 660)
        self.setStyleSheet(APP_STYLE)

        self.config = load_config()
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.client = BilibiliClient(self.post_status, cancel_event=self.cancel_event)
        self.followings = []
        self.monitoring = False
        self.busy = False
        self.closed = False
        self.hidden = False
        self.logged_in = False
        self.start_after_login = False
        self.next_check_at = None
        self.last_check_at = None
        self.refresh_selection_snapshot = None
        self.tray_icon = None
        self.tray_normal_icon = self.windowIcon()
        self.tray_alert_icon = self._make_alert_icon()
        self.flash_alert = False

        self.saved_startup_enabled = is_startup_enabled()
        self.close_behavior_value = self.config.get("close_behavior") == "tray"

        self.check_timer = QTimer(self)
        self.check_timer.setSingleShot(True)
        self.check_timer.timeout.connect(self.auto_check)
        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self.process_events)
        self.event_timer.start(100)
        self.flash_timer = QTimer(self)
        self.flash_timer.timeout.connect(self._flash_tray_icon)

        self.build_ui()
        QTimer.singleShot(500, self.start_login)

    def _make_alert_icon(self):
        pixmap = self.windowIcon().pixmap(64, 64)
        if pixmap.isNull():
            return QIcon()
        painter = QPainter(pixmap)
        painter.setBrush(QColor("#dc2626"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(43, 3, 18, 18)
        painter.end()
        return QIcon(pixmap)

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 16, 24, 14)
        account_area = QVBoxLayout()
        self.account_label = QLabel("账号：未登录")
        self.account_label.setObjectName("accountLabel")
        account_area.addWidget(self.account_label)
        subtitle = QLabel("Bilibili 关注动态监控")
        subtitle.setObjectName("muted")
        account_area.addWidget(subtitle)
        header_layout.addLayout(account_area)
        header_layout.addStretch()
        status_area = QVBoxLayout()
        self.monitor_state_label = QLabel("状态：未启动")
        self.check_time_label = QLabel("上次检查：未执行")
        self.status_label = QLabel("准备登录")
        for label in (self.monitor_state_label, self.check_time_label, self.status_label):
            label.setObjectName("statusLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignRight)
            status_area.addWidget(label)
        header_layout.addLayout(status_area)
        root_layout.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root_layout.addLayout(body, 1)

        nav = QFrame()
        nav.setObjectName("nav")
        nav.setFixedWidth(170)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(12, 18, 12, 12)
        nav_layout.setSpacing(4)
        nav_caption = QLabel("工作区")
        nav_caption.setObjectName("muted")
        nav_layout.addWidget(nav_caption)
        self.nav_buttons = []
        for title, index in (("动态", 0), ("设置", 1), ("运行日志", 2)):
            button = QPushButton(title)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, value=index: self.switch_page(value))
            nav_layout.addWidget(button)
            self.nav_buttons.append(button)
        nav_layout.addStretch()
        body.addWidget(nav)

        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        self.pages.addWidget(self.build_dynamic_page())
        self.pages.addWidget(self.build_settings_page())
        self.pages.addWidget(self.build_log_page())
        self.switch_page(0)

        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 10, 24, 12)
        self.monitor_button = QPushButton("开始监控")
        self.monitor_button.setObjectName("primaryButton")
        self.monitor_button.clicked.connect(self.toggle_monitoring)
        self.check_button = QPushButton("立即检查")
        self.check_button.clicked.connect(self.manual_check)
        exit_button = QPushButton("退出")
        exit_button.clicked.connect(self.exit_app)
        footer_layout.addWidget(self.monitor_button)
        footer_layout.addWidget(self.check_button)
        footer_layout.addStretch()
        footer_layout.addWidget(exit_button)
        root_layout.addWidget(footer)

    def build_dynamic_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 20, 22, 16)
        title = QLabel("动态")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        splitter = QHBoxLayout()
        splitter.setSpacing(16)
        layout.addLayout(splitter, 1)

        follow_panel = QFrame()
        follow_panel.setObjectName("panel")
        follow_layout = QVBoxLayout(follow_panel)
        follow_layout.setContentsMargins(14, 14, 14, 14)
        follow_layout.addWidget(QLabel("关注 UP 主"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索昵称或 MID")
        self.search_edit.textChanged.connect(self.filter_followings)
        follow_layout.addWidget(self.search_edit)
        actions = QHBoxLayout()
        all_button = QPushButton("全选")
        all_button.clicked.connect(lambda: self.set_all_selection(True))
        clear_button = QPushButton("清空")
        clear_button.clicked.connect(lambda: self.set_all_selection(False))
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_followings)
        actions.addWidget(all_button)
        actions.addWidget(clear_button)
        actions.addWidget(refresh_button)
        follow_layout.addLayout(actions)
        self.follow_count_label = QLabel("未加载关注列表")
        self.follow_count_label.setObjectName("muted")
        follow_layout.addWidget(self.follow_count_label)
        self.follow_list = QListWidget()
        self.follow_list.itemChanged.connect(self._selection_changed)
        follow_layout.addWidget(self.follow_list, 1)
        splitter.addWidget(follow_panel)

        result_panel = QFrame()
        result_panel.setObjectName("panel")
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(14, 14, 14, 14)
        result_layout.addWidget(QLabel("动态结果"))
        self.result_stack = QStackedWidget()
        self.qr_page = self.build_qr_page()
        self.result_page = self.build_result_page()
        self.result_stack.addWidget(self.qr_page)
        self.result_stack.addWidget(self.result_page)
        result_layout.addWidget(self.result_stack, 1)
        splitter.addWidget(result_panel)
        splitter.setStretch(0, 1)
        splitter.setStretch(1, 2)
        return page

    def build_qr_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label = QLabel("等待二维码")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(300, 300)
        self.qr_label.setStyleSheet("background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px;")
        layout.addWidget(self.qr_label)
        self.qr_status_label = QLabel("正在准备登录")
        self.qr_status_label.setObjectName("muted")
        self.qr_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.qr_status_label)
        login_button = QPushButton("重新登录")
        login_button.clicked.connect(lambda: self.start_login(force=True))
        layout.addWidget(login_button, alignment=Qt.AlignmentFlag.AlignCenter)
        return page

    def build_result_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        self.result_scroll = QScrollArea()
        self.result_scroll.setWidgetResizable(True)
        self.result_container = QWidget()
        self.result_layout = QVBoxLayout(self.result_container)
        self.result_layout.setContentsMargins(8, 8, 8, 8)
        self.result_layout.setSpacing(10)
        self.result_layout.addStretch()
        self.result_scroll.setWidget(self.result_container)
        outer.addWidget(self.result_scroll)
        return page

    def build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 20, 22, 16)
        title = QLabel("设置")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        group = QGroupBox("监控设置")
        group.setObjectName("settingsGroup")
        form = QGridLayout(group)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(12)
        form.addWidget(QLabel("检查间隔（分钟）"), 0, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 10080)
        self.interval_spin.setValue(int(self.config.get("check_interval_minutes", 1)))
        form.addWidget(self.interval_spin, 0, 1)
        form.addWidget(QLabel("动态类型"), 1, 0, Qt.AlignmentFlag.AlignTop)
        type_area = QHBoxLayout()
        self.type_checks = {}
        for label, key in (("视频", "DYNAMIC_TYPE_AV"), ("图文", "DYNAMIC_TYPE_DRAW"), ("转发", "DYNAMIC_TYPE_FORWARD")):
            check = QCheckBox(label)
            check.setChecked(key in self.config["followed_dynamic_types"])
            self.type_checks[key] = check
            type_area.addWidget(check)
        type_area.addStretch()
        form.addLayout(type_area, 1, 1)
        form.addWidget(QLabel("关闭窗口"), 2, 0)
        self.close_combo = QComboBox()
        self.close_combo.addItems(("隐藏到系统托盘", "直接退出"))
        self.close_combo.setCurrentIndex(0 if self.close_behavior_value else 1)
        form.addWidget(self.close_combo, 2, 1)
        self.startup_check = QCheckBox("登录 Windows 后自动启动并隐藏到系统托盘")
        self.startup_check.setChecked(self.saved_startup_enabled)
        form.addWidget(self.startup_check, 3, 0, 1, 2)
        layout.addWidget(group)

        sound_group = QGroupBox("声音提示")
        sound_group.setObjectName("settingsGroup")
        sound_layout = QGridLayout(sound_group)
        self.sound_enabled_check = QCheckBox("发现新动态时播放声音")
        self.sound_enabled_check.setChecked(self.config.get("sound_enabled", True))
        sound_layout.addWidget(self.sound_enabled_check, 0, 0, 1, 3)
        sound_layout.addWidget(QLabel("声音来源"), 1, 0)
        self.sound_mode_combo = QComboBox()
        self.sound_mode_combo.addItems(("系统音效", "自定义 WAV"))
        self.sound_mode_combo.setCurrentIndex(0 if self.config.get("sound_mode") == "system" else 1)
        self.sound_mode_combo.currentIndexChanged.connect(self.update_sound_controls)
        sound_layout.addWidget(self.sound_mode_combo, 1, 1)
        sound_layout.addWidget(QLabel("系统音效"), 2, 0)
        self.system_sound_combo = QComboBox()
        self.system_sound_combo.addItems(tuple(SYSTEM_SOUND_LABELS.values()))
        self.system_sound_combo.setCurrentText(SYSTEM_SOUND_LABELS.get(self.config.get("system_sound"), SYSTEM_SOUND_LABELS["asterisk"]))
        sound_layout.addWidget(self.system_sound_combo, 2, 1)
        self.sound_file_edit = QLineEdit(self.config.get("sound_file", ""))
        self.sound_file_edit.setPlaceholderText("选择本地 .wav 文件")
        self.sound_browse_button = QPushButton("选择 WAV")
        self.sound_browse_button.clicked.connect(self.select_sound_file)
        sound_layout.addWidget(QLabel("WAV 文件"), 3, 0)
        sound_layout.addWidget(self.sound_file_edit, 3, 1)
        sound_layout.addWidget(self.sound_browse_button, 3, 2)
        test_button = QPushButton("测试声音")
        test_button.clicked.connect(self.test_sound)
        sound_layout.addWidget(test_button, 4, 0)
        sound_note = QLabel("每轮检查最多播放一次；WAV 无法播放时使用默认系统提示音。")
        sound_note.setObjectName("muted")
        sound_layout.addWidget(sound_note, 4, 1, 1, 2)
        layout.addWidget(sound_group)

        layout.addStretch()
        save_button = QPushButton("保存设置")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.update_sound_controls()
        return page

    def build_log_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 20, 22, 16)
        title = QLabel("运行日志")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        return page

    def switch_page(self, index):
        self.pages.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)

    def post_status(self, message):
        self.events.put(("status", message))

    def post_qr(self, path):
        self.events.put(("qr", str(path)))

    def start_thread(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True).start()

    def start_login(self, force=False):
        if self.busy or self.closed:
            return
        self.start_after_login = True
        self.pause_monitoring(cancel=False)
        self.logged_in = False
        self.busy = True
        self.cancel_event.clear()
        self.status_label.setText("正在登录")
        self.show_qr_area()
        if force:
            self.client.account = None
        self.start_thread(self.login_worker, force)

    def login_worker(self, force=False):
        try:
            account = self.client.login(self.post_status, self.post_qr, force=force)
            self.events.put(("account", account))
            self.events.put(("login_success", None))
            self.events.put(("followings", self.client.get_followings()))
        except TaskCancelledError:
            self.events.put(("status", "登录任务已取消"))
        except CookieExpiredError as error:
            self.events.put(("cookie_expired", str(error)))
        except Exception as error:
            self.events.put(("error", f"登录或读取关注列表失败：{error}"))
        finally:
            self.events.put(("idle", None))

    def refresh_followings(self):
        if self.busy or not self.client.account:
            self.post_status("请先完成登录")
            return
        self.busy = True
        self.cancel_event.clear()
        self.refresh_selection_snapshot = set(self.selected_mids())
        self.start_thread(self.refresh_followings_worker)

    def refresh_followings_worker(self):
        try:
            self.events.put(("followings", self.client.get_followings()))
        except TaskCancelledError:
            self.events.put(("status", "刷新关注列表已取消"))
        except CookieExpiredError as error:
            self.events.put(("cookie_expired", str(error)))
        except Exception as error:
            self.events.put(("error", f"刷新关注列表失败：{error}"))
        finally:
            self.events.put(("idle", None))

    def selected_mids(self):
        return [self.follow_list.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(self.follow_list.count())
                if self.follow_list.item(index).checkState() == Qt.CheckState.Checked]

    def filter_followings(self, text):
        keyword = text.strip().lower()
        visible = 0
        for index in range(self.follow_list.count()):
            item = self.follow_list.item(index)
            user = item.data(Qt.ItemDataRole.UserRole + 1)
            matches = not keyword or keyword in user["name"].lower() or keyword in user["mid"]
            item.setHidden(not matches)
            visible += int(matches)
        self.follow_count_label.setText(f"显示 {visible} / 共 {len(self.followings)}")

    def _selection_changed(self, _item):
        self.config["followed_mids"] = self.selected_mids()

    def update_sound_controls(self):
        custom = self.sound_mode_combo.currentIndex() == 1
        self.system_sound_combo.setEnabled(not custom)
        self.sound_file_edit.setEnabled(custom)
        self.sound_browse_button.setEnabled(custom)

    def select_sound_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择提示音", "", "WAV 音频 (*.wav)")
        if path:
            self.sound_file_edit.setText(str(Path(path).resolve()))

    def _sound_config_from_ui(self):
        return {
            "sound_enabled": self.sound_enabled_check.isChecked(),
            "sound_mode": "wav" if self.sound_mode_combo.currentIndex() == 1 else "system",
            "system_sound": SYSTEM_SOUND_KEYS.get(self.system_sound_combo.currentText(), "asterisk"),
            "sound_file": self.sound_file_edit.text().strip(),
        }

    def test_sound(self):
        sound_config = self._sound_config_from_ui()
        sound_config["sound_enabled"] = True
        self.play_notification_sound(sound_config)

    def play_notification_sound(self, sound_config=None):
        sound_config = sound_config or self.config
        if not sound_config.get("sound_enabled", True):
            return
        if winsound is None:
            QApplication.beep()
            self.write_log("当前系统不支持 Windows 声音接口，已使用 Qt 窗口提示音")
            return
        fallback_to_default = False
        if sound_config.get("sound_mode") == "wav":
            sound_file = sound_config.get("sound_file", "")
            if sound_file and Path(sound_file).suffix.lower() == ".wav" and Path(sound_file).is_file():
                try:
                    winsound.PlaySound(sound_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    return
                except (OSError, RuntimeError) as error:
                    self.write_log(f"自定义 WAV 播放失败：{error}，已回退到默认系统提示音")
                    fallback_to_default = True
            else:
                self.write_log("自定义 WAV 文件不存在或格式不是 WAV，已回退到默认系统提示音")
                fallback_to_default = True
        sound_types = {
            "asterisk": winsound.MB_ICONASTERISK,
            "exclamation": winsound.MB_ICONEXCLAMATION,
            "question": winsound.MB_ICONQUESTION,
            "hand": winsound.MB_ICONHAND,
        }
        try:
            system_sound = "asterisk" if fallback_to_default else sound_config.get("system_sound", "asterisk")
            winsound.MessageBeep(sound_types.get(system_sound, winsound.MB_ICONASTERISK))
        except (OSError, RuntimeError) as error:
            self.write_log(f"播放系统提示音失败：{error}")

    def save_settings(self, show_message=True):
        types = [key for key, check in self.type_checks.items() if check.isChecked()]
        if not types:
            QMessageBox.warning(self, "设置错误", "至少选择一种动态类型")
            return False
        if show_message:
            try:
                if self.startup_check.isChecked() or self.saved_startup_enabled:
                    set_startup_enabled(self.startup_check.isChecked())
                self.saved_startup_enabled = self.startup_check.isChecked()
            except (OSError, RuntimeError) as error:
                self.startup_check.setChecked(self.saved_startup_enabled)
                self.post_status(f"开机自启设置失败：{error}，其他设置仍会保存")
        self.config.update({
            "check_interval_minutes": self.interval_spin.value(),
            "followed_dynamic_types": types,
            "followed_mids": self.selected_mids() if self.followings else self.config["followed_mids"],
            "close_behavior": "tray" if self.close_combo.currentIndex() == 0 else "exit",
            **self._sound_config_from_ui(),
        })
        try:
            save_config(self.config)
        except OSError as error:
            self.post_status(f"配置保存失败：{error}")
            return False
        if show_message and self.monitoring and not self.busy:
            self.schedule_next_check()
        if show_message:
            self.post_status(f"设置已保存：{len(self.config['followed_mids'])} 个 UP 主，间隔 {self.interval_spin.value()} 分钟")
        return True

    def set_all_selection(self, selected):
        self.follow_list.blockSignals(True)
        for index in range(self.follow_list.count()):
            self.follow_list.item(index).setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
        self.follow_list.blockSignals(False)
        self.config["followed_mids"] = self.selected_mids()

    def update_followings(self, followings):
        prior = self.refresh_selection_snapshot if self.refresh_selection_snapshot is not None else set(self.config.get("followed_mids", []))
        self.refresh_selection_snapshot = None
        available = {user["mid"] for user in followings}
        self.followings = followings
        self.follow_list.blockSignals(True)
        self.follow_list.clear()
        for user in followings:
            item = QListWidgetItem(f"{user['name']}\nMID: {user['mid']}")
            item.setData(Qt.ItemDataRole.UserRole, user["mid"])
            item.setData(Qt.ItemDataRole.UserRole + 1, user)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if user["mid"] in prior else Qt.CheckState.Unchecked)
            self.follow_list.addItem(item)
        self.follow_list.blockSignals(False)
        removed = prior - available
        if removed:
            self.post_status(f"以下已选 MID 已不在关注列表中，已取消监控：{', '.join(sorted(removed))}")
        self.config["followed_mids"] = sorted(prior & available)
        self.filter_followings(self.search_edit.text())

    def toggle_monitoring(self):
        if self.monitoring:
            self.pause_monitoring()
            self.post_status("自动监控已暂停")
            return
        if self.busy:
            self.post_status("已有任务正在执行，请稍候")
            return
        if not self.logged_in or not self.followings or not self.selected_mids():
            self.post_status("请先登录、加载关注列表并选择监控对象")
            return
        if not self.save_settings(show_message=False):
            return
        self.monitoring = True
        self.cancel_event.clear()
        self.monitor_button.setText("暂停监控")
        self.monitor_state_label.setText("状态：运行中")
        self.post_status("自动监控已启动")
        self.schedule_next_check(immediate=True)

    def pause_monitoring(self, cancel=True):
        self.monitoring = False
        if cancel:
            self.cancel_event.set()
        self.check_timer.stop()
        self.next_check_at = None
        self.monitor_button.setText("开始监控")
        self.monitor_state_label.setText("状态：已暂停")
        self.check_time_label.setText(self._check_time_text())

    def schedule_next_check(self, immediate=False):
        if self.closed or not self.monitoring:
            return
        delay = 0 if immediate else self.config["check_interval_minutes"] * 60 * 1000
        self.next_check_at = time.time() + delay / 1000
        self.check_time_label.setText(self._check_time_text())
        self.check_timer.start(delay)

    def auto_check(self):
        if self.monitoring:
            if self.busy:
                self.schedule_next_check()
            else:
                self.perform_check()

    def manual_check(self):
        if self.busy:
            self.post_status("已有任务正在执行，请稍候")
            return
        if not self.logged_in or not self.selected_mids():
            self.post_status("未选择监控对象，无法检查")
            return
        if self.save_settings(show_message=False):
            self.perform_check()

    def perform_check(self):
        if self.busy or not self.logged_in:
            return
        self.busy = True
        self.cancel_event.clear()
        self.check_button.setEnabled(False)
        self.post_status("正在检查动态")
        self.start_thread(self.check_worker, list(self.config["followed_mids"]), list(self.config["followed_dynamic_types"]))

    def check_worker(self, mids, types):
        try:
            def check_status(message):
                if message not in ("本次无新增动态",) and not message.startswith("发现 "):
                    self.post_status(message)
            dynamics = self.client.get_followed_dynamics(mids, types, check_status)
            self.events.put(("result", dynamics))
            self.events.put(("checked", time.time()))
        except TaskCancelledError:
            self.events.put(("status", "检查任务已取消"))
        except CookieExpiredError as error:
            self.events.put(("cookie_expired", str(error)))
        except Exception as error:
            self.events.put(("error", f"检查动态失败：{error}"))
        finally:
            self.events.put(("idle", None))
            self.events.put(("schedule", None))

    def process_events(self):
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    self.status_label.setText(payload)
                    self.write_log(payload)
                elif event == "qr":
                    self.show_qr(payload)
                elif event == "account":
                    self.account_label.setText(f"账号：{payload['name']}（MID: {payload['mid']}）")
                elif event == "login_success":
                    self.logged_in = True
                    self.show_result_area()
                    self.write_log("登录成功，二维码区域已切换为动态结果区域")
                    if self.startup_requested:
                        self.startup_requested = False
                        self.hide_to_tray()
                elif event == "followings":
                    self.update_followings(payload)
                    self.post_status(f"已加载 {len(payload)} 个关注对象")
                elif event == "result":
                    self.show_results(payload)
                    if payload:
                        self.play_notification_sound()
                        if self.hidden:
                            self.notify_tray()
                elif event == "checked":
                    self.last_check_at = payload
                    self.check_time_label.setText(self._check_time_text())
                    self.write_log("动态检查完成")
                elif event == "error":
                    self.status_label.setText("发生错误")
                    self.write_log(payload)
                elif event == "cookie_expired":
                    self.pause_monitoring()
                    self.logged_in = False
                    self.client.account = None
                    if self.hidden:
                        self._show_from_tray()
                    self.monitor_state_label.setText("状态：需要重新登录")
                    self.status_label.setText("Cookie 已失效")
                    self.show_qr_area()
                    self.qr_label.setText("登录已失效，请点击下方“重新登录”")
                    self.write_log(f"{payload}，请点击“重新登录”")
                elif event == "idle":
                    self.busy = False
                    self.check_button.setEnabled(True)
                    if self.start_after_login:
                        self.start_after_login = False
                        if self.logged_in and self.selected_mids():
                            self.toggle_monitoring()
                elif event == "schedule":
                    self.schedule_next_check()
        except queue.Empty:
            pass

    def show_qr_area(self):
        self.result_stack.setCurrentWidget(self.qr_page)

    def show_result_area(self):
        self.result_stack.setCurrentWidget(self.result_page)

    def show_qr(self, path):
        self.show_qr_area()
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.write_log(f"二维码显示失败，文件位置：{path}")
            return
        self.qr_label.setPixmap(pixmap.scaled(300, 300, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.qr_label.setText("")
        self.qr_status_label.setText("请使用哔哩哔哩 App 扫码登录")

    def show_results(self, dynamics):
        self.show_result_area()
        if not dynamics:
            return
        for dynamic in dynamics:
            card = DynamicCard(dynamic, self.open_result_link)
            self.result_layout.insertWidget(self.result_layout.count() - 1, card)
        self.result_scroll.verticalScrollBar().setValue(self.result_scroll.verticalScrollBar().maximum())

    def open_result_link(self, url):
        try:
            if not QDesktopServices.openUrl(QUrl(url)):
                self.write_log(f"无法打开浏览器，请复制网址手动打开：{url}")
        except (webbrowser.Error, OSError, RuntimeError) as error:
            self.write_log(f"打开浏览器失败：{error}，请复制网址手动打开：{url}")

    def write_log(self, message):
        self.log_text.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _check_time_text(self):
        last = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_check_at)) if self.last_check_at else "未执行"
        if self.next_check_at and self.monitoring:
            next_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.next_check_at))
            return f"上次检查：{last}；下次：{next_time}"
        return f"上次检查：{last}；下次：暂停"

    def create_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.post_status("当前系统不支持系统托盘，无法隐藏窗口")
            return False
        self.tray_icon = QSystemTrayIcon(self.tray_normal_icon, self)
        menu = QMenu(self)
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self._show_from_tray)
        exit_action = QAction("退出程序", self)
        exit_action.triggered.connect(self.exit_app)
        menu.addAction(show_action)
        menu.addAction(exit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(lambda reason: self._show_from_tray() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray_icon.show()
        return True

    def hide_to_tray(self):
        if self.closed:
            return False
        if self.tray_icon is None and not self.create_tray_icon():
            return False
        self.hidden = True
        self.hide()
        self.post_status("窗口已隐藏到系统托盘")
        return True

    def notify_tray(self):
        if self.tray_icon and self.hidden and not self.closed:
            self.flash_timer.start(500)

    def _flash_tray_icon(self):
        self.flash_alert = not self.flash_alert
        self.tray_icon.setIcon(self.tray_alert_icon if self.flash_alert else self.tray_normal_icon)

    def stop_tray_alert(self):
        self.flash_timer.stop()
        self.flash_alert = False
        if self.tray_icon:
            self.tray_icon.setIcon(self.tray_normal_icon)

    def _show_from_tray(self):
        if self.closed:
            return
        self.stop_tray_alert()
        self.hidden = False
        self.showNormal()
        self.activateWindow()
        self.raise_()
        self.post_status("窗口已恢复")

    def handle_window_close(self):
        if self.close_combo.currentIndex() == 0:
            self.config["close_behavior"] = "tray"
            save_config(self.config)
            return self.hide_to_tray()
        else:
            self.exit_app()
            return True

    def closeEvent(self, event):
        if not self.closed and self.close_combo.currentIndex() == 0:
            if self.handle_window_close():
                event.ignore()
            else:
                self.exit_app()
                event.accept()
        else:
            event.accept()

    def exit_app(self):
        if self.closed:
            return
        self.closed = True
        self.monitoring = False
        self.cancel_event.set()
        self.check_timer.stop()
        self.event_timer.stop()
        self.stop_tray_alert()
        if self.tray_icon:
            self.tray_icon.hide()
        QApplication.quit()


def run_app(startup=False):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("BilibiliNotifier")
    window = BilibiliMonitorApp(startup=startup)
    window.show()
    return app.exec()


if __name__ == "__main__":
    run_app()
