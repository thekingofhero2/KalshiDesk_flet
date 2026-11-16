# -*- coding: utf-8 -*-
"""KalshiDesk —— 程序入口。

开发运行：python main.py  （桌面模式）
Web 运行：flet run --web main.py
"""
from kalshi_desk.app import main

if __name__ in {"__main__", "__mp_main__"}:
    main()
