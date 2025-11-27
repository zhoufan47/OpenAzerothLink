# 🖥️ Open Azeroth Link

## 中文介绍

Open Azeroth Link 是一款基于 Python 开发的桌面屏幕翻译工具。它允许用户选择屏幕上的任意区域，通过 OCR（光学字符识别）提取文字，并调用 OpenAI 兼容的 LLM（大语言模型）进行翻译、润色或解释。

项目旨在提供一个轻量级、针对《魔兽世界》的屏幕翻译工具，支持 Windows 和 macOS 系统。

✨ 主要特性

区域监控：类似截图工具的操作，自由框选屏幕上需要翻译的区域。

AI 驱动：支持接入 OpenAI (GPT-3.5/4) 或任何兼容 OpenAI 接口的模型（如 DeepSeek, Moonshot, Local LLMs）。

自定义 Prompt：不仅限于翻译！你可以修改提示词让它变成代码解释器、文本摘要工具或润色工具。预置的 Prompt 适用于《魔兽世界：巫妖王之怒》的场景。

悬浮窗设计：

常驻按钮：一个可拖拽的悬浮按钮，随时点击触发识别。

结果浮窗：翻译结果直接悬浮在鼠标附近，点击即可关闭。

视觉反馈：识别过程中，目标区域会高亮显示，提供清晰的交互体验。

跨平台支持：同时支持 Windows 和 macOS 系统，macOS下未经过严格测试，可能存在崩溃风险。

系统托盘：支持最小化至托盘，右键菜单进行设置或退出。

🛠️ 安装指南

1.从Release 中下载最新压缩包，解压后运行OpenAzerothLink.exe
2.从系统托盘中寻找图标，右键设置相关参数
3.点击“译”按钮，对监控区域进行翻译，翻译完成后，将有浮窗展示翻译后内容

📜 开源协议

本项目遵循 GNU General Public License v3.0 (GPLv3) 协议。

========================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================================

## English Introduction
Open Azeroth Link is a Python-based desktop screen translation tool. It allows users to select any area on the screen, extract text via OCR (Optical Character Recognition), and invoke OpenAI-compatible LLMs (Large Language Models) for translation, polishing, or explanation.

The project aims to provide a lightweight screen translation tool tailored for World of Warcraft, supporting both Windows and macOS systems.

✨ Key Features

Region Monitoring: Features screenshot-like operation, allowing you to freely select the screen area that needs translation.

AI Driven: Supports integration with OpenAI (GPT-3.5/4) or any model compatible with the OpenAI interface (such as DeepSeek, Moonshot, Local LLMs).

Custom Prompts: Not limited to translation! You can modify the prompts to turn it into a code interpreter, text summarizer, or text polishing tool. The pre-set prompts are optimized for World of Warcraft: Wrath of the Lich King scenarios.

Floating Window Design:

Persistent Button: A draggable floating button that can be clicked at any time to trigger recognition.

Result Popup: Translation results float directly near the mouse cursor and can be closed with a click.

Visual Feedback: During the recognition process, the target area is highlighted to provide a clear interactive experience.

Cross-Platform Support: Supports both Windows and macOS systems. (Note: macOS support has not undergone strict testing and may carry a risk of crashing.)

System Tray: Supports minimizing to the system tray; right-click the menu to access settings or exit.

🛠️ Installation Guide

Download the latest archive from Releases, unzip it, and run OpenAzerothLink.exe.

Locate the icon in the system tray and right-click to configure the relevant parameters.

Click the "Translate" (译) button to translate the monitored area. Once complete, a floating window will display the translated content.

📜 License

This project is licensed under the GNU General Public License v3.0 (GPLv3).