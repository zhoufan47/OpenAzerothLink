import sys
import json
import os
import io
import logging
import platform
import traceback
import httpx
import base64
from datetime import datetime
from logging.handlers import RotatingFileHandler

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QSystemTrayIcon, QMenu, QDialog, QLineEdit,
                             QFormLayout, QSpinBox, QTextEdit, QMessageBox, QComboBox,
                             QFrame, QSizePolicy, QCheckBox)
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QRect, QPoint, QSize,
                          QBuffer, QByteArray, QIODevice)
from PyQt6.QtGui import (QIcon, QAction, QPixmap, QPainter, QColor, QCursor,
                         QGuiApplication)
from PIL import Image, ImageGrab
import pytesseract


# ==========================================
# 0. 日志与国际化配置
# ==========================================
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')

    file_handler = RotatingFileHandler('../app.log', maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.critical("Uncaught exception:", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = handle_exception
logger = setup_logging()

# --- 国际化字典 ---
I18N = {
    "zh_CN": {
        "tray_region": "设置监控区域",
        "tray_settings": "设置参数",
        "tray_stats": "Token 统计",
        "tray_exit": "退出",
        "msg_region_updated": "监控区域已更新",
        "msg_no_region": "请先设置监控区域",
        "msg_screenshot_fail": "截图失败",
        "msg_ocr_empty": "未识别到文字",
        "msg_api_key_missing": "错误: 未配置 API Key",
        "msg_net_error": "网络连接失败",
        "msg_processing": "识别中...",
        "msg_waiting": "等待翻译...",
        "msg_prev_task": "上个任务进行中...",
        "settings_title": "软件设置",
        "lbl_api_base": "API Base URL:",
        "lbl_api_key": "API Key:",
        "lbl_model": "模型名称:",
        "lbl_timeout": "超时(秒):",
        "lbl_proxy": "代理地址 (Proxy):",
        "lbl_lang": "语言 (Language):",
        "lbl_prompt": "Prompt:",
        "chk_advanced_mode": "高级模式 (跳过OCR，直接发送图片)",
        "tip_advanced_mode": "⚠️ 注意：高级模式需要模型支持视觉识别 (Vision)，且消耗更多 Token。",
        "btn_save": "保存",
        "stats_title": "Token 消耗统计",
        "stats_session": "本次运行消耗:",
        "stats_total": "历史总计消耗:",
        "stats_input": "输入 (Prompt):",
        "stats_output": "输出 (Completion):",
        "stats_day": "今日消耗:",
        "overlay_tokens": "消耗: {} (In) + {} (Out)",
        "prompt_placeholder": "例如：请翻译...",
        "proxy_placeholder": "例如: http://127.0.0.1:7890 (留空则不使用)"
    },
    "en_US": {
        "tray_region": "Set Region",
        "tray_settings": "Settings",
        "tray_stats": "Statistics",
        "tray_exit": "Exit",
        "msg_region_updated": "Region updated",
        "msg_no_region": "Please set region first",
        "msg_screenshot_fail": "Screenshot failed",
        "msg_ocr_empty": "No text detected",
        "msg_api_key_missing": "Error: API Key missing",
        "msg_net_error": "Network connection failed",
        "msg_processing": "Processing...",
        "msg_waiting": "Waiting...",
        "msg_prev_task": "Task in progress...",
        "settings_title": "Settings",
        "lbl_api_base": "API Base URL:",
        "lbl_api_key": "API Key:",
        "lbl_model": "Model:",
        "lbl_timeout": "Timeout (s):",
        "lbl_proxy": "Proxy:",
        "lbl_lang": "Language:",
        "lbl_prompt": "Prompt:",
        "chk_advanced_mode": "Advanced Mode (Send image directly)",
        "tip_advanced_mode": "⚠️ Note: Requires Vision-capable model. Consumes more tokens.",
        "btn_save": "Save",
        "stats_title": "Token Statistics",
        "stats_session": "Current Session:",
        "stats_total": "Historical Total:",
        "stats_input": "Input (Prompt):",
        "stats_output": "Output (Completion):",
        "stats_day": "Today:",
        "overlay_tokens": "Tokens: {} (In) + {} (Out)",
        "prompt_placeholder": "E.g.: Please translate...",
        "proxy_placeholder": "E.g.: http://127.0.0.1:7890"
    }
}


# ==========================================
# 1. 配置与数据管理
# ==========================================
class ConfigManager:
    def __init__(self, filename="config.json"):
        self.filename = filename
        self.default_config = {
            "api_base": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-3.5-turbo",
            "timeout": 30,
            "region": [0, 0, 0, 0],
            "custom_prompt": "请将以下内容翻译成中文（如果是中文则润色），直接输出结果，不要包含额外解释：",
            "proxy": "",
            "language": "zh_CN",  # zh_CN or en_US
            "overlay_pos": None,  # [新增] [x, y]
            "advanced_mode": False  # [新增] 高级模式开关
        }
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    config = self.default_config.copy()
                    config.update(loaded)
                    logging.info("配置加载成功")
                    return config
            except Exception as e:
                logging.error(f"加载配置文件失败: {e}")
                return self.default_config
        return self.default_config

    def save_config(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            logging.error(f"保存配置失败: {e}")

    def get(self, key, default=None):
        val = self.config.get(key)
        if val is None:
            return default if default is not None else self.default_config.get(key)
        return val

    def set(self, key, value):
        self.config[key] = value
        self.save_config()

    # I18N Helper
    def tr(self, key):
        lang = self.config.get("language", "zh_CN")
        return I18N.get(lang, I18N["zh_CN"]).get(key, key)


class TokenManager:
    """管理 Token 消耗统计，保存至 cost.json"""

    def __init__(self, filename="cost.json"):
        self.filename = filename
        self.session_input = 0
        self.session_output = 0
        self.data = self.load_data()

    def load_data(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {"history": {}, "total": {"input": 0, "output": 0}}

    def save_data(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            logging.error(f"保存 Token 数据失败: {e}")

    def record_usage(self, input_tokens, output_tokens):
        # 1. 更新本次会话
        self.session_input += input_tokens
        self.session_output += output_tokens

        # 2. 更新按天统计
        today = datetime.now().strftime("%Y-%m-%d")
        if "history" not in self.data: self.data["history"] = {}
        if today not in self.data["history"]:
            self.data["history"][today] = {"input": 0, "output": 0}

        self.data["history"][today]["input"] += input_tokens
        self.data["history"][today]["output"] += output_tokens

        # 3. 更新总计
        if "total" not in self.data: self.data["total"] = {"input": 0, "output": 0}
        self.data["total"]["input"] += input_tokens
        self.data["total"]["output"] += output_tokens

        self.save_data()

    def get_stats(self):
        today = datetime.now().strftime("%Y-%m-%d")
        today_data = self.data.get("history", {}).get(today, {"input": 0, "output": 0})
        total_data = self.data.get("total", {"input": 0, "output": 0})
        return {
            "session": {"input": self.session_input, "output": self.session_output},
            "today": today_data,
            "total": total_data
        }


# ==========================================
# 2. 后台工作线程
# ==========================================
class TranslationWorker(QThread):
    finished = pyqtSignal(str, dict)  # 结果文本, usage字典
    error = pyqtSignal(str)

    def __init__(self, config, image_data):
        super().__init__()
        self.config = config
        self.image_data = image_data

    def run(self):
        try:
            # 1. 预处理图像
            logging.info("Worker: 开始处理任务")
            try:
                pil_bytes = io.BytesIO(self.image_data)
                image = Image.open(pil_bytes)
            except Exception as e:
                logging.error(f"PIL 读取图像失败,{str(e)}", exc_info=True)
                self.error.emit("Image Data Error")
                return

            # 2. 检查模式
            is_advanced = self.config.get("advanced_mode", False)
            messages = []

            if is_advanced:
                logging.info("使用高级模式 (Vision)")
                # 高级模式：转换为 JPG -> Base64 -> Vision Payload
                if image.mode in ("RGBA", "P"):
                    image = image.convert("RGB")

                jpg_buffer = io.BytesIO()
                # 质量设置为 85
                image.save(jpg_buffer, format='JPEG', quality=85)
                jpg_base64 = base64.b64encode(jpg_buffer.getvalue()).decode('utf-8')

                # 构造符合 OpenAI Vision 格式的消息
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": self.config.get("custom_prompt")},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{jpg_base64}"
                                }
                            }
                        ]
                    }
                ]
            else:
                logging.info("使用普通模式 (OCR)")
                # 普通模式：本地 OCR -> 纯文本
                try:
                    text = pytesseract.image_to_string(image, lang='chi_sim+eng')
                except Exception as e:
                    self.error.emit(f"OCR Error: {str(e)}")
                    return

                text = text.strip()
                logging.info(f"OCR 成功，字符数: {len(text)}")
                logging.info(f"内容:{text}")
                if not text:
                    self.error.emit(self.config.tr("msg_ocr_empty"))
                    return

                full_content = f"{self.config.get('custom_prompt')}\n\n{text}"
                messages = [{"role": "user", "content": full_content}]

            # 3. 发送请求
            api_base = self.config.get("api_base").strip()
            api_key = self.config.get("api_key").strip()
            proxy_url = self.config.get("proxy").strip()

            if not api_key:
                self.error.emit(self.config.tr("msg_api_key_missing"))
                return

            # 构建 HTTP 请求配置
            proxies_arg = None
            if proxy_url and proxy_url.strip():
                p_url = proxy_url.strip()
                if not p_url.startswith("http"): p_url = f"http://{p_url}"
                logging.info(f"配置代理: {p_url}")
                proxies_arg = p_url

            if api_base.endswith("/chat/completions"):
                target_url = api_base
            else:
                target_url = f"{api_base.rstrip('/')}/chat/completions"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/ai-screen-translator",
                "X-Title": "AI Screen Translator"
            }

            payload = {
                "model": self.config.get("model"),
                "messages": messages,
                "stream": False
            }

            try:
                logging.info(f"发送 POST 请求至: {target_url}")
                timeout_val = self.config.get("timeout")
                try:
                    client = httpx.Client(proxy=proxies_arg, timeout=timeout_val)
                except TypeError:
                    client = httpx.Client(proxies=proxies_arg, timeout=timeout_val)

                with client:
                    response = client.post(target_url, headers=headers, json=payload)
            except Exception as e:
                logging.error(f"HTTP Error {str(e)}", exc_info=True)
                raise Exception(f"{self.config.tr('msg_net_error')}: {str(e)}")

            if response.status_code != 200:
                raise Exception(f"API Error ({response.status_code}): {response.text}")

            try:
                result_json = response.json()
                logging.info(f"API 响应: {result_json}")
                content = result_json['choices'][0]['message']['content']
                # 提取 Usage 信息
                usage = result_json.get("usage", {"prompt_tokens": 0, "completion_tokens": 0})
                self.finished.emit(content, usage)
            except Exception as e:
                logging.error("Parse Error", exc_info=True)
                raise Exception(f"Parse Error: {str(e)}")

        except Exception as e:
            logging.error("Worker Exception", exc_info=True)
            self.error.emit(f"Error: {str(e)}")


# ==========================================
# 3. UI 组件
# ==========================================
class RegionSelector(QWidget):
    region_selected = pyqtSignal(QRect)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setStyleSheet("background-color: black;")
        self.setWindowOpacity(0.3)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.start_point = None
        self.end_point = None

        total_rect = QRect()
        for screen in QApplication.screens():
            total_rect = total_rect.united(screen.geometry())

        logging.info(f"RegionSelector 覆盖区域: {total_rect}")
        self.setGeometry(total_rect)

    def paintEvent(self, event):
        if self.start_point and self.end_point:
            painter = QPainter(self)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 100))
            rect = QRect(self.start_point, self.end_point).normalized()
            painter.drawRect(rect)

    def mousePressEvent(self, event):
        self.start_point = event.pos()
        self.end_point = event.pos()
        self.update()

    def mouseMoveEvent(self, event):
        self.end_point = event.pos()
        self.update()

    def mouseReleaseEvent(self, event):
        # 计算相对于桌面的绝对坐标
        rect = QRect(self.start_point, event.pos()).normalized()
        # 将窗口内的局部坐标转换为屏幕的全局坐标
        global_top_left = self.mapToGlobal(rect.topLeft())
        global_rect = QRect(global_top_left, rect.size())

        logging.info(f"选中区域(全局): {global_rect}")
        self.region_selected.emit(global_rect)
        self.close()


# ==========================================
# 4. 高亮提示框
# ==========================================
class RegionHighlighter(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def show_effect(self, rect_list):
        x, y, w, h = rect_list
        self.setGeometry(x, y, w, h)
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = painter.pen()
        pen.setColor(QColor(0, 255, 255))
        pen.setWidth(4)
        painter.setPen(pen)
        painter.setBrush(QColor(0, 255, 255, 30))
        rect = self.rect()
        painter.drawRect(rect.adjusted(2, 2, -2, -2))


# ==========================================
# 5. 设置窗口
# ==========================================
class SettingsDialog(QDialog):
    def __init__(self, config_manager, main_app):
        super().__init__()
        self.config = config_manager
        self.main_app = main_app
        self.setWindowTitle(self.config.tr("settings_title"))
        self.resize(400, 600)
        self.init_ui()

    def init_ui(self):
        layout = QFormLayout()

        # 语言选择
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("中文 (Chinese)", "zh_CN")
        self.lang_combo.addItem("English", "en_US")
        # 设置当前选中项
        curr_lang = self.config.get("language")
        idx = self.lang_combo.findData(curr_lang)
        if idx >= 0: self.lang_combo.setCurrentIndex(idx)

        self.url_input = QLineEdit(self.config.get("api_base"))
        self.key_input = QLineEdit(self.config.get("api_key"))
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_input = QLineEdit(self.config.get("model"))
        self.timeout_input = QSpinBox()
        self.timeout_input.setValue(self.config.get("timeout"))

        self.proxy_input = QLineEdit(self.config.get("proxy"))
        self.proxy_input.setPlaceholderText(self.config.tr("proxy_placeholder"))

        # [新增] 高级模式开关
        self.advanced_mode_chk = QCheckBox(self.config.tr("chk_advanced_mode"))
        self.advanced_mode_chk.setChecked(self.config.get("advanced_mode", False))
        self.advanced_mode_chk.setToolTip(self.config.tr("tip_advanced_mode"))

        self.prompt_input = QTextEdit()
        self.prompt_input.setPlainText(self.config.get("custom_prompt"))
        self.prompt_input.setPlaceholderText(self.config.tr("prompt_placeholder"))
        self.prompt_input.setMaximumHeight(100)

        layout.addRow(self.config.tr("lbl_lang"), self.lang_combo)
        layout.addRow(self.config.tr("lbl_api_base"), self.url_input)
        layout.addRow(self.config.tr("lbl_api_key"), self.key_input)
        layout.addRow(self.config.tr("lbl_model"), self.model_input)
        layout.addRow(self.config.tr("lbl_timeout"), self.timeout_input)
        layout.addRow(self.config.tr("lbl_proxy"), self.proxy_input)
        layout.addRow("", self.advanced_mode_chk)  # 添加复选框
        layout.addRow(self.config.tr("lbl_prompt"), self.prompt_input)

        save_btn = QPushButton(self.config.tr("btn_save"))
        save_btn.clicked.connect(self.save_settings)
        layout.addRow(save_btn)
        self.setLayout(layout)

    def save_settings(self):
        old_lang = self.config.get("language")
        new_lang = self.lang_combo.currentData()

        self.config.set("language", new_lang)
        self.config.set("api_base", self.url_input.text())
        self.config.set("api_key", self.key_input.text())
        self.config.set("model", self.model_input.text())
        self.config.set("timeout", self.timeout_input.value())
        self.config.set("proxy", self.proxy_input.text())
        self.config.set("advanced_mode", self.advanced_mode_chk.isChecked())  # 保存高级模式状态
        self.config.set("custom_prompt", self.prompt_input.toPlainText())

        if old_lang != new_lang:
            QMessageBox.information(self, "Info",
                                    "Language changed. Some UI elements will update next time they are opened.")
            # 尝试刷新托盘菜单
            self.main_app.update_tray_menu()

        self.accept()


class StatisticsDialog(QDialog):
    """显示 Token 统计信息的窗口"""

    def __init__(self, config_manager, token_manager):
        super().__init__()
        self.config = config_manager
        self.tm = token_manager
        self.setWindowTitle(self.config.tr("stats_title"))
        self.resize(300, 200)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        stats = self.tm.get_stats()

        # 本次会话
        grp_session = QLabel(f"<b>{self.config.tr('stats_session')}</b>")
        layout.addWidget(grp_session)
        layout.addWidget(QLabel(f"{self.config.tr('stats_input')} {stats['session']['input']}"))
        layout.addWidget(QLabel(f"{self.config.tr('stats_output')} {stats['session']['output']}"))

        layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine))

        # 历史总计
        grp_total = QLabel(f"<b>{self.config.tr('stats_total')}</b>")
        layout.addWidget(grp_total)
        layout.addWidget(QLabel(f"{self.config.tr('stats_input')} {stats['total']['input']}"))
        layout.addWidget(QLabel(f"{self.config.tr('stats_output')} {stats['total']['output']}"))

        self.setLayout(layout)


# [重构] 结果浮窗：支持拖拽、关闭按钮、Header显示Token
class ResultOverlay(QWidget):
    def __init__(self, config_manager):
        super().__init__()
        self.config = config_manager
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 拖拽相关
        self.old_pos = None

        # 主布局：垂直
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 容器 Widget (用于设置背景色和圆角)
        self.container = QWidget()
        self.container.setStyleSheet("""
            QWidget {
                background-color: rgba(0, 0, 0, 220);
                border-radius: 8px;
                border: 1px solid #444;
            }
        """)
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(10, 5, 10, 10)

        # --- 1. Header (Token信息 + 关闭按钮) ---
        header_layout = QHBoxLayout()

        self.token_label = QLabel("")
        self.token_label.setStyleSheet("color: #aaa; font-size: 10px; background: transparent; border: none;")

        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close_overlay)
        close_btn.setStyleSheet("""
            QPushButton {
                color: white; background: transparent; border: none; font-size: 16px; font-weight: bold;
            }
            QPushButton:hover { color: #ff5555; }
        """)

        header_layout.addWidget(self.token_label)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)

        container_layout.addLayout(header_layout)

        # --- 2. 内容区域 ---
        self.content_label = QLabel("Waiting...")
        self.content_label.setWordWrap(True)
        self.content_label.setMaximumWidth(400)
        # 允许文本选择
        self.content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.content_label.setStyleSheet("color: white; font-size: 14px; background: transparent; border: none;")

        container_layout.addWidget(self.content_label)

        self.main_layout.addWidget(self.container)
        self.setLayout(self.main_layout)

    def show_content(self, text, usage_info=None):
        self.content_label.setText(text)

        # 设置 Token 信息
        if usage_info:
            pt = usage_info.get('prompt_tokens', 0)
            ct = usage_info.get('completion_tokens', 0)
            msg = self.config.tr("overlay_tokens").format(pt, ct)
            self.token_label.setText(msg)
            self.token_label.show()
        else:
            self.token_label.hide()

        self.adjustSize()

        # 位置处理
        saved_pos = self.config.get("overlay_pos")
        if saved_pos:
            self.move(saved_pos[0], saved_pos[1])
        else:
            # 默认显示在鼠标附近
            cursor_pos = QCursor.pos()
            self.move(cursor_pos.x() + 20, cursor_pos.y() + 20)

        self.show()

    def close_overlay(self):
        self.hide()

    # --- 拖拽逻辑 ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self.old_pos:
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self.old_pos = None
        # [功能] 记忆位置
        if self.isVisible():
            new_pos = self.pos()
            self.config.set("overlay_pos", [new_pos.x(), new_pos.y()])


class FloatingButton(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main_app = main_app
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(100, 100, 60, 60)

        self.btn = QPushButton("译", self)
        self.btn.setGeometry(0, 0, 50, 50)
        self.btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50; color: white; border-radius: 25px;
                font-weight: bold; font-size: 16px; border: 2px solid white;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:pressed { background-color: #3e8e41; }
        """)
        self.btn.clicked.connect(self.main_app.trigger_translation)

        # [新增] 右键菜单支持
        self.btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn.customContextMenuRequested.connect(self.on_context_menu_requested)

        self.old_pos = None

    def showEvent(self, event):
        self.raise_()
        super().showEvent(event)

    def on_context_menu_requested(self, point):
        # 按钮上的点击，需要映射到全局坐标
        global_pos = self.btn.mapToGlobal(point)
        self.show_menu(global_pos)

    def show_menu(self, global_pos):
        menu = QMenu(self)
        menu.addAction(self.main_app.config.tr("tray_region"), self.main_app.start_selection)
        menu.addAction(self.main_app.config.tr("tray_settings"), self.main_app.open_settings)
        menu.addAction(self.main_app.config.tr("tray_stats"), self.main_app.open_stats)
        menu.addSeparator()
        menu.addAction(self.main_app.config.tr("tray_exit"), self.main_app.quit_app)
        menu.exec(global_pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()
        elif event.button() == Qt.MouseButton.RightButton:
            # 边缘点击，直接使用事件的 globalPosition
            self.show_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self.old_pos:
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self.old_pos = None


# ==========================================
# 4. 主程序
# ==========================================
class MainApplication:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.config = ConfigManager()
        self.token_manager = TokenManager()

        # UI 组件
        self.result_overlay = ResultOverlay(self.config)
        self.highlighter = RegionHighlighter()

        self.init_tray()
        self.float_btn = FloatingButton(self)
        self.float_btn.show()
        self.worker = None

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self.app)
        icon_path = "./tray.icon"
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            pixmap = QPixmap(16, 16)
            pixmap.fill(Qt.GlobalColor.green)
            self.tray_icon.setIcon(QIcon(pixmap))

        self.update_tray_menu()
        self.tray_icon.show()

    def update_tray_menu(self):
        """根据当前语言刷新托盘菜单"""
        menu = QMenu()
        menu.addAction(self.config.tr("tray_region"), self.start_selection)
        menu.addAction(self.config.tr("tray_settings"), self.open_settings)
        menu.addAction(self.config.tr("tray_stats"), self.open_stats)
        menu.addSeparator()
        menu.addAction(self.config.tr("tray_exit"), self.quit_app)
        self.tray_icon.setContextMenu(menu)

    def start_selection(self):
        self.selector = RegionSelector()
        self.selector.region_selected.connect(self.on_region_selected)
        self.selector.show()

    def on_region_selected(self, rect):
        self.config.set("region", [rect.x(), rect.y(), rect.width(), rect.height()])
        self.tray_icon.showMessage("Info", self.config.tr("msg_region_updated"),
                                   QSystemTrayIcon.MessageIcon.Information, 2000)

    def open_settings(self):
        dialog = SettingsDialog(self.config, self)
        dialog.exec()

    def open_stats(self):
        dialog = StatisticsDialog(self.config, self.token_manager)
        dialog.exec()

    def trigger_translation(self):
        if self.worker is not None and self.worker.isRunning():
            self.result_overlay.show_content(self.config.tr("msg_prev_task"))
            return

        region = self.config.get("region")
        if region[2] == 0 or region[3] == 0:
            QMessageBox.warning(None, "Info", self.config.tr("msg_no_region"))
            return

        x, y, w, h = region
        image_bytes = None
        current_os = platform.system()
        logging.info(f"系统: {current_os}, 区域: {region}")

        try:
            if current_os == "Windows":
                screen = QApplication.primaryScreen()
                screenshot = screen.grabWindow(0, x, y, w, h)
                if screenshot.isNull(): raise Exception("Empty Screenshot")
                byte_array = QByteArray()
                buffer = QBuffer(byte_array)
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                screenshot.save(buffer, "PNG")
                image_bytes = bytes(byte_array.data())
            else:
                screen = QApplication.primaryScreen()
                ratio = screen.devicePixelRatio()
                real_bbox = (int(x * ratio), int(y * ratio), int((x + w) * ratio), int((y + h) * ratio))
                image = ImageGrab.grab(bbox=real_bbox, all_screens=True)
                buf = io.BytesIO()
                image.save(buf, format='PNG')
                image_bytes = buf.getvalue()

            self.highlighter.show_effect(region)
            self.result_overlay.show_content(self.config.tr("msg_processing"))

            self.worker = TranslationWorker(self.config, image_bytes)
            self.worker.finished.connect(self.on_translation_success)
            self.worker.error.connect(self.on_translation_error)
            self.worker.start()

        except Exception as e:
            logging.error("Capture Fail", exc_info=True)
            self.result_overlay.show_content(f"{self.config.tr('msg_screenshot_fail')}: {e}")

    def on_translation_success(self, text, usage):
        self.highlighter.hide()

        # 记录 Token
        if usage:
            pt = usage.get('prompt_tokens', 0)
            ct = usage.get('completion_tokens', 0)
            self.token_manager.record_usage(pt, ct)

        self.result_overlay.show_content(text, usage)

    def on_translation_error(self, msg):
        self.highlighter.hide()
        self.result_overlay.show_content(msg)

    def quit_app(self):
        self.app.quit()

    def run(self):
        sys.exit(self.app.exec())


if __name__ == "__main__":
    main = MainApplication()
    main.run()