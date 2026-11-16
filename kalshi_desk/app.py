# -*- coding: utf-8 -*-
"""Kalshi 账户管理系统（Flet 版）。

启动：python -m kalshi_desk
桌面：python main.py  或  flet run main.py
Web：flet run --web main.py
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import flet as ft

from . import ai as ai_client
from . import db, store
from .api import ACTION_LABEL, ENV_LABEL, ENDPOINTS, DeskAPI

# ========== 常量 ==========
TAB_POS, TAB_ORD, TAB_FILL, TAB_SUB, TAB_SET = (
    "持仓", "订单", "成交", "子账户 / 分片", "设置")

PAGE_OVER, PAGE_TRADE, PAGE_ANALY, PAGE_AI, PAGE_SET, PAGE_CHAT = (
    "概览", "交易", "分析", "AI", "系统设置", "聊天室")
PAGES = (PAGE_OVER, PAGE_TRADE, PAGE_ANALY, PAGE_AI, PAGE_SET, PAGE_CHAT)
PAGE_ICONS = {
    PAGE_OVER: ft.Icons.INSIGHTS,
    PAGE_TRADE: ft.Icons.CANDLESTICK_CHART,
    PAGE_ANALY: ft.Icons.QUERY_STATS,
    PAGE_AI: ft.Icons.SMART_TOY,
    PAGE_SET: ft.Icons.SETTINGS,
    PAGE_CHAT: ft.Icons.FORUM,
}
PAGE_META = {
    PAGE_OVER: ("ACCOUNT", "账户概览", "余额、敞口与子账户分片，读取本地最新快照"),
    PAGE_TRADE: ("EXECUTION", "交易", "持仓、订单、成交与子账户；手动下单按分类与周期筛选"),
    PAGE_ANALY: ("ANALYTICS", "分析", "近 30 天成交与已结算盈亏，统计自本地 DuckDB"),
    PAGE_AI: ("INTELLIGENCE", "AI 分析", "基于本地库数据提问，模型与密钥在系统设置中配置"),
    PAGE_SET: ("CONTROL", "系统设置", "同步、连接、代理、AI 模型与用户权限"),
    PAGE_CHAT: ("COMMS", "聊天室", "同一环境下的用户共享这段讨论"),
}

FREQ_ORDER = ["fifteen_min", "hourly", "daily", "weekly", "monthly",
              "quarterly", "annual", "one_off", "custom", "often", "irregular"]
FREQ_CN = {
    "fifteen_min": "15 分钟", "hourly": "每小时", "daily": "每日",
    "weekly": "每周", "monthly": "每月", "quarterly": "每季",
    "annual": "每年", "one_off": "一次性", "one_time": "一次性",
    "custom": "不定期", "often": "高频", "irregular": "不定期",
    "adhoc": "不定期",
}
CATEGORY_CN = {
    "Crypto": "加密货币", "Sports": "体育", "Politics": "政治",
    "Elections": "选举", "Economics": "经济", "Financials": "金融",
    "Companies": "公司", "Entertainment": "娱乐", "Culture": "文化",
    "World": "国际", "Climate and Weather": "气候天气", "Health": "健康",
    "Science and Technology": "科技", "Transportation": "交通",
    "Commodities": "大宗商品", "Mentions": "提及次数", "Social": "社会",
    "AI": "人工智能", "Business": "商业", "Education": "教育",
    "Exotics": "另类", "other": "其他", "Other": "其他",
}

# ========== 主题色 ==========
COLORS = {
    "bg": "#07080b", "panel": "#0c0e13", "line": "#181c24",
    "line_strong": "#242a36", "text": "#e8ebf1", "muted": "#79808f",
    "accent": "#35e0a1", "neg": "#ff6b7a",
}


# ========== 工具函数 ==========
def border_all(width: float = 1, color: str = COLORS["line"]) -> ft.Border:
    """四边同宽的边框（Flet 1.x 已移除 ft.border.all）。"""
    side = ft.BorderSide(width, color)
    return ft.Border(top=side, right=side, bottom=side, left=side)


def spacer() -> ft.Control:
    """弹性占位（Flet 1.x 已移除 ft.Spacer）。"""
    return ft.Container(expand=True)


def usd(x: Optional[float], signed: bool = False) -> str:
    if x is None:
        return "—"
    body = f"${abs(x):,.2f}"
    if x < 0:
        return f"-{body}"
    return f"+{body}" if signed and x > 0 else body


def fmt_ts(v) -> str:
    if v is None or v == "":
        return "—"
    try:
        if isinstance(v, (int, float)):
            t = float(v)
            if t > 2e10:
                t /= 1000.0
            return datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S")
        if hasattr(v, "strftime"):
            return v.strftime("%m-%d %H:%M:%S")
        s = str(v).replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone().strftime("%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(v)[:19]


def run_in_thread(coro_fn, *args, **kwargs):
    """在后台线程运行一个同步或异步 callable（带参数），返回 Future。"""
    import concurrent.futures
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def _run():
        fn = coro_fn
        if asyncio.iscoroutinefunction(fn):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(fn(*args, **kwargs))
            finally:
                loop.close()
        return fn(*args, **kwargs)

    return pool.submit(_run)


# ============================================================================
# DeskopUI 主类
# ============================================================================
class DeskUI:
    def __init__(self, page: ft.Page):
        self.page = page
        self.authed = False
        self.current_user: Optional[dict] = None
        self.page_name = PAGE_OVER
        self.user = ""
        self.api: Optional[DeskAPI] = None
        self.sess: Optional[dict] = None
        self.auto = True
        self.busy = False
        self.cooldown = 0.0
        self._trade_opening = False
        # CF Benchmarks 状态
        self._cf_index_ids: set = set()
        self._cf_last_discovery: float = 0.0
        self._cf_fail_until: float = 0.0
        self._cf_last_error: str = ""
        self.cfb_latest: dict = {}
        self.ticks = 0
        self.status_filter: Optional[str] = "resting"
        self.orders_ticker = ""
        self.fills_ticker = ""
        self.fills_days: Optional[int] = 3
        self._orders_busy = False
        self._fills_busy = False
        self._pem_text: Optional[str] = None
        self.proxy_cfg: dict = store.load_config()["proxy"]
        self.refresh_interval: int = store.load_config()["refresh_interval"]
        self.timer_task: Optional[asyncio.Task] = None
        self.chat_timer_task: Optional[asyncio.Task] = None
        self._closed = False
        self.state = {"balance": {}, "positions": [], "orders": [], "fills": [], "subs": []}
        self.pos_by_ticker: dict = {}
        self.order_by_id: dict = {}
        self._ai_active = 0

        # UI 容器（rebuild 时清空）
        self.root: ft.Column = ft.Column()
        self.body: ft.Column = ft.Column()

    # ================= 页面骨架 =================
    async def start(self) -> None:
        """首次进入页面时调用：配置主题、数据库、主布局。"""
        p = self.page
        p.on_disconnect = self._on_page_disconnect
        p.on_close = self._on_page_disconnect
        p.title = "KalshiDesk"
        p.theme_mode = ft.ThemeMode.DARK
        p.bgcolor = COLORS["bg"]
        p.padding = ft.padding.Padding(left=28, top=16, right=28, bottom=16)
        p.window.width = 1480
        p.window.min_width = 1100
        p.window.height = 820
        p.theme = ft.Theme(
            color_scheme_seed=COLORS["accent"],
            font_family="Inter",
            use_material3=True,
        )
        p.dark_theme = ft.Theme(
            color_scheme=ft.ColorScheme(
                primary=COLORS["accent"],
                on_primary="#07080b",
                secondary=COLORS["accent"],
                surface=COLORS["panel"],
                error=COLORS["neg"],
            ),
            scaffold_bgcolor=COLORS["bg"],
            card_theme=ft.CardTheme(color=COLORS["panel"]),
            dialog_theme=ft.DialogTheme(
                bgcolor=COLORS["panel"],
                shape=ft.RoundedRectangleBorder(radius=12),
            ),
        )

        # 初始化数据库
        try:
            db.init()
        except Exception as ex:
            self.notify(f"本地数据库无法打开：{ex}", type="negative")

        # 主根容器
        self.root = ft.Column(expand=True, spacing=0)
        self.page.add(self.root)

        self.rebuild()
        self._start_timers()

    def _on_page_disconnect(self, e=None) -> None:
        """浏览器关闭/刷新时取消所有后台任务，避免向已断开的 WebSocket 推送。"""
        self._closed = True
        for task in (self.timer_task, self.chat_timer_task):
            if task and not task.done():
                task.cancel()

    def _start_timers(self) -> None:
        """启动自动刷新定时器与聊天室刷新定时器。"""
        async def auto_tick():
            await asyncio.sleep(self.refresh_interval)
            while not self._closed:
                if self.api and self.auto and not self.busy and self.authed:
                    if time.time() >= self.cooldown:
                        try:
                            await self.refresh_all()
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass
                await asyncio.sleep(self.refresh_interval)

        self.timer_task = asyncio.create_task(auto_tick())

    def rebuild(self) -> None:
        """重建主内容区（清空 root 重新构建）。"""
        self.root.controls.clear()
        self.root.update()

        if not self.authed:
            self.build_auth_login()
        elif self.api is None and self.is_admin:
            self.build_login()
        else:
            self.build_main()
        self.root.update()

    def _new_api(self, key_id: str, pem_path: str, env: str) -> DeskAPI:
        return DeskAPI(key_id, pem_path, env, proxy=store.proxy_url(self.proxy_cfg))

    @property
    def is_admin(self) -> bool:
        return bool(self.current_user and self.current_user.get("role") == "admin")

    @property
    def role_label(self) -> str:
        return "管理员" if self.is_admin else "只读"

    def allowed_pages(self) -> list:
        return list(PAGES) if self.is_admin else [PAGE_OVER, PAGE_CHAT]

    # ================= 通知与确认对话框 =================
    def notify(self, message: str, type: str = "info") -> None:
        """Flet SnackBar 通知。"""
        if self._closed:
            return
        color_map = {
            "positive": ft.Colors.GREEN,
            "negative": COLORS["neg"],
            "warning": ft.Colors.ORANGE,
            "info": ft.Colors.BLUE_GREY,
        }
        snack = ft.SnackBar(
            content=ft.Text(message, color=COLORS["text"], size=12),
            bgcolor=color_map.get(type, ft.Colors.BLUE_GREY_700),
            behavior=ft.SnackBarBehavior.FLOATING,
            margin=ft.margin.Margin(16),
            padding=ft.padding.Padding(left=16, top=10, right=16, bottom=10),
            shape=ft.RoundedRectangleBorder(radius=8),
            duration=2500,
        )
        try:
            self.page.show_dialog(snack)
            self.page.update()
        except Exception:
            pass

    async def confirm(self, text: str, title: str = "请确认") -> bool:
        """确认对话框（异步）。"""
        result = {"value": False}
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(title, weight=ft.FontWeight.W_600),
            content=ft.Text(text, size=13),
            actions_alignment=ft.MainAxisAlignment.END,
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_dialog(dialog, False, result)),
                ft.FilledButton("确认", style=ft.ButtonStyle(color=COLORS["neg"], bgcolor=COLORS["neg"]),
                                on_click=lambda e: self._close_dialog(dialog, True, result)),
            ],
        )
        self.page.show_dialog(dialog)
        self.page.update()
        # 等待用户点击
        deadline = time.time() + 60
        while time.time() < deadline:
            await asyncio.sleep(0.1)
            if not dialog.open:
                break
        try:
            self.page.pop_dialog()
        except Exception:
            pass
        return result["value"]

    def _close_dialog(self, dialog, val, result):
        result["value"] = val
        dialog.open = False
        try:
            dialog.update()
        except Exception:
            pass

    async def prompt_text(self, title: str) -> str:
        """输入对话框。"""
        result = {"value": ""}
        input_ref = ft.Ref[ft.TextField]()
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(title, weight=ft.FontWeight.W_600),
            content=ft.TextField(ref=input_ref, label="新值", dense=True),
            actions_alignment=ft.MainAxisAlignment.END,
            actions=[
                ft.TextButton("取消", on_click=lambda e: self._close_prompt(dialog, "", result)),
                ft.FilledButton("确定", on_click=lambda e: self._close_prompt(dialog, (input_ref.current.value or "").strip(), result)),
            ],
        )
        self.page.show_dialog(dialog)
        self.page.update()
        deadline = time.time() + 60
        while time.time() < deadline:
            await asyncio.sleep(0.1)
            if not dialog.open:
                break
        try:
            self.page.pop_dialog()
        except Exception:
            pass
        return result["value"]

    def _close_prompt(self, dialog, val, result):
        result["value"] = val
        dialog.open = False
        try:
            dialog.update()
        except Exception:
            pass

    # ================= 登录 =================
    def build_auth_login(self) -> None:
        """工作台账户登录（admin/admin）。"""
        u_field = ft.Ref[ft.TextField]()
        p_field = ft.Ref[ft.TextField]()

        def do_login(e):
            username = (u_field.current.value or "").strip()
            user = store.check_login(username, p_field.current.value or "")
            if not user:
                self.notify("用户名或密码错误", type="negative")
                return
            self.authed, self.user, self.current_user = True, username, user
            self.page_name = PAGE_OVER
            self._pem_text = None
            self.notify(f"欢迎，{username}（{self.role_label}）", type="positive")
            self.page.run_task(self.auto_connect)

        self.root.controls.append(
            ft.Container(
                content=ft.Column([
                    ft.Text("KALSHI DESK", weight=ft.FontWeight.W_700, size=12,
                            color=COLORS["text"], style=ft.TextStyle(letter_spacing=0.26)),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text("登录工作台", size=24, weight=ft.FontWeight.W_600),
                    ft.Text("本地账户，凭据只保存在本机 data/config.json。",
                            size=12, color=COLORS["muted"]),
                    ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                    ft.TextField(ref=u_field, label="用户名", dense=True, autofocus=True),
                    ft.TextField(ref=p_field, label="密码", password=True,
                                 can_reveal_password=True, dense=True),
                    ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
                    ft.FilledButton("登录", on_click=do_login,
                                    style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                                    height=42),
                    ft.Text("默认账户 admin / admin，可在「系统设置 → 用户管理」中新增或改密。",
                            size=11, color=COLORS["muted"]),
                ], spacing=12, width=360, horizontal_alignment=ft.CrossAxisAlignment.START),
                alignment=ft.alignment.Alignment(0.5, 0.5),
                width=360,
                padding=32,
                border=ft.BorderSide(1, COLORS["line_strong"]),
                border_radius=12,
                bgcolor=COLORS["panel"],
                shadow=ft.BoxShadow(blur_radius=40, spread_radius=0,
                                    color=ft.Colors.with_opacity(0.5, "black")),
            )
        )
        # 让整个页面居中
        self.root.alignment = ft.MainAxisAlignment.CENTER
        self.root.expand = True
        self.root.update()

    async def auto_connect(self) -> None:
        sess = store.load_session()
        if not sess:
            self.rebuild()
            return
        try:
            api = self._new_api(sess["key_id"], sess["pem_path"], sess["env"])
            loop = asyncio.get_running_loop()
            bal = await loop.run_in_executor(None, api.balance)
            await loop.run_in_executor(None, db.save_balance, sess["env"], sess["key_id"], bal)
            self.api, self.sess = api, sess
        except Exception as ex:
            self.login_error = f"自动连接 Kalshi 失败：{ex}"
        self.rebuild()
        if self.api:
            self.page.run_task(self.load_cached)

    def build_login(self) -> None:
        """连接 Kalshi 账户（上传 PEM + Key ID）。"""
        p_field = ft.Ref[ft.TextField]()
        key_field = ft.Ref[ft.TextField]()
        env_drop = ft.Ref[ft.Dropdown]()
        pem_name_lbl = ft.Ref[ft.Text]()
        diag_lbl = ft.Ref[ft.Text]()

        file_picker = ft.FilePicker()
        self.page.services.append(file_picker)

        def connect_clicked(e):
            env_val = env_drop.current.value or "prod"
            self.page.run_task(self._connect, (key_field.current.value or "").strip(), env_val)

        def diagnose_clicked(e):
            self.page.run_task(self.diagnose_key,
                               (key_field.current.value or "").strip(), diag_lbl)

        async def _do_pick_pem():
            # Web 模式下 FilePicker 不可用（无 listener），走粘贴后备
            if self.page.web:
                await self._paste_pem_dialog(name_lbl_ref=pem_name_lbl)
                return
            try:
                files = await file_picker.pick_files(allow_multiple=False,
                                                      allowed_extensions=["pem", "key", "txt"])
            except Exception:
                await self._paste_pem_dialog(name_lbl_ref=pem_name_lbl)
                return
            if files:
                from types import SimpleNamespace
                self.on_pem_picked(SimpleNamespace(files=files), pem_name_lbl)

        def upload_pem(e):
            self.page.run_task(_do_pick_pem)

        self.root.alignment = ft.MainAxisAlignment.CENTER
        self.root.expand = True

        card = ft.Container(
            content=ft.Column([
                ft.Text("KALSHI DESK", weight=ft.FontWeight.W_700, size=12,
                        style=ft.TextStyle(letter_spacing=0.26)),
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                ft.Text("连接 Kalshi 账户", size=24, weight=ft.FontWeight.W_600),
                ft.Text("上传 API 私钥后可同步余额、持仓、订单与成交，并直接下单。",
                        size=12, color=COLORS["muted"]),
                ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                ft.Button("上传 PEM 私钥", icon=ft.Icons.UPLOAD_FILE,
                                  on_click=upload_pem, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
                ft.Text(ref=pem_name_lbl, value="未选择文件", size=11, color=COLORS["muted"]),
                ft.TextField(ref=key_field, label="API Key ID", dense=True),
                ft.Dropdown(ref=env_drop, label="环境", dense=True,
                            options=[ft.dropdown.Option("prod", "生产环境"),
                                     ft.dropdown.Option("demo", "模拟环境")],
                            value=(getattr(self, "login_env", None) or
                                   (self.sess or {}).get("env", "prod"))),
                ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
                ft.Row([
                    ft.FilledButton("连接账户", icon=ft.Icons.LOGIN, expand=True,
                                    on_click=connect_clicked,
                                    style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b")),
                    ft.OutlinedButton("诊断 Key", icon=ft.Icons.TROUBLESHOOT,
                                      on_click=diagnose_clicked),
                ], spacing=8),
                ft.Text(ref=diag_lbl, value="", size=11, color=COLORS["muted"], selectable=True),
                ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
                self._build_proxy_box(),
                ft.Text(f"私钥仅保存在本机 {store.DATA_DIR}，不会发送到任何第三方服务。",
                        size=11, color=COLORS["muted"]),
            ], spacing=10, width=440, horizontal_alignment=ft.CrossAxisAlignment.START,
               scroll=ft.ScrollMode.AUTO),
            alignment=ft.alignment.Alignment(0.5, 0.5),
            width=480,
            height=680,
            padding=24,
            border=ft.BorderSide(1, COLORS["line_strong"]),
            border_radius=12,
            bgcolor=COLORS["panel"],
            shadow=ft.BoxShadow(blur_radius=40, spread_radius=0,
                                color=ft.Colors.with_opacity(0.5, "black")),
        )
        self.root.controls.append(card)
        self.root.update()

    async def _paste_pem_dialog(self, title: str = "粘贴 PEM 私钥",
                                 name_lbl_ref=None) -> None:
        """Web 模式后备：弹对话框让用户直接粘贴 PEM 文本。"""
        import base64
        import re as _re

        typed: dict = {"value": ""}
        text_field = ft.TextField(
            multiline=True, min_lines=8, max_lines=16,
            hint_text="粘贴完整 PEM 内容，例如：\n-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
            on_change=lambda e: typed.update(value=e.control.value or ""),
        )

        def _close() -> None:
            try:
                self.page.pop_dialog()
            except Exception:
                pass
            try:
                self.page.update()
            except Exception:
                pass

        def on_confirm(e):
            try:
                raw = (typed["value"] or text_field.value or "").strip()
                if not raw:
                    self.notify("PEM 内容为空", type="negative")
                    return
                if "BEGIN" in raw:
                    m = _re.search(r"-----BEGIN [^-]+-----([\s\S]*?)-----END [^-]+-----", raw)
                    body = _re.sub(r"\s+", "", m.group(1)) if m else raw
                else:
                    body = _re.sub(r"\s+", "", raw)
                base64.b64decode(body, validate=True)

                self._pem_text = raw
                if name_lbl_ref is not None and getattr(name_lbl_ref, "current", None):
                    name_lbl_ref.current.value = "已粘贴 PEM 私钥"
                _close()
                self.notify("私钥已载入（粘贴）", type="positive")
            except Exception as ex:
                self.notify(f"载入失败：{ex}", type="negative")

        def on_cancel(e):
            _close()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(title, size=14, weight=ft.FontWeight.W_600),
            content=ft.Column([text_field], tight=True, width=560),
            actions=[
                ft.TextButton("取消", on_click=on_cancel),
                ft.FilledButton("确认载入", on_click=on_confirm,
                                style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b")),
            ],
        )
        self.page.show_dialog(dlg)
        self.page.update()

    def on_pem_picked(self, e: ft.FilePickerResultEvent, name_lbl_ref) -> None:
        if not e.files:
            return

        def _load():
            try:
                path = e.files[0].path
                text = store.normalize_pem(Path(path).read_bytes())
                return text, e.files[0].name
            except Exception as ex:
                return None, str(ex)

        loop = asyncio.get_running_loop() if hasattr(asyncio, "get_running_loop") else None

        def done(fut):
            text, name_or_err = fut.result()
            if text is None:
                self.notify(f"PEM 解析失败：{name_or_err}", type="negative")
                return
            self._pem_text = text
            name_lbl_ref.current.value = f"已选择：{name_or_err}"
            self.page.update()
            self.notify("私钥已载入", type="positive")

        import concurrent.futures
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(_load)
        fut.add_done_callback(done)

    async def diagnose_key(self, key_id: str, diag_lbl_ref) -> None:
        if not self._pem_text:
            self.notify("请先上传 .pem 私钥文件", type="warning")
            return
        if not key_id:
            self.notify("请填写 API Key ID", type="warning")
            return
        diag_lbl_ref.current.value = "诊断中…"
        self.page.update()
        try:
            staged = store.stage_pem(self._pem_text)
        except Exception as ex:
            diag_lbl_ref.current.value = f"私钥不可用：{ex}"
            self.page.update()
            return
        loop = asyncio.get_running_loop()
        lines = []
        try:
            for env in ("prod", "demo"):
                try:
                    api = self._new_api(key_id, str(staged), env)
                    bal = await loop.run_in_executor(None, api.balance)
                    lines.append(f"{ENV_LABEL[env]}：认证通过，可用余额 {usd(bal.get('available'))}")
                except Exception as ex:
                    msg = str(ex)
                    if " 401" in msg or "401:" in msg:
                        msg = "认证失败（401，Key 不属于该环境或与私钥不匹配）"
                    lines.append(f"{ENV_LABEL[env]}：{msg[:160]}")
        finally:
            try:
                staged.unlink()
            except OSError:
                pass
        diag_lbl_ref.current.value = "\n".join(lines)
        self.page.update()

    async def _connect(self, key_id: str, env: str) -> None:
        key_id = (key_id or "").strip()
        if not self._pem_text:
            self.notify("请先上传 .pem 私钥文件", type="warning")
            return
        if not key_id:
            self.notify("请填写 API Key ID", type="warning")
            return
        staged = None
        loop = asyncio.get_running_loop()
        try:
            staged = store.stage_pem(self._pem_text)
            api = self._new_api(key_id, str(staged), env)
            bal = await loop.run_in_executor(None, api.balance)
            await loop.run_in_executor(None, db.save_balance, env, key_id, bal)
            sess = store.save_session(key_id, self._pem_text, env)
            api = self._new_api(key_id, sess["pem_path"], env)
        except Exception as ex:
            self.notify(f"连接失败：{ex}", type="negative")
            return
        finally:
            if staged is not None:
                try:
                    staged.unlink()
                except OSError:
                    pass
        self.api, self.sess = api, sess
        self.login_error = ""
        self._pem_text = None
        self.notify("已连接 Kalshi 账户", type="positive")
        self.rebuild()
        if self.api:
            self.page.run_task(self.load_cached)

    # ================= 代理配置 =================
    def _build_proxy_box(self) -> ft.Column:
        """返回代理配置的 Column（登录页与设置页共用）。"""
        p = self.proxy_cfg
        self.px_enabled = ft.Switch(label="启用 SOCKS5 代理", value=bool(p.get("enabled")))
        self.px_host = ft.TextField(label="代理地址", value=p.get("host") or "127.0.0.1", dense=True, width=180)
        self.px_port = ft.TextField(label="端口", value=str(p.get("port") or 1080), dense=True, width=100)
        self.px_user = ft.TextField(label="用户名（可空）", value=p.get("username") or "", dense=True, width=160)
        self.px_pass = ft.TextField(label="密码（可空）", value=p.get("password") or "", dense=True,
                                    password=True, can_reveal_password=True, width=160)
        self.px_status = ft.Text(self._proxy_status_text(p), size=11, color=COLORS["muted"])
        return ft.Column([
            ft.Text("网络代理（SOCKS5）", weight=ft.FontWeight.W_600, size=13, color=COLORS["text"]),
            ft.Text("启用后，访问 Kalshi 接口全部经该代理（socks5h，域名由代理端解析）。",
                    size=11, color=COLORS["muted"]),
            self.px_enabled,
            ft.Row([self.px_host, self.px_port], spacing=8),
            ft.Row([self.px_user, self.px_pass], spacing=8),
            self.px_status,
        ], spacing=6)

    @staticmethod
    def _proxy_status_text(p: dict) -> str:
        url = store.proxy_url(p)
        return f"当前生效：{url}" if url else "当前：直连（未启用代理）"

    # ================= 主界面 =================
    def build_main(self) -> None:
        env = self.api.env if self.api else (self.sess or {}).get("env", "local")
        topbar = ft.Container(
            content=ft.Row([
                ft.Text("KALSHI DESK", weight=ft.FontWeight.W_700, size=12,
                        style=ft.TextStyle(letter_spacing=0.26)),
                ft.Container(
                    content=ft.Text(ENV_LABEL.get(env, env), size=11,
                                    color="#ffffff" if env == "prod" else COLORS["text"]),
                    bgcolor=COLORS["neg"] if env == "prod" else "#445066",
                    padding=ft.padding.Padding(left=10, top=2, right=10, bottom=2),
                    border_radius=4,
                ),
                ft.TextButton(
                    content=ft.Row([
                        ft.Icon(ft.Icons.SWAP_HORIZ, size=16),
                        ft.Text(f"切到{'模拟' if env == 'prod' else '生产'}", size=11),
                    ], spacing=4),
                    style=ft.ButtonStyle(color=COLORS["accent"]),
                    tooltip="切换环境",
                    visible=bool(self.is_admin and self.api),
                    on_click=lambda e: self.page.run_task(self.switch_env),
                ),
                ft.Text(store.mask_key(self.sess["key_id"]), size=10,
                        color=COLORS["muted"], font_family="JetBrains Mono"),
                spacer(),
                ft.Switch(label="自动刷新", value=self.auto,
                          on_change=lambda e: setattr(self, "auto", bool(e.control.value))),
                interval_label := ft.Text(f"每 {self.refresh_interval} 秒", size=11, color=COLORS["muted"]),
                ft.IconButton(icon=ft.Icons.REFRESH, tooltip="立即刷新",
                              on_click=lambda e: self.page.run_task(self.refresh_all, True)),
                updated_label := ft.Text("本地快照", size=11, color=COLORS["muted"]),
                ft.IconButton(icon=ft.Icons.LOGOUT, tooltip=f"退出（{self.user} · {self.role_label}）",
                              on_click=lambda e: self.page.run_task(self.do_logout)),
            ], spacing=12),
            padding=ft.padding.Padding(top=12, bottom=12),
            border=ft.Border(bottom=ft.BorderSide(1, COLORS["line"])),
        )

        self.interval_label = interval_label
        self.updated_label = updated_label
        body_row = ft.Row(
            expand=True, spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                self._build_sidebar(),
                body := ft.Column(expand=True, spacing=20, scroll=ft.ScrollMode.AUTO),
            ])
        self.body = body

        self.root.controls.extend([topbar, body_row])
        self.show_page(self.page_name)

        self.root.update()
        if self.api:
            self.page.run_task(self.load_cached)

    def _build_sidebar(self) -> ft.Container:
        """左侧导航栏。"""
        self.nav_buttons: dict = {}
        nav_col = ft.Column(spacing=4)
        for name in self.allowed_pages():
            item = self._nav_item(name, name == self.page_name)
            self.nav_buttons[name] = item
            nav_col.controls.append(item)

        nav_col.controls.append(spacer())
        nav_col.controls.append(ft.Text(f"{self.user} · {self.role_label}", size=11,
                                       color=COLORS["muted"]))

        return ft.Container(
            content=nav_col,
            width=200,
            padding=ft.padding.Padding(left=12, top=8, right=12, bottom=8),
            border=ft.Border(right=ft.BorderSide(1, COLORS["line"])),
        )

    def _nav_item(self, name: str, active: bool) -> ft.Container:
        """单个导航项（用 Container 实现，避免 TextButton 默认样式干扰）。"""
        color = COLORS["accent"] if active else COLORS["muted"]
        return ft.Container(
            content=ft.Row([
                ft.Icon(PAGE_ICONS[name], size=18, color=color),
                ft.Text(name, size=12.5, color=color),
            ], spacing=10),
            padding=ft.padding.Padding(left=10, top=8, right=10, bottom=8),
            border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.08, COLORS["accent"]) if active else None,
            ink=True,
            on_click=lambda e, n=name: self.show_page(n),
        )

    def _reset_page_refs(self) -> None:
        for attr in ("c_avail", "c_avail_ts", "c_expo", "c_pnl", "c_rest", "c_subs",
                     "shard_table", "sub_mini", "pos_table", "order_table",
                     "fill_table", "sub_table", "tr_table", "ai_answer",
                     "chat_box", "chat_preview", "an_metrics", "an_table",
                     "cf_src_label", "cf_row", "trade_data_table"):
            setattr(self, attr, None)

    def show_page(self, name: str) -> None:
        if name not in self.allowed_pages():
            name = PAGE_OVER
        self.page_name = name
        # 更新导航选中
        for btn_name, item in getattr(self, "nav_buttons", {}).items():
            active = btn_name == name
            color = COLORS["accent"] if active else COLORS["muted"]
            item.bgcolor = ft.Colors.with_opacity(0.08, COLORS["accent"]) if active else None
            item.content.controls[0].color = color
            item.content.controls[1].color = color
            try:
                item.update()
            except Exception:
                pass

        self._reset_page_refs()
        self.body.controls.clear()

        eyebrow, title, desc = PAGE_META[name]
        self.body.controls.extend([
            ft.Column([
                ft.Text(eyebrow, size=10, style=ft.TextStyle(letter_spacing=0.24),
                        color=COLORS["accent"], weight=ft.FontWeight.W_500),
                ft.Text(title, size=21, weight=ft.FontWeight.W_600),
                ft.Text(desc, size=12, color=COLORS["muted"]),
            ], spacing=4),
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
        ])

        if name == PAGE_OVER:
            self._build_overview()
        elif name == PAGE_TRADE:
            self._build_trade()
        elif name == PAGE_ANALY:
            self._build_analytics()
        elif name == PAGE_AI:
            self._build_ai()
        elif name == PAGE_SET:
            self._build_settings()
        elif name == PAGE_CHAT:
            self._build_chat()

        try:
            self.body.update()
        except Exception:
            pass

    def _metric_label(self, title: str) -> tuple:
        """指标卡：返回 (label_value, label_sub) —— 后续 render 时 .value=xxx。"""
        val_lbl = ft.Text("—", size=25, weight=ft.FontWeight.W_600,
                          font_family="JetBrains Mono")
        sub_lbl = ft.Text("", size=10.5, color=COLORS["muted"],
                          font_family="JetBrains Mono")
        col = ft.Column([
            ft.Text(title, size=10, style=ft.TextStyle(letter_spacing=0.16),
                    color=COLORS["muted"], weight=ft.FontWeight.W_500,
                    font_family="JetBrains Mono"),
            val_lbl,
            sub_lbl,
        ], spacing=4, expand=True)
        return col, val_lbl, sub_lbl

    # ================= 概览页 =================
    def _build_overview(self) -> None:
        metric_row = ft.Row(expand=True, spacing=0)

        col_avail, self.c_avail, self.c_avail_ts = self._metric_label("可用余额")
        col_expo, self.c_expo, _ = self._metric_label("持仓敞口")
        col_pnl, self.c_pnl, _ = self._metric_label("已实现盈亏")
        col_rest, self.c_rest, _ = self._metric_label("在挂订单")
        col_subs, self.c_subs, _ = self._metric_label("子账户数")

        for i, col in enumerate([col_avail, col_expo, col_pnl, col_rest, col_subs]):
            col_container = ft.Container(content=col, padding=ft.padding.Padding(20), expand=True)
            if i > 0:
                col_container.border = ft.Border(left=ft.BorderSide(1, COLORS["line"]))
            metric_row.controls.append(col_container)

        shard_table = self.shard_table = self._make_data_table(
            ["shard", "余额", "占比"], ["shard", "balance", "pct"])
        sub_mini = self.sub_mini = self._make_data_table(
            ["shard", "子账户", "余额", "更新时间"],
            ["shard", "sub", "balance", "updated"])

        cf_row = self.cf_row = ft.Row(spacing=12, wrap=True)

        self.body.controls.extend([
            ft.Container(content=metric_row,
                         border=ft.Border(top=ft.BorderSide(1, COLORS["line"]),
                                          bottom=ft.BorderSide(1, COLORS["line"]))),
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            ft.Column([
                ft.Row([
                    ft.Text("CF BENCHMARKS", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                            color=COLORS["muted"], weight=ft.FontWeight.W_500,
                            font_family="JetBrains Mono"),
                    cf_src_label := ft.Text("数据源：Kalshi 转发", size=11, color=COLORS["muted"]),
                    spacer(),
                    ft.IconButton(icon=ft.Icons.REFRESH, tooltip="立即刷新 CF 价格",
                                  on_click=lambda e: self.page.run_task(self.refresh_cf)),
                ], spacing=8),
                cf_row,
            ], spacing=8),
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            ft.Row([
                ft.Column([
                    ft.Text("分片余额分布", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                            color=COLORS["muted"], weight=ft.FontWeight.W_500,
                            font_family="JetBrains Mono"),
                    shard_table,
                ], spacing=8, expand=True),
                ft.Column([
                    ft.Text("子账户余额", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                            color=COLORS["muted"], weight=ft.FontWeight.W_500,
                            font_family="JetBrains Mono"),
                    sub_mini,
                ], spacing=8, expand=True),
            ], spacing=24),
        ])
        self.cf_src_label = cf_src_label
        self.render_balance()
        self.render_subs()

    # ================= 工具：构建 DataTable =================
    def _make_data_table(self, headers: list[str], keys: list[str],
                         selectable: bool = False) -> ft.DataTable:
        """返回一个可动态更新的 DataTable。通过 data_table.rows = [...] 赋值。"""
        def _header(label):
            return ft.DataColumn(
                ft.Text(label, size=10, style=ft.TextStyle(letter_spacing=0.12), color=COLORS["muted"],
                        font_family="JetBrains Mono", weight=ft.FontWeight.W_600))

        columns = [_header(h) for h in headers]
        dt = ft.DataTable(
            columns=columns,
            rows=[],
            border=border_all(1, COLORS["line"]),
            border_radius=8,
            vertical_lines=ft.BorderSide(1, COLORS["line"]),
            horizontal_lines=ft.BorderSide(1, COLORS["line"]),
            heading_row_height=36,
            data_row_min_height=32,
            data_text_style=ft.TextStyle(size=12, font_family="JetBrains Mono"),
            sort_column_index=0,
            sort_ascending=True,
        )
        dt._keys = keys  # type: ignore
        return dt

    def _set_table_rows(self, dt: ft.DataTable, rows: list[dict]) -> None:
        """把 dict 列表灌进 DataTable 的 rows。"""
        def _cell(v, align="left"):
            txt = ft.Text(str(v) if v is not None else "—", size=12,
                          font_family="JetBrains Mono",
                          color=COLORS["accent"] if isinstance(v, str) and v.startswith("+")
                          else (COLORS["neg"] if isinstance(v, str) and v.startswith("-")
                                else COLORS["text"]))
            return ft.DataCell(ft.Container(content=txt,
                                            alignment=ft.alignment.Alignment(1.0, 0.5) if align == "right"
                                            else ft.alignment.Alignment(0.0, 0.5)))

        keys = getattr(dt, "_keys", [])
        new_rows = []
        for r in rows:
            cells = []
            for k in keys:
                v = r.get(k, "—")
                # 自动判断对齐
                if k in ("balance", "pct", "exposure", "pnl", "qty", "count", "filled",
                         "remaining", "price", "volume", "fees", "contracts", "amount",
                         "balance"):
                    cells.append(_cell(v, "right"))
                else:
                    cells.append(_cell(v, "left"))
            new_rows.append(ft.DataRow(cells=cells))
        dt.rows = new_rows
        try:
            dt.update()
        except Exception:
            pass

    # ================= 交易页 =================
    def _build_trade(self) -> None:
        tab_labels = [TAB_POS, TAB_ORD, TAB_FILL, TAB_SUB]
        tabs = ft.Tabs(
            selected_index=0,
            length=4,
            content=ft.Column([
                ft.TabBar(
                    tabs=[
                        ft.Tab(label=TAB_POS, icon=ft.Icons.ACCOUNT_BALANCE_WALLET),
                        ft.Tab(label=TAB_ORD, icon=ft.Icons.RECEIPT_LONG),
                        ft.Tab(label=TAB_FILL, icon=ft.Icons.HISTORY),
                        ft.Tab(label=TAB_SUB, icon=ft.Icons.CALL_SPLIT),
                    ],
                    tab_alignment=ft.TabAlignment.START,
                    divider_color=COLORS["line"],
                    label_color=COLORS["accent"],
                    unselected_label_color=COLORS["muted"],
                    indicator_color=COLORS["accent"],
                ),
                ft.TabBarView(
                    expand=True,
                    controls=[
                        self._build_positions_tab(),
                        self._build_orders_tab(),
                        self._build_fills_tab(),
                        self._build_subs_tab(),
                    ],
                ),
            ], expand=True),
            on_change=lambda e: self.page.run_task(
                self._on_trade_tab_change,
                tab_labels[min(max(int(getattr(e.control, "selected_index", 0) or 0), 0), 3)]),
        )
        self.body.controls.append(tabs)

    async def _on_trade_tab_change(self, tab_name: str) -> None:
        if not self.api:
            return
        if tab_name == TAB_ORD:
            await self.refresh_orders()
        elif tab_name == TAB_FILL:
            await self.refresh_fills()
        elif tab_name == TAB_SUB:
            await self.refresh_transfers()

    def _build_positions_tab(self) -> ft.Column:
        header = ft.Row([
            ft.FilledButton("手动交易", icon=ft.Icons.ADD,
                            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                            on_click=lambda e: self.page.run_task(self.open_trade)),
            ft.Text("选中任意持仓行 → 查看实时行情并直接平仓 / 买入 / 卖出",
                    size=11, color=COLORS["muted"]),
        ], spacing=12)
        self.pos_table = self._make_data_table(
            ["市场", "方向", "数量", "敞口", "已实现盈亏", "在挂", "shard", "操作"],
            ["ticker", "side", "qty", "exposure", "pnl", "resting", "shard", "act"])
        return ft.Column([header, self.pos_table], spacing=12)

    def _build_orders_tab(self) -> ft.Column:
        self.status_toggle = ft.Dropdown(
            label="状态", dense=True, width=110,
            options=[ft.dropdown.Option("all", "全部"),
                     ft.dropdown.Option("resting", "在挂"),
                     ft.dropdown.Option("executed", "已成交"),
                     ft.dropdown.Option("canceled", "已撤")],
            value="resting",
            on_select=lambda e: self.page.run_task(self._on_status_change))
        self.ord_ticker_input = ft.TextField(label="ticker 过滤（留空=全部）",
                                             dense=True, width=200)
        self.order_table = self._make_data_table(
            ["时间", "市场", "方向", "价格", "数量", "成交", "剩余", "状态", "shard"],
            ["time", "ticker", "side", "price", "count", "filled", "remaining", "status", "shard"])
        header = ft.Row([
            self.status_toggle,
            self.ord_ticker_input,
            ft.FilledButton("查询", icon=ft.Icons.SEARCH,
                            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                            on_click=lambda e: self.page.run_task(self._on_order_query)),
            ft.Text("状态筛选毫秒级查本地库 · 下单/撤单后立即刷新", size=11, color=COLORS["muted"]),
        ], spacing=12)
        return ft.Column([header, self.order_table], spacing=12)

    def _build_fills_tab(self) -> ft.Column:
        self.fill_ticker_input = ft.TextField(label="ticker 过滤", dense=True, width=200)
        self.fill_days_select = ft.Dropdown(
            label="时间范围", dense=True, width=110,
            options=[ft.dropdown.Option("3", "近 3 天"),
                     ft.dropdown.Option("7", "近 7 天"),
                     ft.dropdown.Option("30", "近 30 天"),
                     ft.dropdown.Option("0", "不限")],
            value="3")
        self.fill_table = self._make_data_table(
            ["时间", "市场", "动作", "方向", "成交价", "数量", "角色", "订单"],
            ["time", "ticker", "action", "side", "price", "count", "role", "order"])
        header = ft.Row([
            self.fill_ticker_input,
            self.fill_days_select,
            ft.FilledButton("查询", icon=ft.Icons.SEARCH,
                            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                            on_click=lambda e: self.page.run_task(self._on_fill_query)),
        ], spacing=12)
        return ft.Column([header, self.fill_table], spacing=12)

    def _build_subs_tab(self) -> ft.Column:
        self.sub_table = self._make_data_table(
            ["shard", "子账户", "余额", "更新时间"],
            ["shard", "sub", "balance", "updated"])
        self.tr_table = self._make_data_table(
            ["时间", "金额", "从", "到"],
            ["time", "amount", "from", "to"])

        # 子账户转账表单
        self.t_from = ft.TextField(label="从子账户", dense=True, width=100, value="0")
        self.t_to = ft.TextField(label="到子账户", dense=True, width=100, value="1")
        self.t_shard = ft.TextField(label="shard（可空）", dense=True, width=110, value="0")
        self.t_amt = ft.TextField(label="金额（美元）", dense=True, width=140, value="10.0")

        # 跨 shard 转账
        self.x_from = ft.TextField(label="源 shard", dense=True, width=100, value="0")
        self.x_to = ft.TextField(label="目标 shard", dense=True, width=100, value="2")
        self.x_amt = ft.TextField(label="金额（美元）", dense=True, width=140, value="10.0")
        self.x_src = ft.Dropdown(label="源类型", dense=True, width=140,
                                 options=[ft.dropdown.Option("event_contract"),
                                          ft.dropdown.Option("margined")],
                                 value="event_contract")
        self.x_dst = ft.Dropdown(label="目标类型", dense=True, width=140,
                                 options=[ft.dropdown.Option("event_contract"),
                                          ft.dropdown.Option("margined")],
                                 value="event_contract")

        self.new_sub_shard = ft.TextField(label="shard（可空）", dense=True, width=140)

        top_row = ft.Row([
            ft.Text("子账户余额（含各 shard）", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                    color=COLORS["muted"], weight=ft.FontWeight.W_500,
                    font_family="JetBrains Mono"),
        ], spacing=8)

        transfer_form = ft.Column([
            ft.Text("同 shard 子账户转账", weight=ft.FontWeight.W_500, size=13),
            ft.Row([self.t_from, self.t_to, self.t_shard, self.t_amt], spacing=8),
            ft.FilledButton("提交转账", icon=ft.Icons.SWAP_HORIZ,
                            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                            on_click=lambda e: self.page.run_task(self.do_transfer_sub)),
        ], spacing=8)

        cross_shard_form = ft.Column([
            ft.Text("跨 shard 转账", weight=ft.FontWeight.W_500, size=13),
            ft.Row([self.x_from, self.x_to, self.x_amt], spacing=8),
            ft.Row([self.x_src, self.x_dst], spacing=8),
            ft.FilledButton("提交跨 shard 转账", icon=ft.Icons.SYNC_ALT,
                            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                            on_click=lambda e: self.page.run_task(self.do_transfer_shard)),
            ft.Text("金额按 centicents（1 美元 = 10000）提交。", size=11, color=COLORS["muted"]),
        ], spacing=8)

        create_row = ft.Row([
            ft.Text("创建子账户 / 转账记录", weight=ft.FontWeight.W_500, size=13),
            self.new_sub_shard,
            ft.FilledButton("创建", icon=ft.Icons.ADD,
                            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                            on_click=lambda e: self.page.run_task(self.do_create_subaccount)),
            spacer(),
            ft.OutlinedButton("刷新转账记录", icon=ft.Icons.REFRESH,
                              on_click=lambda e: self.page.run_task(self.refresh_transfers)),
        ], spacing=8)

        return ft.Column([
            top_row,
            self.sub_table,
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            ft.Row([transfer_form, cross_shard_form], spacing=24),
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            create_row,
            self.tr_table,
        ], spacing=12)

    # ================= 分析页 =================
    def _build_analytics(self) -> None:
        metric_row = ft.Row(spacing=0)
        self.an_fills = ft.Text("—", size=25, weight=ft.FontWeight.W_600, font_family="JetBrains Mono")
        self.an_fills_sub = ft.Text("", size=10.5, color=COLORS["muted"], font_family="JetBrains Mono")
        self.an_vol = ft.Text("—", size=25, weight=ft.FontWeight.W_600, font_family="JetBrains Mono")
        self.an_fees = ft.Text("—", size=25, weight=ft.FontWeight.W_600, font_family="JetBrains Mono")
        self.an_net = ft.Text("—", size=25, weight=ft.FontWeight.W_600, font_family="JetBrains Mono")
        self.an_pos = ft.Text("—", size=25, weight=ft.FontWeight.W_600, font_family="JetBrains Mono")

        def _metric(label, val_lbl, sub_lbl=None):
            col = ft.Column([
                ft.Text(label, size=10, style=ft.TextStyle(letter_spacing=0.16),
                        color=COLORS["muted"], weight=ft.FontWeight.W_500,
                        font_family="JetBrains Mono"),
                val_lbl,
            ], spacing=4, expand=True)
            if sub_lbl is not None:
                col.controls.append(sub_lbl)
            return ft.Container(content=col, padding=ft.padding.Padding(20), expand=True)

        metric_row.controls.extend([
            _metric("近 30 天成交笔数", self.an_fills, self.an_fills_sub),
            _metric("近 30 天成交额", self.an_vol),
            _metric("近 30 天手续费", self.an_fees),
            _metric("已结算净盈亏", self.an_net),
            _metric("当前持仓数", self.an_pos),
        ])
        # 边框
        for i in range(len(metric_row.controls)):
            if i > 0:
                metric_row.controls[i].border = ft.Border(left=ft.BorderSide(1, COLORS["line"]))

        self.an_table = self._make_data_table(
            ["市场", "成交笔数", "合约数", "成交额"],
            ["ticker", "fills", "contracts", "volume"])

        self.body.controls.extend([
            ft.Row([
                ft.Text("统计口径 · 近 30 天", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                        color=COLORS["muted"], weight=ft.FontWeight.W_500,
                        font_family="JetBrains Mono"),
                spacer(),
                ft.OutlinedButton("重新统计", icon=ft.Icons.REFRESH,
                                  on_click=lambda e: self.page.run_task(self.load_analytics)),
            ]),
            ft.Container(content=metric_row,
                         border=ft.Border(top=ft.BorderSide(1, COLORS["line"]),
                                          bottom=ft.BorderSide(1, COLORS["line"]))),
            ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
            ft.Text("成交额 Top 市场", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                    color=COLORS["muted"], weight=ft.FontWeight.W_500,
                    font_family="JetBrains Mono"),
            self.an_table,
        ])
        self.page.run_task(self.load_analytics)

    # ================= AI 页 =================
    def _build_ai(self) -> None:
        ai_cfg = store.load_config()["ai"]
        providers = ai_cfg["providers"]
        self._ai_active = ai_cfg["active"]

        self.ai_active_drop = ft.Dropdown(
            label="模型", dense=True, width=320,
            options=[ft.dropdown.Option(str(i), f"{p['name']} · {p.get('model') or '未命名模型'}")
                     for i, p in enumerate(providers)],
            value=str(self._ai_active),
        )
        self.ai_question = ft.TextField(
            label="问题", multiline=True, min_lines=2, max_lines=4,
            hint_text="例如：近 30 天我的成交集中在哪些市场？已结算部分的盈亏如何？",
        )
        self.ai_answer = ft.Markdown("", selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)
        self.ai_answer_wrap = ft.Container(content=self.ai_answer,
                                           bgcolor=COLORS["panel"],
                                           padding=16,
                                           border_radius=8,
                                           border=ft.BorderSide(1, COLORS["line"]),
                                           width=float("inf"))

        self.body.controls.extend([
            ft.Row([
                ft.Text("选择模型", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                        color=COLORS["muted"], weight=ft.FontWeight.W_500,
                        font_family="JetBrains Mono"),
                spacer(),
                self.ai_active_drop,
            ]),
            ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
            self.ai_question,
            ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
            ft.Row([
                ft.FilledButton("提问", icon=ft.Icons.SEND,
                                style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                                on_click=lambda e: self.page.run_task(self.do_ai_ask)),
                ft.Text("仅使用本地 DuckDB 中的余额、持仓、成交、订单与结算数据。",
                        size=11, color=COLORS["muted"]),
            ]),
            ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
            self.ai_answer_wrap,
            ft.Text("提示：在「系统设置 → AI 模型」中配置服务商与 API Key。",
                    size=11, color=COLORS["muted"]),
        ])

    # ================= 设置页 =================
    def _build_settings(self) -> None:
        tab = getattr(self, "settings_tab", "系统设置")
        settings_labels = ["系统设置", "用户管理"]
        tabs = ft.Tabs(
            selected_index=0 if tab == "系统设置" else 1,
            length=2,
            content=ft.Column([
                ft.TabBar(
                    tabs=[
                        ft.Tab(label="系统设置", icon=ft.Icons.SETTINGS),
                        ft.Tab(label="用户管理", icon=ft.Icons.GROUP),
                    ],
                    tab_alignment=ft.TabAlignment.START,
                    label_color=COLORS["accent"],
                    unselected_label_color=COLORS["muted"],
                    indicator_color=COLORS["accent"],
                    divider_color=COLORS["line"],
                ),
                ft.TabBarView(
                    expand=True,
                    controls=[
                        self._build_system_settings(),
                        self._build_user_management(),
                    ],
                ),
            ], expand=True),
            on_change=lambda e: setattr(
                self, "settings_tab",
                settings_labels[min(max(int(getattr(e.control, "selected_index", 0) or 0), 0), 1)]),
        )
        self.body.controls.append(tabs)

    def _build_system_settings(self) -> ft.Column:
        # 同步设置
        self.ri_input = ft.TextField(label="自动刷新间隔（秒）", dense=True, width=120,
                                      value=str(self.refresh_interval))
        # 当前连接
        env_txt = ENV_LABEL[self.api.env] if self.api else "local"
        url_txt = self.api.base_url if self.api else "—"
        key_mask = store.mask_key((self.sess or {}).get("key_id", ""))
        pem_path = (self.sess or {}).get("pem_path", "—")

        # 重新绑定私钥
        pem_name2 = ft.Ref[ft.Text]()
        key_input2 = ft.TextField(label="API Key ID", dense=True)
        env_select2 = ft.Dropdown(
            label="环境", dense=True, width=140,
            options=[ft.dropdown.Option("prod", "生产环境"),
                     ft.dropdown.Option("demo", "模拟环境")],
            value=self.api.env if self.api else "prod")
        file_picker2 = ft.FilePicker()
        self.page.services.append(file_picker2)

        async def _do_pick_pem2():
            if self.page.web:
                await self._paste_pem_dialog("重新绑定私钥", name_lbl_ref=pem_name2)
                return
            try:
                files = await file_picker2.pick_files(allow_multiple=False,
                                                       allowed_extensions=["pem", "key", "txt"])
            except Exception:
                await self._paste_pem_dialog("重新绑定私钥", name_lbl_ref=pem_name2)
                return
            if files:
                from types import SimpleNamespace
                self.on_pem_picked2(SimpleNamespace(files=files), pem_name2)

        def upload_pem2(e):
            self.page.run_task(_do_pick_pem2)

        # AI 配置
        self._ai_providers = [dict(p) for p in store.load_config()["ai"]["providers"]]
        self._ai_active_idx = store.load_config()["ai"]["active"]
        ai_list_col = ft.Column(spacing=0)
        self._render_ai_rows(ai_list_col)

        self.ai_name = ft.TextField(label="名称", dense=True, width=120)
        self.ai_base = ft.TextField(label="Base URL", dense=True, width=280,
                                    hint_text="https://api.openai.com/v1")
        self.ai_model = ft.TextField(label="模型", dense=True, width=160)
        self.ai_key = ft.TextField(label="API Key", dense=True, password=True, can_reveal_password=True, width=200)
        self.ai_active_select = ft.Dropdown(
            label="默认服务商", dense=True, width=160,
            options=[ft.dropdown.Option(str(i), p["name"]) for i, p in enumerate(self._ai_providers)],
            value=str(min(self._ai_active_idx, len(self._ai_providers) - 1)),
        )

        # 代理
        proxy_box = self._build_proxy_box()

        return ft.Column([
            # 同步
            ft.Text("同步", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                    color=COLORS["muted"], weight=ft.FontWeight.W_500,
                    font_family="JetBrains Mono"),
            ft.Row([
                self.ri_input,
                ft.FilledButton("保存", icon=ft.Icons.SCHEDULE,
                                style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                                on_click=lambda e: self.page.run_task(self.do_save_interval)),
            ], spacing=12),
            ft.Text("默认 60 秒；保存后立即生效并写入 data/config.json。", size=11, color=COLORS["muted"]),
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            # 当前连接
            ft.Text("当前连接", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                    color=COLORS["muted"], weight=ft.FontWeight.W_500,
                    font_family="JetBrains Mono"),
            ft.Text(f"环境：{env_txt}　({url_txt})", size=13),
            ft.Text(f"Key ID：{key_mask}", size=13, font_family="JetBrains Mono"),
            ft.Text(f"私钥路径：{pem_path}", size=11, color=COLORS["muted"]),
            ft.Row([
                ft.OutlinedButton("测试连接", icon=ft.Icons.WIFI_TETHERING,
                                  on_click=lambda e: self.page.run_task(self.test_conn)),
                ft.OutlinedButton("解绑并清除全部环境私钥", icon=ft.Icons.DELETE_FOREVER,
                                  style=ft.ButtonStyle(color=COLORS["neg"]),
                                  on_click=lambda e: self.page.run_task(self.do_unbind)),
            ], spacing=12),
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            # 重新绑定
            ft.Text("重新绑定私钥", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                    color=COLORS["muted"], weight=ft.FontWeight.W_500,
                    font_family="JetBrains Mono"),
            ft.Button("上传新的 PEM", icon=ft.Icons.UPLOAD_FILE, on_click=upload_pem2),
            ft.Text(ref=pem_name2, value="未选择文件", size=11, color=COLORS["muted"]),
            key_input2,
            env_select2,
            ft.FilledButton("保存并连接", icon=ft.Icons.SAVE,
                            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                            on_click=lambda e: self.page.run_task(self.do_rebind, key_input2, env_select2)),
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            # 代理
            proxy_box,
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            ft.FilledButton("保存并测试代理", icon=ft.Icons.NETWORK_CHECK,
                            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                            on_click=lambda e: self.page.run_task(self.save_proxy_and_test)),
            ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
            # AI 模型
            ft.Text("AI 模型", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                    color=COLORS["muted"], weight=ft.FontWeight.W_500,
                    font_family="JetBrains Mono"),
            ft.Text("支持任意 OpenAI 兼容接口（OpenAI / DeepSeek / Moonshot / 自建网关）。",
                    size=11, color=COLORS["muted"]),
            ai_list_col,
            ft.Row([self.ai_name, self.ai_base, self.ai_model, self.ai_key,
                    ft.FilledButton("添加", icon=ft.Icons.ADD,
                                    style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                                    on_click=lambda e: self.page.run_task(self.do_add_provider))],
                   spacing=8, wrap=True),
            ft.Row([
                self.ai_active_select,
                ft.FilledButton("保存默认服务商", icon=ft.Icons.SAVE,
                                style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                                on_click=lambda e: self.page.run_task(self.do_save_ai)),
            ], spacing=12),
        ], spacing=10, width=760, scroll=ft.ScrollMode.AUTO)

    def _build_user_management(self) -> ft.Column:
        self.user_list_col = ft.Column(spacing=0)
        self._render_users()
        self.nu_name = ft.TextField(label="用户名", dense=True, width=160)
        self.nu_pass = ft.TextField(label="密码", dense=True, width=160,
                                    password=True, can_reveal_password=True)
        self.nu_role = ft.Dropdown(
            label="角色", dense=True, width=120,
            options=[ft.dropdown.Option("viewer", "只读"),
                     ft.dropdown.Option("admin", "管理员")],
            value="viewer")
        return ft.Column([
            ft.Text("账户", size=10.5, style=ft.TextStyle(letter_spacing=0.18),
                    color=COLORS["muted"], weight=ft.FontWeight.W_500,
                    font_family="JetBrains Mono"),
            ft.Text("非管理员账户只能访问「概览」与「聊天室」。", size=11, color=COLORS["muted"]),
            self.user_list_col,
            ft.Row([
                self.nu_name, self.nu_pass, self.nu_role,
                ft.FilledButton("添加用户", icon=ft.Icons.PERSON_ADD,
                                style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                                on_click=lambda e: self.page.run_task(self.do_add_user)),
            ], spacing=8),
        ], spacing=10, width=760)

    # ================= 聊天室 =================
    def _build_chat(self) -> ft.Column:
        env = self.api.env if self.api else (self.sess or {}).get("env", "local")
        self.chat_env_name = ENV_LABEL.get(env, env)

        self.chat_input = ft.TextField(
            hint_text="输入消息…", multiline=True, min_lines=1, max_lines=4)
        self.chat_scroll = ft.ListView(spacing=8, expand=True, auto_scroll=True)
        self.chat_box = ft.Column(spacing=8)
        self.chat_scroll.controls.append(self.chat_box)

        self._chat_pick_img = ft.FilePicker()
        self.page.services.append(self._chat_pick_img)

        self.chat_preview = ft.Image(src="", width=120, height=80, visible=False)

        header = ft.Row([
            ft.Container(content=ft.Text(self.chat_env_name, size=11),
                         bgcolor=COLORS["line"], padding=ft.padding.Padding(left=8, top=2, right=8, bottom=2),
                         border_radius=4),
            ft.Text("同一环境下的用户共享这段讨论", size=11, color=COLORS["muted"]),
            spacer(),
            ft.OutlinedButton("刷新", icon=ft.Icons.REFRESH,
                              on_click=lambda e: self.page.run_task(self.load_chat)),
        ])
        input_row = ft.Row([
            ft.Button("图片", icon=ft.Icons.IMAGE,
                              visible=not self.page.web,
                              on_click=lambda e: self.page.run_task(self._pick_chat_image)),
            self.chat_input,
            ft.FilledButton("发送", icon=ft.Icons.SEND,
                            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                            on_click=lambda e: self.page.run_task(self.do_chat_send)),
        ], spacing=8)

        self.body.controls.extend([header, self.chat_scroll, self.chat_preview, input_row])
        self.chat_scroll.height = 400
        self.page.run_task(self.load_chat)

        # 定时刷新聊天室（每 4 秒）
        async def chat_timer():
            await asyncio.sleep(4)
            while not self._closed:
                try:
                    await self.load_chat()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                await asyncio.sleep(4)

        if self.chat_timer_task and not self.chat_timer_task.done():
            self.chat_timer_task.cancel()
        self.chat_timer_task = asyncio.create_task(chat_timer())

    # ================= CF Benchmarks 行情条 =================
    _CF_MAP: dict[str, str] = {
        "BTC": "BRTI", "ETH": "ETHUSD_RTI", "SOL": "SOLUSD_RTI",
        "XRP": "XRPUSD_RTI", "ADA": "ADAUSD_RTI", "BNB": "BNBUSD_RTI",
        "AVAX": "AVAXUSD_RTI", "DOGE": "DOGEUSD_RTI", "LINK": "LINKUSD_RTI",
        "ARB": "ARBUSD_RTI", "OP": "OPUSD_RTI", "MATIC": "MATICUSD_RTI",
        "LTC": "LTCUSD_RTI", "BCH": "BCHUSD_RTI", "XLM": "XLMUSD_RTI",
        "UNI": "UNIUSD_RTI", "AAVE": "AAVEUSD_RTI", "SUI": "SUIUSD_RTI",
    }
    _CF_REVERSE = {"BRTI": "BTC"}

    @classmethod
    def _cf_sym_label(cls, idx_id: str) -> str:
        if idx_id in cls._CF_REVERSE:
            return cls._CF_REVERSE[idx_id]
        return idx_id.replace("USD_RTI", "").replace("USD_RR", "")

    _CF_CORE_SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "DOGE")

    # ================= 渲染 =================
    def render_balance(self) -> None:
        b = self.state.get("balance") or {}
        if self.c_avail:
            self.c_avail.value = usd(b.get("available"))
            if self.c_avail_ts:
                self.c_avail_ts.value = (
                    f"本地库同步于 {fmt_ts(b.get('synced_at'))}" if b.get("synced_at")
                    else "尚无本地快照")
            try:
                self.c_avail.update()
                self.c_avail_ts.update()
            except Exception:
                pass
        if self.updated_label:
            self.updated_label.value = (
                f"本地快照 · {fmt_ts(b.get('synced_at'))}" if b.get("synced_at")
                else "本地快照")
            try:
                self.updated_label.update()
            except Exception:
                pass

        pos = self.state.get("positions") or []
        expo = sum(p.get("exposure") or 0 for p in pos)
        pnl = sum(p.get("realized_pnl") or 0 for p in pos)
        if self.c_expo:
            self.c_expo.value = usd(expo)
        if self.c_pnl:
            self.c_pnl.value = usd(pnl, signed=True)
        resting = sum(1 for o in (self.state.get("orders") or [])
                      if o.get("status") == "resting")
        if self.c_rest:
            self.c_rest.value = str(resting)
        if self.c_subs:
            self.c_subs.value = str(len(self.state.get("subs") or []))
        for lbl in (self.c_expo, self.c_pnl, self.c_rest, self.c_subs):
            if lbl:
                try:
                    lbl.update()
                except Exception:
                    pass

        if self.shard_table:
            shards = b.get("shards") or []
            total = sum(s.get("balance") or 0 for s in shards)
            rows = [{
                "shard": str(s.get("exchange_index")),
                "balance": usd(s.get("balance")),
                "pct": f"{(s.get('balance') or 0) / total * 100:.1f}%" if total else "—",
            } for s in shards]
            self._set_table_rows(self.shard_table, rows)

    def render_subs(self) -> None:
        subs = self.state.get("subs") or []
        rows = [{
            "shard": "—" if s.get("exchange_index") is None else str(s.get("exchange_index")),
            "sub": "—" if s.get("subaccount") is None else str(s.get("subaccount")),
            "balance": usd(s.get("balance")),
            "updated": fmt_ts(s.get("updated_ts")),
        } for s in subs]
        if self.sub_table:
            self._set_table_rows(self.sub_table, rows)
        if self.sub_mini:
            self._set_table_rows(self.sub_mini, rows[:50])
        if self.c_subs:
            self.c_subs.value = str(len(subs))
            try:
                self.c_subs.update()
            except Exception:
                pass

    def render_positions(self) -> None:
        positions = self.state.get("positions") or []
        self.pos_by_ticker = {p["ticker"]: p for p in positions}
        rows = [{
            "ticker": p["ticker"],
            "side": "YES" if p["yes_qty"] > 0 else "NO",
            "qty": f"{int(p['yes_qty'] or p['no_qty'])}",
            "exposure": usd(p.get("exposure")),
            "pnl": usd(p.get("realized_pnl"), signed=True),
            "resting": str(p.get("resting") or 0),
            "shard": "—" if p.get("exchange_index") is None else str(p.get("exchange_index")),
            "act": "交易",
        } for p in positions]
        if self.pos_table:
            self._set_table_rows(self.pos_table, rows)

    def render_orders(self) -> None:
        orders = self.state.get("orders") or []
        self.order_by_id = {o["order_id"]: o for o in orders if o.get("order_id")}
        rows = [{
            "time": fmt_ts(o.get("created")),
            "ticker": o.get("ticker") or "",
            "side": o.get("side_txt") or "",
            "price": usd(o.get("price")),
            "count": f"{(o.get('count') or 0):g}",
            "filled": f"{(o.get('filled') or 0):g}",
            "remaining": f"{(o.get('remaining') or 0):g}",
            "status": o.get("status") or "",
            "shard": "—" if o.get("exchange_index") is None else str(o.get("exchange_index")),
        } for o in orders]
        if self.order_table:
            self._set_table_rows(self.order_table, rows)
        if self.c_rest:
            self.c_rest.value = str(sum(1 for o in orders if o.get("status") == "resting"))
            try:
                self.c_rest.update()
            except Exception:
                pass

    def render_fills(self) -> None:
        fills = self.state.get("fills") or []
        rows = [{
            "time": fmt_ts(f.get("created")),
            "ticker": f.get("ticker") or "",
            "action": "买入" if f.get("action") == "buy" else ("卖出" if f.get("action") == "sell" else "—"),
            "side": (f.get("side") or "—").upper(),
            "price": usd(f.get("price")),
            "count": f"{(f.get('count') or 0):g}",
            "role": "Taker" if f.get("is_taker") else "Maker",
            "order": (f.get("order_id") or "")[:18],
        } for f in fills]
        if self.fill_table:
            self._set_table_rows(self.fill_table, rows)

    def render_cf_overview(self) -> None:
        if not self.cf_row:
            return
        ids = sorted(self._cf_index_ids) or [
            self._CF_MAP[s] for s in self._CF_CORE_SYMBOLS if s in self._CF_MAP]
        snap = self.cfb_latest or {}
        self.cf_row.controls.clear()
        live = [i for i in ids if (snap.get(i) or {}).get("value") is not None]
        if not live:
            if self.cf_src_label:
                self.cf_src_label.value = "数据源：Kalshi 转发 · 当前不可用"
                try:
                    self.cf_src_label.update()
                except Exception:
                    pass
            reason = self._cf_last_error or "尚未拉取"
            self.cf_row.controls.append(
                ft.Text(f"等待数据 —— {reason}", size=11, color=COLORS["muted"]))
            try:
                self.cf_row.update()
            except Exception:
                pass
            return
        if self.cf_src_label:
            self.cf_src_label.value = "数据源：Kalshi 转发"
            try:
                self.cf_src_label.update()
            except Exception:
                pass
        for idx in ids:
            rec = snap.get(idx) or {}
            sym = self._cf_sym_label(idx)
            val = rec.get("value")
            ts = rec.get("ingested_at")
            stamp = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else "—"
            card = ft.Container(
                content=ft.Column([
                    ft.Text(f"{sym} / USD", size=10, style=ft.TextStyle(letter_spacing=0.16),
                            color=COLORS["muted"], weight=ft.FontWeight.W_500,
                            font_family="JetBrains Mono"),
                    ft.Text(f"${val:,.2f}" if val is not None else "—",
                            size=22, weight=ft.FontWeight.W_600,
                            font_family="JetBrains Mono"),
                    ft.Text(f"更新 {stamp}", size=10, color=COLORS["muted"],
                            font_family="JetBrains Mono"),
                ], spacing=2, min_width=130),
                padding=ft.padding.Padding(12),
                border=ft.BorderSide(1, COLORS["line"]),
                border_radius=6,
            )
            self.cf_row.controls.append(card)
        try:
            self.cf_row.update()
        except Exception:
            pass

    # ================= 核心异步方法 =================
    async def load_cached(self) -> None:
        if not self.api:
            return
        loop = asyncio.get_running_loop()
        try:
            state = await loop.run_in_executor(
                None, db.load_state, self.api.env, self.sess["key_id"],
                self.status_filter, self.orders_ticker or None,
                self.fills_ticker or None, self.fills_days, True)
        except Exception:
            return
        self.state.update(state)
        self.render_balance()
        self.render_positions()
        self.render_orders()
        self.render_subs()
        self.render_fills()
        try:
            self.cfb_latest = await loop.run_in_executor(None, db.cfb_latest, self.api.env) or {}
        except Exception:
            self.cfb_latest = {}
        self.render_cf_overview()

    async def refresh_all(self, manual: bool = False) -> None:
        if not self.api or self.busy:
            return
        self.busy = True
        loop = asyncio.get_running_loop()
        try:
            jobs = []
            for delay, fn, args in [
                (0.0, self.api.balance, ()),
                (0.25, self.api.positions, ()),
                (0.5, self.api.orders, (self.status_filter, self.orders_ticker or None, None)),
                (0.75, self.api.subaccount_balances, ()),
            ]:
                async def _j(d=delay, f=fn, a=args):
                    await asyncio.sleep(d)
                    return await loop.run_in_executor(None, f, *a)
                jobs.append(_j())
            with_fills = manual or self.ticks % 2 == 0
            if with_fills:
                async def _jf():
                    await asyncio.sleep(1.0)
                    return await loop.run_in_executor(None, self.api.fills,
                                                      self.fills_ticker or None, self.fills_days)
                jobs.append(_jf())

            results = await asyncio.gather(*jobs, return_exceptions=True)
            errs = []
            loaded = {}
            for i, key in enumerate(
                    ["balance", "positions", "orders", "subs"] + (["fills"] if with_fills else [])):
                r = results[i]
                if isinstance(r, Exception):
                    errs.append(f"{key}: {r}")
                else:
                    loaded[key] = r

            env, key_id = self.api.env, self.sess["key_id"]
            try:
                await loop.run_in_executor(None, db.persist, env, key_id, **loaded)
            except Exception as ex:
                errs.append(f"本地库写入失败: {ex}")

            # CF Benchmarks
            try:
                await self._cf_discover()
                await self._cf_fetch_and_persist()
                self.render_cf_overview()
            except Exception as ex:
                errs.append(f"CF Benchmarks 刷新失败: {ex}")

            try:
                state = await loop.run_in_executor(
                    None, db.load_state, env, key_id, self.status_filter,
                    self.orders_ticker or None, self.fills_ticker or None,
                    self.fills_days, with_fills)
            except Exception as ex:
                errs.append(f"本地库读取失败: {ex}")
                state = {}

            if state:
                self.state.update(state)
                if "balance" in loaded:
                    self.render_balance()
                if "positions" in loaded:
                    self.render_positions()
                if "orders" in loaded:
                    self.render_orders()
                if "subs" in loaded:
                    self.render_subs()
                if with_fills and "fills" in loaded:
                    self.render_fills()

            self.ticks += 1
            limited = any("429" in e for e in errs)
            if limited:
                self.cooldown = time.time() + 10
            if self.updated_label:
                if limited:
                    self.updated_label.value = "触发限流，10 秒后恢复"
                elif errs and manual:
                    self.updated_label.value = "部分同步失败"
                else:
                    self.updated_label.value = f"已同步 {datetime.now().strftime('%H:%M:%S')}"
                try:
                    self.updated_label.update()
                except Exception:
                    pass
            if errs and manual:
                self.notify(errs[0], type="negative")
        finally:
            self.busy = False

    async def refresh_orders(self) -> None:
        if not self.api or self._orders_busy:
            return
        self._orders_busy = True
        loop = asyncio.get_running_loop()
        try:
            rows = await loop.run_in_executor(None, self.api.orders,
                                              self.status_filter, self.orders_ticker or None, None)
            await loop.run_in_executor(None, db.save_orders, self.api.env, rows)
            self.state["orders"] = await loop.run_in_executor(
                None, db.orders, self.api.env, self.status_filter, self.orders_ticker or None)
            self.render_orders()
        except Exception as ex:
            self.notify(f"订单查询失败：{ex}", type="negative")
        finally:
            self._orders_busy = False

    async def refresh_fills(self) -> None:
        if not self.api or self._fills_busy:
            return
        self._fills_busy = True
        loop = asyncio.get_running_loop()
        try:
            rows = await loop.run_in_executor(None, self.api.fills,
                                              self.fills_ticker or None, self.fills_days)
            await loop.run_in_executor(None, db.save_fills, self.api.env, rows)
            self.state["fills"] = await loop.run_in_executor(
                None, db.fills, self.api.env, self.fills_ticker or None, self.fills_days)
            self.render_fills()
        except Exception as ex:
            self.notify(f"成交查询失败：{ex}", type="negative")
        finally:
            self._fills_busy = False

    async def refresh_transfers(self) -> None:
        if not self.api:
            return
        loop = asyncio.get_running_loop()
        try:
            rows = await loop.run_in_executor(None, self.api.subaccount_transfers, 50)
            await loop.run_in_executor(None, db.save_transfers, self.api.env, rows)
            rows = await loop.run_in_executor(None, db.transfers, self.api.env, 50)
            if self.tr_table:
                self._set_table_rows(self.tr_table, [{
                    "time": fmt_ts(r.get("created_ts")),
                    "amount": usd(r.get("amount")),
                    "from": str(r.get("from_subaccount")),
                    "to": str(r.get("to_subaccount")),
                } for r in rows])
        except Exception as ex:
            self.notify(f"转账记录查询失败：{ex}", type="negative")

    async def _on_status_change(self, e=None) -> None:
        v = self.status_toggle.value
        self.status_filter = None if v == "all" else v
        await self._orders_from_db()

    async def _orders_from_db(self) -> None:
        loop = asyncio.get_running_loop()
        self.state["orders"] = await loop.run_in_executor(
            None, db.orders, self.api.env if self.api else "prod",
            self.status_filter, self.orders_ticker or None)
        self.render_orders()

    async def _on_order_query(self, e=None) -> None:
        if self._orders_busy:
            return
        self.orders_ticker = (self.ord_ticker_input.value or "").strip()
        await self.refresh_orders()

    async def _on_fill_query(self, e=None) -> None:
        if self._fills_busy:
            return
        self.fills_ticker = (self.fill_ticker_input.value or "").strip()
        days_val = self.fill_days_select.value
        self.fills_days = None if days_val == "0" else int(days_val)
        await self.refresh_fills()

    # ================= CF Benchmarks 发现/拉取 =================
    async def _cf_discover(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._cf_last_discovery) < 600:
            return
        ids = {self._CF_MAP[s] for s in self._CF_CORE_SYMBOLS if s in self._CF_MAP}
        loop = asyncio.get_running_loop()
        try:
            import re as _re
            d = await loop.run_in_executor(None, self.api.cli.request, "GET", "/events",
                                           {"status": "open", "limit": 200})
            for e in (d.get("events") or []):
                if (e.get("category") or "").lower() != "crypto":
                    continue
                for s in (e.get("settlement_sources") or []):
                    m = _re.search(r"/indices/([A-Za-z0-9_]+)", s.get("url") or "")
                    if m:
                        ids.add(m.group(1))
        except Exception:
            pass
        self._cf_index_ids = ids
        self._cf_last_discovery = now

    async def _cf_fetch_and_persist(self, force: bool = False) -> None:
        if not self._cf_index_ids or not self.api:
            return
        if not force and time.time() < self._cf_fail_until:
            return
        ids = sorted(self._cf_index_ids)
        rows = []
        loop = asyncio.get_running_loop()
        summaries = await loop.run_in_executor(None, self.api.cf_summaries, ids)
        if summaries:
            rows.extend(summaries)
        else:
            for idx in ids:
                r = await loop.run_in_executor(None, self.api.cf_values, idx)
                if r:
                    rows.append(r)
        if not rows:
            self._cf_fail_until = time.time() + 600
            try:
                _ok, msg = await loop.run_in_executor(None, self.api.cf_probe, ids[0])
                self._cf_last_error = msg
            except Exception as ex:
                self._cf_last_error = str(ex)[:160]
            return
        self._cf_last_error = ""
        self._cf_fail_until = 0.0
        try:
            await loop.run_in_executor(None, db.save_cfb_ticks, self.api.env, rows)
        except Exception:
            pass
        latest = await loop.run_in_executor(None, db.cfb_latest, self.api.env, list(ids))
        self.cfb_latest.update(latest)

    async def refresh_cf(self) -> None:
        if not self.api:
            return
        await self._cf_discover(force=True)
        await self._cf_fetch_and_persist(force=True)
        self.render_cf_overview()

    # ================= 交易对话框（简化版）=================
    async def open_trade(self, ticker: str = "", leg: Optional[str] = None,
                         qty: Optional[int] = None) -> None:
        if not self.api:
            return
        if self._trade_opening:
            self.notify("交易对话框正在加载，请稍候", type="warning")
            return
        self._trade_opening = True
        loop = asyncio.get_running_loop()
        try:
            quote = None
            if ticker:
                try:
                    quote = await loop.run_in_executor(None, self.api.quote, ticker)
                except Exception:
                    pass
            catalog = []
            try:
                catalog = await loop.run_in_executor(None, self.api.series_catalog)
            except Exception:
                pass
            self._catalog = [s for s in catalog
                             if s["category"] and "test" not in s["category"].lower()]
            cats = sorted({s["category"] for s in self._catalog})
            cat = "Crypto" if "Crypto" in cats else (cats[0] if cats else None)

            trade_content = await self._build_trade_dialog_content(
                cat, quote, ticker, leg, qty)

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("下单 / 平仓", weight=ft.FontWeight.W_600),
                content=ft.Container(content=trade_content, width=820,
                                     padding=ft.padding.Padding(12)),
                actions_alignment=ft.MainAxisAlignment.END,
                actions=[
                    ft.TextButton("关闭", on_click=lambda e: self._close_trade_dlg(dialog)),
                ],
            )
            self.page.show_dialog(dialog)
            self.page.update()
            self._trade_dialog = dialog
        finally:
            self._trade_opening = False

    def _close_trade_dlg(self, dialog):
        dialog.open = False
        try:
            dialog.update()
        except Exception:
            pass
        try:
            self.page.pop_dialog()
        except Exception:
            pass

    async def _build_trade_dialog_content(self, cat, quote, ticker, leg, qty) -> ft.Column:
        """构建交易对话框的内容（返回一个 Column）。"""
        loop = asyncio.get_running_loop()
        cat_options = {c: CATEGORY_CN.get(c, c) for c in sorted(
            {s["category"] for s in self._catalog})}
        freq_set = {s["frequency"] for s in self._catalog
                    if s["category"] == cat}
        freqs = [f for f in FREQ_ORDER if f in freq_set] or list(freq_set)

        # 先拿该分类 + 周期的 series 选项
        series = [s for s in self._catalog if s["category"] == cat and s["frequency"] in freqs]
        series_options = {s["ticker"]: f"{s['frequency']} · {s['title'][:40]}" for s in series}

        cat_drop = ft.Dropdown(
            label="分类", dense=True, width=200,
            options=[ft.dropdown.Option(k, v) for k, v in cat_options.items()],
            value=cat)
        freq_drop = ft.Dropdown(
            label="周期", dense=True, width=140,
            options=[ft.dropdown.Option(f, FREQ_CN.get(f, f)) for f in freqs],
            value=freqs[0] if freqs else None)
        series_drop = ft.Dropdown(
            label="系列", dense=True, expand=True,
            options=[ft.dropdown.Option(k, v) for k, v in sorted(series_options.items())])

        # market ticker
        tk_drop = ft.Dropdown(label="选择 ticker", dense=True, expand=True)
        q_info = ft.Text((quote or {}).get("title") or "选择市场与 ticker 后自动加载行情",
                         size=11, color=COLORS["muted"])
        q_line = ft.Text("", size=13, font_family="JetBrains Mono")

        def load_markets(series_t):
            if not series_t:
                tk_drop.options = []
                tk_drop.value = None
                self.page.update()
                return

            def _get():
                try:
                    return loop.run_in_executor(None, self.api.markets_by_series, series_t)
                except Exception:
                    return []

            def done(fut):
                try:
                    mkts = fut.result()
                    tk_drop.options = [ft.dropdown.Option(m["ticker"], m["label"]) for m in mkts]
                    self.page.update()
                except Exception:
                    pass

            import concurrent.futures
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut = pool.submit(loop.run_until_complete, self.api.markets_by_series(series_t))
            fut.add_done_callback(lambda f: done_wrap(f))

            def done_wrap(f):
                try:
                    mkts = f.result()
                    tk_drop.options = [ft.dropdown.Option(m["ticker"], m["label"]) for m in mkts]
                    self.page.update()
                except Exception:
                    pass

        default_action = "sell_yes" if leg == "yes" else ("sell_no" if leg == "no" else "buy_yes")
        px_input = ft.TextField(label="价格（美元）", dense=True, width=140, value="0.5")
        qty_input = ft.TextField(label="数量（张）", dense=True, width=120,
                                  value=str(qty or 1))
        tif_drop = ft.Dropdown(
            label="TIF", dense=True, width=140,
            options=[ft.dropdown.Option("immediate_or_cancel", "IOC 立即成交"),
                     ft.dropdown.Option("good_till_canceled", "GTC 挂单")],
            value="immediate_or_cancel")
        action_drop = ft.Dropdown(
            label="动作", dense=True, width=140,
            options=[ft.dropdown.Option(k, v) for k, v in ACTION_LABEL.items()],
            value=default_action)
        post_only = ft.Checkbox(label="只做 maker（post-only）")

        # 初始化：如果有 ticker 就尝试找对应的 series
        if ticker:
            pos = self.pos_by_ticker.get(ticker)
            for s in series:
                series_drop.value = s["ticker"]
                break
            # 直接预填
            tk_drop.value = ticker
            if quote:
                q_info.value = quote.get("title", "")
                q_line.value = (
                    f"YES bid={quote.get('yes_bid')} ask={quote.get('yes_ask')}    "
                    f"NO bid={quote.get('no_bid')} ask={quote.get('no_ask')}")

        def submit(e):
            self.page.run_task(self.submit_trade,
                               action_drop.value, tk_drop.value, px_input.value,
                               qty_input.value, tif_drop.value,
                               post_only.value)

        def reload_q(e):
            self.page.run_task(self.reload_quote, tk_drop.value, q_info, q_line)

        def on_series_change(e):
            load_markets(series_drop.value)

        series_drop.on_change = on_series_change
        tk_drop.on_change = lambda e: self.page.run_task(self.reload_quote, tk_drop.value, q_info, q_line)

        return ft.Column([
            ft.Row([cat_drop, freq_drop,
                    ft.IconButton(ft.Icons.REFRESH, tooltip="刷新",
                                  on_click=lambda e: self.page.run_task(self.open_trade))
                    ], spacing=8),
            ft.Row([series_drop, tk_drop], spacing=8),
            q_info, q_line,
            ft.Row([action_drop, px_input, qty_input, tif_drop], spacing=8),
            ft.Row([post_only]),
            ft.Row([
                ft.OutlinedButton("刷新行情", icon=ft.Icons.REFRESH, on_click=reload_q),
                ft.FilledButton("提交订单", icon=ft.Icons.SEND,
                                style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#07080b"),
                                on_click=submit),
            ], spacing=12),
        ], spacing=10, width=float("inf"))

    async def reload_quote(self, ticker, q_info_lbl, q_line_lbl) -> None:
        if not ticker or not self.api:
            return
        loop = asyncio.get_running_loop()
        try:
            quote = await loop.run_in_executor(None, self.api.quote, ticker)
        except Exception as ex:
            q_info_lbl.value = f"行情获取失败：{ex}"
            self.page.update()
            return
        q_info_lbl.value = quote.get("title", "")
        q_line_lbl.value = (
            f"YES bid={quote.get('yes_bid')} ask={quote.get('yes_ask')}    "
            f"NO bid={quote.get('no_bid')} ask={quote.get('no_ask')}")
        self.page.update()

    async def submit_trade(self, action, ticker, px_str, qty_str, tif, post_only) -> None:
        if not self.api or not ticker:
            self.notify("请选择 ticker", type="warning")
            return
        try:
            price = float(px_str)
            count = int(float(qty_str))
        except (TypeError, ValueError):
            self.notify("价格或数量格式错误", type="warning")
            return
        loop = asyncio.get_running_loop()
        try:
            side, yes_price = DeskAPI.to_book(action, price)
            order = await loop.run_in_executor(
                None, self.api.place, ticker, side, yes_price, count, tif, post_only)
            self.notify(f"订单已提交：{order.get('order_id', '')[:12]}", type="positive")
        except Exception as ex:
            self.notify(f"下单失败：{ex}", type="negative")
            return
        # 刷新订单
        await self.refresh_orders()
        # 关闭对话框
        if hasattr(self, "_trade_dialog") and self._trade_dialog:
            self._close_trade_dlg(self._trade_dialog)

    # ================= 交易辅助方法 =================
    async def reload_catalog(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            catalog = await loop.run_in_executor(None, self.api.series_catalog, True)
        except Exception as ex:
            self.notify(f"刷新目录失败：{ex}", type="negative")
            return
        self._catalog = [s for s in catalog
                         if s["category"] and "test" not in s["category"].lower()]
        self.notify("市场目录已刷新", type="positive")

    def _freqs_for(self, cat: str) -> list:
        freq_set = {s["frequency"] for s in self._catalog if s["category"] == cat}
        return [f for f in FREQ_ORDER if f in freq_set] or list(freq_set)

    def _series_options(self, cat: str, freq: str) -> dict:
        series = [s for s in self._catalog if s["category"] == cat and s["frequency"] == freq]
        return {s["ticker"]: f"{s['title'][:40]}" for s in series}

    async def _on_category_pick(self, cat: str) -> None:
        freqs = self._freqs_for(cat)
        if freqs:
            pass  # 实际在对话框内已内嵌

    async def _on_freq_pick(self) -> None:
        pass

    def _set_quote(self, quote) -> None:
        pass

    def _on_action_change(self, val) -> None:
        pass

    # ================= 代理事件处理 =================
    def on_pem_picked2(self, e: ft.FilePickerResultEvent, name_lbl_ref) -> None:
        if not e.files:
            return

        def _load():
            try:
                path = e.files[0].path
                text = store.normalize_pem(Path(path).read_bytes())
                return text, e.files[0].name
            except Exception as ex:
                return None, str(ex)

        def done(fut):
            text, name_or_err = fut.result()
            if text is None:
                self.notify(f"PEM 解析失败：{name_or_err}", type="negative")
                return
            self._pem_text = text
            name_lbl_ref.current.value = f"已选择：{name_or_err}"
            self.page.update()
            self.notify("私钥已载入", type="positive")

        import concurrent.futures
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(_load)
        fut.add_done_callback(done)

    async def do_rebind(self, key_input2, env_select2) -> None:
        await self._connect((key_input2.value or "").strip(), env_select2.value or "prod")

    async def test_conn(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            bal = await loop.run_in_executor(None, self.api.balance)
            self.notify(f"连接正常，可用余额 {usd(bal['available'])}", type="positive")
        except Exception as ex:
            self.notify(f"连接失败：{ex}", type="negative")

    async def do_save_interval(self) -> None:
        try:
            value = store.save_refresh_interval(int(self.ri_input.value or 0))
        except (TypeError, ValueError) as ex:
            self.notify(f"保存失败：{ex}", type="negative")
            return
        self.refresh_interval = value
        if self.interval_label:
            self.interval_label.value = f"每 {value} 秒"
            try:
                self.interval_label.update()
            except Exception:
                pass
        self.notify(f"自动刷新间隔已设为 {value} 秒", type="positive")

    async def save_proxy_and_test(self) -> None:
        try:
            self.proxy_cfg = store.save_proxy({
                "enabled": bool(self.px_enabled.value),
                "host": self.px_host.value,
                "port": int(self.px_port.value or 1080),
                "username": self.px_user.value,
                "password": self.px_pass.value,
            })
        except ValueError as ex:
            self.notify(str(ex), type="negative")
            return
        self.px_status.value = self._proxy_status_text(self.proxy_cfg)
        try:
            self.px_status.update()
        except Exception:
            pass
        url = store.proxy_url(self.proxy_cfg)
        if not url:
            if self.api and self.sess:
                self.api = self._new_api(self.sess["key_id"], self.sess["pem_path"],
                                         self.sess["env"])
            self.notify("已保存：未启用代理，改为直连", type="positive")
            return
        base = ENDPOINTS[self.api.env] if self.api else ENDPOINTS["prod"]

        def _probe():
            import requests as r
            resp = r.get(f"{base}/markets", params={"limit": 1},
                         proxies={"http": url, "https": url}, timeout=15)
            return f"HTTP {resp.status_code}，返回 {len(resp.content)} 字节"

        loop = asyncio.get_running_loop()
        try:
            msg = await loop.run_in_executor(None, _probe)
        except Exception as ex:
            self.notify(f"代理测试失败：{ex}", type="negative")
            return
        self.notify(f"代理可用（{msg}）", type="positive")
        if self.api and self.sess:
            try:
                api = self._new_api(self.sess["key_id"], self.sess["pem_path"], self.sess["env"])
                await loop.run_in_executor(None, api.balance)
            except Exception as ex:
                self.notify(f"代理切换后连接失败：{ex}", type="negative")
                return
            self.api = api
            await self.refresh_all(True)
            self.notify("已切换代理并重新连接成功", type="positive")

    async def switch_env(self) -> None:
        if not self.api:
            return
        target = "demo" if self.api.env == "prod" else "prod"
        label = "模拟环境" if target == "demo" else "生产环境"
        sess = store.load_session(target)
        loop = asyncio.get_running_loop()
        if not sess:
            self.login_env = target
            self.api, self.sess = None, None
            self.notify(f"尚未绑定{label}的 API Key，请上传私钥并填写 Key ID", type="warning")
            self.rebuild()
            return
        try:
            api = self._new_api(sess["key_id"], sess["pem_path"], target)
            bal = await loop.run_in_executor(None, api.balance)
        except Exception as ex:
            self.notify(f"切换到{label}失败：{ex}", type="negative")
            return
        try:
            await loop.run_in_executor(None, db.save_balance, target, sess["key_id"], bal)
            await loop.run_in_executor(None, store.save_last_env, target)
        except Exception:
            pass
        self.api, self.sess = api, sess
        self.state = {"balance": {}, "positions": [], "orders": [], "fills": [], "subs": []}
        self.page_name = PAGE_OVER
        self.notify(f"已切换到{label}（{store.mask_key(sess['key_id'])}）", type="positive")
        self.rebuild()
        if self.api:
            self.page.run_task(self.load_cached)

    async def do_unbind(self) -> None:
        if not await self.confirm("确定解除绑定？本机保存的私钥文件将被删除。"):
            return
        store.clear_session()
        self.api, self.sess = None, None
        self.state = {"balance": {}, "positions": [], "orders": [], "fills": [], "subs": []}
        self.notify("已解绑并清除本地私钥", type="positive")
        self.rebuild()

    async def do_logout(self) -> None:
        if not await self.confirm("退出工作台？Kalshi 私钥与本地数据仍保留在本机。"):
            return
        self.authed, self.user = False, ""
        self.current_user = None
        self.page_name = PAGE_OVER
        self.api, self.sess = None, None
        self.rebuild()

    # ================= 子账户/转账 =================
    async def do_transfer_sub(self) -> None:
        if not self.api:
            return
        try:
            from_s = int(self.t_from.value or 0)
            to_s = int(self.t_to.value or 0)
            amt = float(self.t_amt.value or 0)
            shard_val = self.t_shard.value
            shard = int(shard_val) if shard_val not in (None, "") else None
        except (TypeError, ValueError):
            self.notify("金额或子账户格式错误", type="warning")
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self.api.transfer_subaccounts,
                                       from_s, to_s, amt, shard)
        except Exception as ex:
            self.notify(f"转账失败：{ex}", type="negative")
            return
        self.notify("转账已提交", type="positive")
        await asyncio.gather(self.refresh_all(), self.refresh_transfers())

    async def do_transfer_shard(self) -> None:
        if not self.api:
            return
        try:
            from_s = int(self.x_from.value or 0)
            to_s = int(self.x_to.value or 0)
            amt = float(self.x_amt.value or 0)
            src = self.x_src.value or "event_contract"
            dst = self.x_dst.value or "event_contract"
        except (TypeError, ValueError):
            self.notify("参数格式错误", type="warning")
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self.api.transfer_across_shards,
                                       from_s, to_s, amt, src, dst)
        except Exception as ex:
            self.notify(f"跨 shard 转账失败：{ex}", type="negative")
            return
        self.notify("跨 shard 转账已提交", type="positive")
        await asyncio.gather(self.refresh_all(), self.refresh_transfers())

    async def do_create_subaccount(self) -> None:
        if not self.api:
            return
        shard_val = self.new_sub_shard.value
        shard = int(shard_val) if shard_val not in (None, "") else None
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self.api.create_subaccount, shard)
        except Exception as ex:
            self.notify(f"创建子账户失败：{ex}", type="negative")
            return
        self.notify("子账户已创建", type="positive")
        await self.refresh_all()
        await self.refresh_transfers()

    # ================= 分析 =================
    async def load_analytics(self) -> None:
        if not self.api:
            return
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, db.analytics, self.api.env)
        except Exception as ex:
            self.notify(f"统计失败：{ex}", type="negative")
            return
        f = data.get("fills") or {}
        s = data.get("settlements") or {}
        if self.an_fills:
            self.an_fills.value = str(f.get("n") or 0)
        if self.an_fills_sub:
            self.an_fills_sub.value = f"{f.get('contracts', 0):g} 张合约"
        if self.an_vol:
            self.an_vol.value = usd(f.get("volume"))
        if self.an_fees:
            self.an_fees.value = usd(f.get("fees"))
        if self.an_net:
            self.an_net.value = usd(s.get("net"), signed=True)
        if self.an_pos:
            self.an_pos.value = str(data.get("position_count") or 0)
        for lbl in (self.an_fills, self.an_fills_sub, self.an_vol, self.an_fees,
                    self.an_net, self.an_pos):
            if lbl:
                try:
                    lbl.update()
                except Exception:
                    pass
        if self.an_table:
            self._set_table_rows(self.an_table, [{
                "ticker": r.get("ticker") or "",
                "fills": str(r.get("fills") or 0),
                "contracts": f"{r.get('contracts') or 0:g}",
                "volume": usd(r.get("volume")),
            } for r in (data.get("top_markets") or [])])

    # ================= AI =================
    async def do_ai_ask(self) -> None:
        question = (self.ai_question.value or "").strip()
        if not question:
            self.notify("请输入问题", type="warning")
            return
        cfg = store.load_config()["ai"]
        try:
            idx = int(self.ai_active_drop.value or cfg["active"])
        except Exception:
            idx = cfg["active"]
        provider = cfg["providers"][min(max(idx, 0), len(cfg["providers"]) - 1)]
        if not provider.get("api_key"):
            self.notify("请先在「系统设置 → AI 模型」中填写 API Key", type="negative")
            return
        self.ai_answer.value = "_思考中…_"
        self.page.update()
        loop = asyncio.get_running_loop()
        try:
            env = self.api.env if self.api else "local"
            data = await loop.run_in_executor(None, db.analytics, env)
            context = json.dumps(data, ensure_ascii=False, default=str)[:12000]
            answer = await loop.run_in_executor(
                None, ai_client.ask, question, context, provider,
                store.proxy_url(self.proxy_cfg))
            self.ai_answer.value = answer or "（模型没有返回内容）"
        except Exception as ex:
            self.ai_answer.value = f"**分析失败**：{ex}"
        self.page.update()

    # ================= 用户管理 =================
    def _render_users(self) -> None:
        if not hasattr(self, "user_list_col") or self.user_list_col is None:
            return
        self.user_list_col.controls.clear()
        for u in store.load_users():
            row = ft.Row([
                ft.Text(u["username"], size=13, font_family="JetBrains Mono"),
                ft.Text("管理员" if u["role"] == "admin" else "只读",
                        size=11, color=COLORS["muted"]),
                spacer(),
                ft.IconButton(ft.Icons.KEY, tooltip="重置密码",
                              on_click=lambda e, n=u["username"]: self.page.run_task(self.do_reset_pw, n)),
                ft.IconButton(ft.Icons.DELETE, tooltip="删除用户",
                              icon_color=COLORS["neg"],
                              on_click=lambda e, n=u["username"]: self.page.run_task(self.do_delete_user, n)),
            ], spacing=8)
            self.user_list_col.controls.append(
                ft.Container(content=row, padding=ft.padding.Padding(top=8, bottom=8),
                             border=ft.Border(bottom=ft.BorderSide(1, COLORS["line"]))))

    async def do_add_user(self) -> None:
        name = (self.nu_name.value or "").strip()
        users = store.load_users()
        if any(u["username"] == name for u in users):
            self.notify("用户名已存在", type="negative")
            return
        users.append({"username": name, "password": self.nu_pass.value or "",
                      "role": self.nu_role.value or "viewer"})
        try:
            store.save_users(users)
        except ValueError as ex:
            self.notify(str(ex), type="negative")
            return
        self.notify(f"已添加用户 {name}", type="positive")
        self.show_page(PAGE_SET)

    async def do_delete_user(self, name: str) -> None:
        if not await self.confirm(f"删除用户 {name}？"):
            return
        users = [u for u in store.load_users() if u["username"] != name]
        try:
            store.save_users(users)
        except ValueError as ex:
            self.notify(str(ex), type="negative")
            return
        if name == self.user:
            self.authed, self.current_user, self.user = False, None, ""
            self.api, self.sess = None, None
            self.rebuild()
            return
        self.notify(f"已删除用户 {name}", type="positive")
        self.show_page(PAGE_SET)

    async def do_reset_pw(self, name: str) -> None:
        pwd = await self.prompt_text(f"重置 {name} 的密码")
        if not pwd:
            return
        users = store.load_users()
        for u in users:
            if u["username"] == name:
                u["password"] = pwd
        try:
            store.save_users(users)
        except ValueError as ex:
            self.notify(str(ex), type="negative")
            return
        self.notify(f"已重置 {name} 的密码", type="positive")
        self.show_page(PAGE_SET)

    # ================= AI 配置 =================
    def _render_ai_rows(self, container_col: ft.Column) -> None:
        if container_col is None:
            return
        container_col.controls.clear()
        for i, p in enumerate(self._ai_providers):
            row = ft.Row([
                ft.Text(p["name"], size=13, font_family="JetBrains Mono"),
                ft.Text(p.get("base_url") or "—", size=11, color=COLORS["muted"]),
                ft.Text(p.get("model") or "—", size=11, color=COLORS["muted"]),
                ft.Text("已配置 Key" if p.get("api_key") else "未配置 Key",
                        size=11, color=COLORS["muted"]),
                spacer(),
                ft.IconButton(ft.Icons.DELETE, icon_color=COLORS["neg"],
                              tooltip="删除",
                              on_click=lambda e, idx=i: self.page.run_task(self.do_del_provider, idx)),
            ], spacing=12)
            container_col.controls.append(
                ft.Container(content=row, padding=ft.padding.Padding(top=6, bottom=6),
                             border=ft.Border(bottom=ft.BorderSide(1, COLORS["line"]))))

    async def do_add_provider(self) -> None:
        name = (self.ai_name.value or "").strip()
        base = (self.ai_base.value or "").strip().rstrip("/")
        model = (self.ai_model.value or "").strip()
        key = self.ai_key.value or ""
        if not (name and base and model):
            self.notify("名称、Base URL、模型不能为空", type="warning")
            return
        self._ai_providers.append({"name": name, "base_url": base,
                                   "model": model, "api_key": key})
        try:
            store.save_ai(self._ai_providers, self._ai_active_idx)
        except ValueError as ex:
            self.notify(str(ex), type="negative")
            return
        self.notify(f"已添加 AI 服务商 {name}", type="positive")
        self.show_page(PAGE_SET)

    async def do_del_provider(self, idx: int) -> None:
        if not (0 <= idx < len(self._ai_providers)):
            return
        name = self._ai_providers[idx]["name"]
        if not await self.confirm(f"删除 AI 服务商 {name}？"):
            return
        self._ai_providers.pop(idx)
        active = min(self._ai_active_idx, len(self._ai_providers) - 1)
        store.save_ai(self._ai_providers, max(active, 0))
        self.notify(f"已删除 {name}", type="positive")
        self.show_page(PAGE_SET)

    async def do_save_ai(self) -> None:
        try:
            idx = int(self.ai_active_select.value or 0)
        except Exception:
            idx = 0
        store.save_ai(self._ai_providers, idx)
        self.notify("已保存默认 AI 服务商", type="positive")
        self.show_page(PAGE_SET)

    # ================= 聊天室 =================
    async def load_chat(self) -> None:
        if self._closed:
            return
        if not hasattr(self, "chat_box") or self.chat_box is None:
            return
        env = self.api.env if self.api else (self.sess or {}).get("env", "local")
        loop = asyncio.get_running_loop()
        try:
            rows = await loop.run_in_executor(None, db.chat_messages, env, 100)
        except Exception:
            return
        self.chat_box.controls.clear()
        for m in rows:
            col = ft.Column([
                ft.Text(f"{m.get('username')} · {fmt_ts(m.get('created'))}",
                        size=10, color=COLORS["muted"], font_family="JetBrains Mono"),
            ], spacing=2)
            if m.get("image"):
                col.controls.append(ft.Image(
                    src=f"/{m['image']}",
                    width=320, fit=ft.ImageFit.CONTAIN))
            if m.get("body"):
                col.controls.append(ft.Container(
                    content=ft.Text(m["body"], size=12.5, color=COLORS["text"]),
                    bgcolor=COLORS["panel"],
                    padding=ft.padding.Padding(10),
                    border=ft.Border(left=ft.BorderSide(2, COLORS["line_strong"])),
                    border_radius=6))
            self.chat_box.controls.append(col)
        try:
            self.chat_box.update()
        except Exception:
            pass

    async def _pick_chat_image(self) -> None:
        files = await self._chat_pick_img.pick_files(
            allow_multiple=False,
            allowed_extensions=["png", "jpg", "jpeg", "gif", "webp"])
        if files:
            from types import SimpleNamespace
            await self.on_chat_upload(SimpleNamespace(files=files))

    async def on_chat_upload(self, e: ft.FilePickerResultEvent) -> None:
        if not e.files:
            return
        f = e.files[0]
        if f.size and f.size > 4 * 1024 * 1024:
            self.notify("图片不能超过 4MB", type="negative")
            return
        from uuid import uuid4
        name = f"{uuid4().hex}{Path(f.name or 'img.png').suffix or '.png'}"
        folder = store.DATA_DIR / "chat_images"
        folder.mkdir(parents=True, exist_ok=True)

        def _save():
            try:
                Path(f.path).copy_to(folder / name)
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _save)
        self._chat_image = name
        self.chat_preview.src = f"/{name}"
        self.chat_preview.visible = True
        try:
            self.chat_preview.update()
        except Exception:
            pass

    async def do_chat_send(self) -> None:
        text = (self.chat_input.value or "").strip()
        image = getattr(self, "_chat_image", "")
        if not text and not image:
            return
        env = self.api.env if self.api else (self.sess or {}).get("env", "local")
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, db.add_chat_message, env, self.user, text, image)
        except Exception as ex:
            self.notify(f"发送失败：{ex}", type="negative")
            return
        self.chat_input.value = ""
        self._chat_image = ""
        self.chat_preview.visible = False
        try:
            self.chat_input.update()
            self.chat_preview.update()
        except Exception:
            pass
        await self.load_chat()


# ============================================================================
# Flet 入口：main
# ============================================================================
def main() -> None:
    async def app(page: ft.Page) -> None:
        ui = DeskUI(page)
        await ui.start()

    ft.run(main=app, assets_dir=str(store.DATA_DIR / "chat_images")
           if (store.DATA_DIR / "chat_images").exists() else None)
