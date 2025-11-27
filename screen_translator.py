import sys
import json
import os
import io
import logging
from logging.handlers import RotatingFileHandler
from PyQt6.QtCore import (QBuffer, QByteArray, QIODevice)
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QPushButton,
                             QLabel, QSystemTrayIcon, QMenu, QDialog, QLineEdit,
                             QFormLayout, QSpinBox, QTextEdit, QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QCursor
from PIL import Image, ImageGrab
import pytesseract
from openai import OpenAI
import platform


# ==========================================
# 0. 日志系统初始化
# ==========================================
def setup_logging():
    """配置日志系统：同时输出到文件和控制台"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # 格式：时间 - 级别 - 模块:行号 - 消息
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')

    # 1. 文件处理器 (自动轮转，最大1MB，保留3个备份)
    file_handler = RotatingFileHandler('app.log', maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 2. 控制台处理器 (方便开发调试)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


# 全局异常钩子：捕获未处理的崩溃
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.critical("Uncaught exception (程序崩溃):", exc_info=(exc_type, exc_value, exc_traceback))


# 绑定钩子
sys.excepthook = handle_exception
logger = setup_logging()


# ==========================================
# 1. 配置管理模块 (带日志)
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
            "custom_prompt": "请将以下内容翻译成中文（如果是中文则润色），直接输出结果，不要包含额外解释："
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
                logging.error(f"加载配置文件失败，将使用默认配置。错误: {e}")
                return self.default_config
        logging.info("配置文件不存在，使用默认配置")
        return self.default_config

    def save_config(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
            logging.info("配置保存成功")
        except Exception as e:
            logging.error(f"保存配置文件失败: {e}", exc_info=True)

    def get(self, key):
        return self.config.get(key, self.default_config.get(key))

    def set(self, key, value):
        self.config[key] = value
        self.save_config()


# ==========================================
# 2. 后台工作线程 (带详细错误堆栈日志)
# ==========================================
# ==========================================
# 2. 后台工作线程 (接收 Bytes 数据)
# ==========================================
class TranslationWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    # [修改点 1] 这里的第二个参数改为 image_data (bytes 类型)
    def __init__(self, config, image_data):
        super().__init__()
        self.config = config
        self.image_data = image_data  # 接收二进制数据

    def run(self):
        try:
            logging.info("开始处理翻译任务...")

            # [修改点 2] 直接从 bytes 读取，无需再操作 QPixmap
            try:
                # io.BytesIO 需要接收 bytes 类型
                pil_bytes = io.BytesIO(self.image_data)
                image = Image.open(pil_bytes)
            except Exception as e:
                logging.error("PIL 读取图像失败", exc_info=True)
                self.error.emit("图像数据损坏，无法识别")
                return

            # --- 下面是原有的 OCR 和 LLM 逻辑，保持不变 ---
            try:
                text = pytesseract.image_to_string(image, lang='chi_sim+eng')
            except Exception as e:
                logging.error("OCR 识别引擎错误", exc_info=True)
                self.error.emit(f"OCR 错误: {str(e)}")
                return

            text = text.strip()
            if not text:
                logging.warning("OCR 未识别到任何文字")
                self.error.emit("未识别到文字")
                return

            logging.info(f"OCR 识别成功: {text[:20]}...")

            # ... LLM 请求部分保持不变 ...
            api_base = self.config.get("api_base")
            api_key = self.config.get("api_key")

            if not api_key:
                self.error.emit("错误: 请配置 API Key")
                return

            client = OpenAI(base_url=api_base, api_key=api_key)
            custom_prompt = self.config.get("custom_prompt")
            full_content = f"{custom_prompt}\n\n{text}"

            response = client.chat.completions.create(
                model=self.config.get("model"),
                messages=[{"role": "user", "content": full_content}],
                timeout=self.config.get("timeout")
            )

            self.finished.emit(response.choices[0].message.content)

        except Exception as e:
            logging.error("Worker 线程发生未知异常", exc_info=True)
            self.error.emit(f"错误: {str(e)}")

# ==========================================
# 3. 屏幕区域选择工具
# ==========================================
class RegionSelector(QWidget):
    region_selected = pyqtSignal(QRect)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet("background-color: black;")
        self.setWindowOpacity(0.3)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.start_point = None
        self.end_point = None
        screen = QApplication.primaryScreen()
        self.setGeometry(screen.geometry())

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
        rect = QRect(self.start_point, event.pos()).normalized()
        self.region_selected.emit(rect)
        self.close()


# ==========================================
# [新增] 监控区域高亮提示框
# ==========================================
class RegionHighlighter(QWidget):
    def __init__(self):
        super().__init__()
        # 无边框 | 总是置顶 | 作为一个工具窗口(不在任务栏显示)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        # 背景透明
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # [关键] 鼠标穿透：让鼠标事件直接穿过这个窗口传递给下面的程序
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # 初始隐藏
        self.hide()

    def show_effect(self, rect: list):
        """显示高亮特效"""
        x, y, w, h = rect
        self.setGeometry(x, y, w, h)
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. 绘制边框 (例如：青色，宽度 3)
        pen = QPainter(self).pen()
        border_color = QColor(0, 255, 255)  # 青色
        pen.setColor(border_color)
        pen.setWidth(4)
        pen.setStyle(Qt.PenStyle.SolidLine)  # 实线
        # 稍微向内缩一点，防止边框被切掉
        painter.setPen(pen)

        # 2. 绘制半透明填充 (表示正在扫描)
        fill_color = QColor(0, 255, 255, 30)  # 青色，透明度 30/255
        painter.setBrush(fill_color)

        # 绘制矩形
        rect = self.rect()
        # 调整边框绘制位置，保证边框完全在窗口内
        painter.drawRect(rect.adjusted(2, 2, -2, -2))
# ==========================================
# 4. 设置窗口
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

        self.prompt_input = QTextEdit()
        self.prompt_input.setPlainText(self.config.get("custom_prompt"))
        self.prompt_input.setPlaceholderText("例如：请将以下内容翻译成英文...")
        self.prompt_input.setMaximumHeight(100)

        layout.addRow("API Base URL:", self.url_input)
        layout.addRow("API Key:", self.key_input)
        layout.addRow("Model Name:", self.model_input)
        layout.addRow("超时时间(秒):", self.timeout_input)
        layout.addRow("自定义提示词:", self.prompt_input)

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.save_settings)
        layout.addRow(save_btn)
        self.setLayout(layout)

    def save_settings(self):
        try:
            self.config.set("api_base", self.url_input.text())
            self.config.set("api_key", self.key_input.text())
            self.config.set("model", self.model_input.text())
            self.config.set("timeout", self.timeout_input.value())
            self.config.set("custom_prompt", self.prompt_input.toPlainText())
            logging.info("用户更新了设置")
            self.accept()
        except Exception as e:
            logging.error("保存设置时界面出错", exc_info=True)


# ==========================================
# 5. 结果浮窗
# ==========================================
class ResultOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout()
        self.text_label = QLabel("等待翻译...")
        self.text_label.setStyleSheet("""
            background-color: rgba(0, 0, 0, 200);
            color: white;
            padding: 10px;
            border-radius: 5px;
            font-size: 14px;
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
# 6. 浮窗按钮
# ==========================================
class FloatingButton(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main_app = main_app
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
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
# 7. 主程序
# ==========================================
class MainApplication:
    def __init__(self):
        logging.info("程序启动初始化...")
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
        try:
            self.tray_icon = QSystemTrayIcon(self.app)

            # [修改点 3] 加载自定义图标
            icon_path = "tray.icon"  # 假设文件在当前运行目录下
            if os.path.exists(icon_path):
                # 加载本地图标
                icon = QIcon(icon_path)
                self.tray_icon.setIcon(icon)
                self.app.setWindowIcon(icon)  # 同时设置应用程序图标
                logging.info(f"已加载图标: {icon_path}")
            else:
                # 找不到文件时，回退到默认的绿色方块，避免程序无图标
                logging.warning(f"未找到 {icon_path}，使用默认图标")
                pixmap = QPixmap(16, 16)
                pixmap.fill(Qt.GlobalColor.green)
                self.tray_icon.setIcon(QIcon(pixmap))

            menu = QMenu()
            action_select = QAction("设置监控区域", self.app)
            action_select.triggered.connect(self.start_selection)
            menu.addAction(action_select)

            action_settings = QAction("设置参数", self.app)
            action_settings.triggered.connect(self.open_settings)
            menu.addAction(action_settings)

            menu.addSeparator()

            action_quit = QAction("退出", self.app)
            action_quit.triggered.connect(self.quit_app)
            menu.addAction(action_quit)

            self.tray_icon.setContextMenu(menu)
            self.tray_icon.show()
        except Exception as e:
            logging.critical("系统托盘初始化失败", exc_info=True)

    def start_selection(self):
        logging.info("用户开始选择区域")
        self.selector = RegionSelector()
        self.selector.region_selected.connect(self.on_region_selected)
        self.selector.show()

    def on_region_selected(self, rect):
        logging.info(f"区域已更新: {rect}")
        self.config.set("region", [rect.x(), rect.y(), rect.width(), rect.height()])
        self.tray_icon.showMessage("提示", "监控区域已更新", QSystemTrayIcon.MessageIcon.Information, 2000)

    def open_settings(self):
        logging.info("打开设置窗口")
        dialog = SettingsDialog(self.config)
        dialog.exec()

    def trigger_translation(self):
        try:
            # 1. 线程防抖检查
            if self.worker is not None and self.worker.isRunning():
                logging.warning("任务正在运行中")
                self.result_overlay.show_text("任务处理中，请稍候...", QCursor.pos())
                return

            logging.info("触发翻译")

            # 2. 获取坐标区域
            region = self.config.get("region")  # x, y, w, h
            if region[2] == 0 or region[3] == 0:
                QMessageBox.warning(None, "提示", "请先设置监控区域")
                return

            x, y, w, h = region
            image_bytes = None
            current_os = platform.system()  # 获取操作系统名称: "Windows", "Darwin" (Mac), "Linux"

            # ==========================================
            # 分支 A: Windows 平台 -> 使用 Qt 原生截图
            # ==========================================
            if current_os == "Windows":
                logging.info("检测到 Windows 系统，使用 Qt grabWindow 进行截图")
                screen = QApplication.primaryScreen()
                # Windows 下 grabWindow 通常能很好地处理逻辑坐标
                screenshot = screen.grabWindow(0, x, y, w, h)

                if screenshot.isNull():
                    raise Exception("Windows 截图失败: 返回空图像")

                # 转换为 bytes (深拷贝以保证线程安全)
                byte_array = QByteArray()
                buffer = QBuffer(byte_array)
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                screenshot.save(buffer, "PNG")
                image_bytes = bytes(byte_array.data())

            # ==========================================
            # 分支 B: macOS / 其他 平台 -> 使用 Pillow ImageGrab
            # ==========================================
            else:
                logging.info(f"检测到 {current_os} 系统，使用 Pillow ImageGrab 进行截图")

                # 处理 Retina/高分屏缩放
                screen = QApplication.primaryScreen()
                pixel_ratio = screen.devicePixelRatio()

                # 将 Qt 的逻辑坐标转换为屏幕的物理坐标
                real_x = int(x * pixel_ratio)
                real_y = int(y * pixel_ratio)
                real_w = int(w * pixel_ratio)
                real_h = int(h * pixel_ratio)

                bbox = (real_x, real_y, real_x + real_w, real_y + real_h)

                try:
                    # all_screens=True 尝试支持多屏
                    image = ImageGrab.grab(bbox=bbox, all_screens=True)
                except Exception as e:
                    logging.error("Pillow 截图失败", exc_info=True)
                    self.result_overlay.show_text("截图失败: 请检查屏幕录制权限", QCursor.pos())
                    return

                # 转换为 bytes
                img_buffer = io.BytesIO()
                image.save(img_buffer, format='PNG')
                image_bytes = img_buffer.getvalue()

            # 3. [新增] 截图完成，立即显示高亮框提示用户“正在处理”
            #self.highlighter.show_effect(region)

            # 4. 启动线程
            self.result_overlay.show_text("识别中...", QCursor.pos())
            self.worker = TranslationWorker(self.config, image_bytes)
            self.worker.finished.connect(self.on_translation_success)
            self.worker.error.connect(self.on_translation_error)
            self.worker.start()

        except Exception as e:
            logging.error("触发翻译流程严重错误", exc_info=True)
            self.result_overlay.show_text(f"错误: {str(e)}", QCursor.pos())

    def on_translation_success(self, text):
        logging.info("翻译任务完成，显示结果")
        self.result_overlay.show_text(text, QCursor.pos())

    def on_translation_error(self, error_msg):
        # 已经在 worker 中 log 过了，这里只需要显示
        self.result_overlay.show_text(error_msg, QCursor.pos())

    def quit_app(self):
        logging.info("程序正常退出")
        self.app.quit()

    def run(self):
        sys.exit(self.app.exec())


if __name__ == "__main__":
    main = MainApplication()
    main.run()