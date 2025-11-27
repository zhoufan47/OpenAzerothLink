import sys
import json
import os
import io
import logging
import platform
import traceback
import httpx  # [保留] 用于发送 HTTP 请求
from logging.handlers import RotatingFileHandler

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QPushButton,
                             QLabel, QSystemTrayIcon, QMenu, QDialog, QLineEdit,
                             QFormLayout, QSpinBox, QTextEdit, QMessageBox)
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QRect, QPoint, QSize,
                          QBuffer, QByteArray, QIODevice)
from PyQt6.QtGui import (QIcon, QAction, QPixmap, QPainter, QColor, QCursor,
                         QGuiApplication)
from PIL import Image, ImageGrab
import pytesseract


# [移除] from openai import OpenAI (不再依赖 openai 库)

# ==========================================
# 0. 日志系统初始化
# ==========================================
def setup_logging():
    """配置日志系统：同时输出到文件和控制台"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')

    file_handler = RotatingFileHandler('app.log', maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
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


# ==========================================
# 1. 配置管理模块
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
            "proxy": ""  # 代理配置
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
            logging.error(f"保存配置文件失败: {e}")

    def get(self, key):
        return self.config.get(key, self.default_config.get(key))

    def set(self, key, value):
        self.config[key] = value
        self.save_config()


# ==========================================
# 2. 后台工作线程 (使用 httpx 原生请求)
# ==========================================
class TranslationWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config, image_data):
        super().__init__()
        self.config = config
        self.image_data = image_data

    def run(self):
        try:
            logging.info("Worker: 开始处理任务")
            try:
                pil_bytes = io.BytesIO(self.image_data)
                image = Image.open(pil_bytes)
            except Exception as e:
                logging.error("PIL 读取图像失败", exc_info=True)
                self.error.emit("图像数据损坏")
                return

            try:
                # 提示: 确保安装了 tesseract 并在 PATH 中，或在此处指定 cmd 路径
                # pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
                text = pytesseract.image_to_string(image, lang='chi_sim+eng')
            except Exception as e:
                logging.error("OCR 引擎错误", exc_info=True)
                self.error.emit(f"OCR 错误: {str(e)}")
                return

            text = text.strip()
            if not text:
                self.error.emit("未识别到文字")
                return

            logging.info(f"OCR 成功，字符数: {len(text)}")

            api_base = self.config.get("api_base").strip()
            api_key = self.config.get("api_key").strip()
            proxy_url = self.config.get("proxy").strip()

            if not api_key:
                self.error.emit("错误: 未配置 API Key")
                return

            # --- [核心修改] 构建原生 HTTP 请求 ---

            # 1. 准备代理参数
            proxies_arg = None
            if proxy_url and proxy_url.strip():
                p_url = proxy_url.strip()
                if not p_url.startswith("http"):
                    p_url = f"http://{p_url}"
                proxies_arg = p_url
                logging.info(f"配置代理: {p_url}")
                # 环境变量兜底
                os.environ["HTTP_PROXY"] = p_url
                os.environ["HTTPS_PROXY"] = p_url

            # 2. 拼接 URL 和 Headers
            # 兼容处理：如果 base 已经包含了 /chat/completions 则不拼接，否则自动拼接
            if api_base.endswith("/chat/completions"):
                target_url = api_base
            else:
                target_url = f"{api_base.rstrip('/')}/chat/completions"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # OpenRouter 需要的额外 Headers
                "HTTP-Referer": "https://github.com/ai-screen-translator",
                "X-Title": "AI Screen Translator"
            }

            # 3. 构建 Payload
            custom_prompt = self.config.get("custom_prompt")
            full_content = f"{custom_prompt}\n\n{text}"

            payload = {
                "model": self.config.get("model"),
                "messages": [{"role": "user", "content": full_content}],
                "stream": False
            }

            timeout_val = self.config.get("timeout")
            logging.info(f"发送 POST 请求至: {target_url}")

            # 4. 发送请求 (兼容 httpx 版本差异)
            response = None
            try:
                # 尝试新版参数名 proxy
                with httpx.Client(proxy=proxies_arg, timeout=timeout_val) as client:
                    response = client.post(target_url, headers=headers, json=payload)
            except TypeError:
                logging.info("httpx 不支持 proxy 参数，尝试使用 proxies")
                # 回退旧版参数名 proxies
                with httpx.Client(proxies=proxies_arg, timeout=timeout_val) as client:
                    response = client.post(target_url, headers=headers, json=payload)
            except Exception as e:
                raise Exception(f"网络连接失败: {str(e)}")

            # 5. 检查状态码
            if response.status_code != 200:
                logging.error(f"API Error: {response.status_code} - {response.text}")
                try:
                    err_json = response.json()
                    err_msg = err_json.get('error', {}).get('message', response.text)
                except:
                    err_msg = response.text
                raise Exception(f"API 返回错误 ({response.status_code}): {err_msg}")

            # 6. 解析结果
            try:
                result_json = response.json()
                logging.info(f"API 响应: {result_json}")
                result = result_json['choices'][0]['message']['content']
                self.finished.emit(result)
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                logging.error(f"解析响应失败: {response.text}", exc_info=True)
                raise Exception(f"无法解析 API 响应: {str(e)}")

        except Exception as e:
            logging.error("Worker 异常", exc_info=True)
            self.error.emit(f"错误: {str(e)}")


# ==========================================
# 3. [修复版] 屏幕区域选择工具 (支持多屏/Win11)
# ==========================================
class RegionSelector(QWidget):
    region_selected = pyqtSignal(QRect)

    def __init__(self):
        super().__init__()
        # 使用 Tool 属性有时在 Win11 能更好地处理层级
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setStyleSheet("background-color: black;")
        self.setWindowOpacity(0.3)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self.start_point = None
        self.end_point = None

        # [Win11 修复关键] 获取所有屏幕的组合几何形状
        # 这确保了无论你在主屏还是副屏，遮罩都能覆盖
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
    def __init__(self, config_manager):
        super().__init__()
        self.setWindowTitle("软件设置")
        self.config = config_manager
        self.resize(400, 500)
        self.init_ui()

    def init_ui(self):
        layout = QFormLayout()
        self.url_input = QLineEdit(self.config.get("api_base"))
        self.key_input = QLineEdit(self.config.get("api_key"))
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_input = QLineEdit(self.config.get("model"))
        self.timeout_input = QSpinBox()
        self.timeout_input.setValue(self.config.get("timeout"))

        # [新增] 代理输入框
        self.proxy_input = QLineEdit(self.config.get("proxy"))
        self.proxy_input.setPlaceholderText("例如: http://127.0.0.1:7890 (留空则不使用)")

        self.prompt_input = QTextEdit()
        self.prompt_input.setPlainText(self.config.get("custom_prompt"))
        self.prompt_input.setPlaceholderText("例如：请翻译...")
        self.prompt_input.setMaximumHeight(100)

        layout.addRow("API Base URL:", self.url_input)
        layout.addRow("API Key:", self.key_input)
        layout.addRow("Model Name:", self.model_input)
        layout.addRow("超时(秒):", self.timeout_input)
        layout.addRow("代理地址 (Proxy):", self.proxy_input)  # [新增]
        layout.addRow("Prompt:", self.prompt_input)

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.save_settings)
        layout.addRow(save_btn)
        self.setLayout(layout)

    def save_settings(self):
        self.config.set("api_base", self.url_input.text())
        self.config.set("api_key", self.key_input.text())
        self.config.set("model", self.model_input.text())
        self.config.set("timeout", self.timeout_input.value())
        self.config.set("proxy", self.proxy_input.text())  # [新增]
        self.config.set("custom_prompt", self.prompt_input.toPlainText())
        self.accept()


# ==========================================
# 6. 结果浮窗
# ==========================================
class ResultOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QVBoxLayout()
        self.text_label = QLabel("等待...")
        self.text_label.setStyleSheet("""
            background-color: rgba(0, 0, 0, 200); color: white;
            padding: 10px; border-radius: 5px; font-size: 14px;
        """)
        self.text_label.setWordWrap(True)
        self.text_label.setMaximumWidth(400)
        layout.addWidget(self.text_label)
        self.setLayout(layout)
        self.text_label.mousePressEvent = lambda e: self.hide()

    def show_text(self, text, pos: QPoint):
        self.text_label.setText(text)
        self.adjustSize()
        self.move(pos.x() + 20, pos.y() + 20)
        self.show()


# ==========================================
# 7. 浮窗按钮
# ==========================================
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
        self.old_pos = None

    def showEvent(self, event):
        self.raise_()
        super().showEvent(event)

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


# ==========================================
# 8. 主程序
# ==========================================
class MainApplication:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.config = ConfigManager()
        self.init_tray()
        self.result_overlay = ResultOverlay()
        self.highlighter = RegionHighlighter()
        self.float_btn = FloatingButton(self)
        self.float_btn.show()
        self.worker = None

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self.app)
        icon_path = "tray.icon"
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
            self.tray_icon.setIcon(icon)
            self.app.setWindowIcon(icon)
        else:
            pixmap = QPixmap(16, 16)
            pixmap.fill(Qt.GlobalColor.green)
            self.tray_icon.setIcon(QIcon(pixmap))

        menu = QMenu()
        menu.addAction("设置监控区域", self.start_selection)
        menu.addAction("设置参数", self.open_settings)
        menu.addSeparator()
        menu.addAction("退出", self.quit_app)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()

    def start_selection(self):
        logging.info("启动区域选择器")
        self.selector = RegionSelector()
        self.selector.region_selected.connect(self.on_region_selected)
        self.selector.show()

    def on_region_selected(self, rect):
        logging.info(f"保存区域: {rect}")
        self.config.set("region", [rect.x(), rect.y(), rect.width(), rect.height()])
        self.tray_icon.showMessage("提示", "监控区域已更新", QSystemTrayIcon.MessageIcon.Information, 2000)

    def open_settings(self):
        dialog = SettingsDialog(self.config)
        dialog.exec()

    def trigger_translation(self):
        if self.worker is not None and self.worker.isRunning():
            self.result_overlay.show_text("上个任务进行中...", QCursor.pos())
            return

        region = self.config.get("region")
        if region[2] == 0 or region[3] == 0:
            QMessageBox.warning(None, "提示", "请先设置监控区域")
            return

        x, y, w, h = region
        image_bytes = None
        current_os = platform.system()
        logging.info(f"系统: {current_os}, 区域: {region}")

        try:
            # 策略：Windows 使用 Qt 原生截图 (性能好，支持缩放)
            # macOS/Linux 使用 Pillow ImageGrab (兼容性好)
            if current_os == "Windows":
                screen = QApplication.primaryScreen()
                # Windows 11 上的 grabWindow 有时需要正确指定屏幕，这里默认用 primaryScreen
                screenshot = screen.grabWindow(0, x, y, w, h)
                if screenshot.isNull():
                    raise Exception("截图为空")

                byte_array = QByteArray()
                buffer = QBuffer(byte_array)
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                screenshot.save(buffer, "PNG")
                image_bytes = bytes(byte_array.data())
            else:
                # macOS / Linux
                screen = QApplication.primaryScreen()
                ratio = screen.devicePixelRatio()
                real_bbox = (int(x * ratio), int(y * ratio), int((x + w) * ratio), int((y + h) * ratio))
                image = ImageGrab.grab(bbox=real_bbox, all_screens=True)

                buf = io.BytesIO()
                image.save(buf, format='PNG')
                image_bytes = buf.getvalue()

            self.highlighter.show_effect(region)
            self.result_overlay.show_text("识别中...", QCursor.pos())

            self.worker = TranslationWorker(self.config, image_bytes)
            self.worker.finished.connect(self.on_translation_success)
            self.worker.error.connect(self.on_translation_error)
            self.worker.start()

        except Exception as e:
            logging.error("截图失败", exc_info=True)
            self.result_overlay.show_text(f"截图失败: {e}", QCursor.pos())

    def on_translation_success(self, text):
        self.highlighter.hide()
        self.result_overlay.show_text(text, QCursor.pos())

    def on_translation_error(self, msg):
        self.highlighter.hide()
        self.result_overlay.show_text(msg, QCursor.pos())

    def quit_app(self):
        self.app.quit()

    def run(self):
        sys.exit(self.app.exec())


if __name__ == "__main__":
    main = MainApplication()
    main.run()