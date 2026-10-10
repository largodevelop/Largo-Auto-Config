"""Frameless dark window for Largo Auto Config (Largo CRM design language)."""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog
from collections.abc import Callable
from pathlib import Path

import windnd
from PIL import Image, ImageDraw, ImageTk

from win_shell import hide_from_taskbar

# Design tokens (Largo CRM)
BG = "#0f0f0f"
BG_RAISED = "#1c1c1c"
BG_CARD = "#141415"
LINE = "#27272a"
TEXT = "#f4f4f5"
TEXT_SECONDARY = "#d4d4d8"
TEXT_DIM = "#a1a1aa"
TEXT_MUTED_SM = "#9a9aa2"
ACCENT = "#7fdaac"
DANGER = "#ff6b6b"
LAMP_ONLINE = "#5b9ee6"
LAMP_OFF = "#3a3a3a"
SWITCH_OFF = "#2a2a2a"
PRIMARY = "#b9b9bc"  # rgba(228,228,231,.8) on BG
PRIMARY_HOVER = "#d4d4d8"
PRIMARY_TEXT = "#18181b"
CLOSE_HOVER = "#c42b1c"

WIDTH = 420
HEIGHT = 434
PAD = 16
RADIUS = 8
RADIUS_SM = 6

ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets"
LOGO_ON = ASSETS / "brand_icon.png"
LOGO_OFF = ASSETS / "brand_off.png"
LOGO_SIZE = 20


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _blend(fg: str, bg: str, alpha: float) -> tuple[int, int, int]:
    f, b = _rgb(fg), _rgb(bg)
    return tuple(round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3))  # type: ignore[return-value]


def _finish(img: Image.Image, size: tuple[int, int]) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(img.resize(size, Image.Resampling.LANCZOS).convert("RGB"))


def _rounded(w: int, h: int, r: int, fill: str, outline: str | None = None, bg: str = BG) -> ImageTk.PhotoImage:
    """Anti-aliased rounded rectangle (supersampled) — Tk canvas can't do smooth corners."""
    s = 4
    img = Image.new("RGB", (w * s, h * s), bg)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w * s - 1, h * s - 1), radius=r * s, fill=outline or fill)
    if outline:
        d.rounded_rectangle((s, s, w * s - 1 - s, h * s - 1 - s), radius=(r - 1) * s, fill=fill)
    return _finish(img, (w, h))


def _switch(on: bool, bg: str) -> ImageTk.PhotoImage:
    w, h, s = 34, 16, 6
    img = Image.new("RGB", (w * s, h * s), bg)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w * s - 1, h * s - 1), radius=h * s // 2, fill=ACCENT if on else SWITCH_OFF)
    k, m = 12 * s, 2 * s
    x = (w * s - m - k) if on else m
    d.ellipse((x, m, x + k, m + k), fill="#ffffff" if on else "#c8c8c8")
    return _finish(img, (w, h))


def _lamp(color: str, bg: str) -> ImageTk.PhotoImage:
    size, s = 15, 6
    img = Image.new("RGB", (size * s, size * s), bg)
    d = ImageDraw.Draw(img)
    c = size * s / 2
    d.ellipse((c - 7 * s, c - 7 * s, c + 7 * s, c + 7 * s), fill=_blend(color, bg, 0.16))
    d.ellipse((c - 4.5 * s, c - 4.5 * s, c + 4.5 * s, c + 4.5 * s), fill=color)
    return _finish(img, (size, size))


def _logo(path: Path) -> ImageTk.PhotoImage | None:
    if not path.is_file():
        return None
    img = Image.open(path).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE), BG)
    canvas.alpha_composite(img)
    return ImageTk.PhotoImage(canvas.convert("RGB"))


class HoverTip:
    """Small delayed tooltip on hover."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 280) -> None:
        self._widget = widget
        self._text = text
        self._delay = delay_ms
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._hide()
        self._job = self._widget.after(self._delay, self._show)

    def _show(self) -> None:
        if self._tip is not None:
            return
        x = self._widget.winfo_rootx() + 8
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 6
        tip = tk.Toplevel(self._widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(tip, bg=LINE, padx=1, pady=1)
        frame.pack()
        tk.Label(
            frame,
            text=self._text,
            justify="left",
            bg="#151516",
            fg=TEXT_DIM,
            font=("Segoe UI", 8),
            padx=8,
            pady=6,
            wraplength=280,
        ).pack()
        self._tip = tip

    def _hide(self, _event=None) -> None:
        if self._job is not None:
            try:
                self._widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class _Card(tk.Canvas):
    """Labelled value card: UPPERCASE mono caption, value, status lamp."""

    def __init__(self, parent: tk.Widget, width: int, label: str, mono: str) -> None:
        super().__init__(parent, width=width, height=56, bg=BG, highlightthickness=0, bd=0)
        self._bg_img = _rounded(width, 56, RADIUS, BG_CARD, LINE)
        self._lamps = {c: _lamp(c, BG_CARD) for c in (LAMP_ONLINE, ACCENT, LAMP_OFF)}
        self.create_image(0, 0, image=self._bg_img, anchor="nw")
        self.create_text(14, 17, text=label.upper(), anchor="w", fill=TEXT_MUTED_SM, font=(mono, 8))
        self._lamp_item = self.create_image(14, 38, image=self._lamps[LAMP_OFF], anchor="w")
        self._value = self.create_text(34, 38, text="—", anchor="w", fill=TEXT, font=(mono, 11))

    def set(self, value: str, lamp: str) -> None:
        self.itemconfigure(self._value, text=value)
        self.itemconfigure(self._lamp_item, image=self._lamps[lamp])


class _Button(tk.Canvas):
    """Rounded button: 'primary' (light) or outlined ghost."""

    def __init__(self, parent: tk.Widget, text: str, command: Callable[[], None], font: tuple, primary: bool) -> None:
        w, h = tkfont.Font(font=font).measure(text) + 28, 28
        super().__init__(parent, width=w, height=h, bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        if primary:
            idle = _rounded(w, h, RADIUS_SM, PRIMARY)
            hover = _rounded(w, h, RADIUS_SM, PRIMARY_HOVER)
            self._fg = (PRIMARY_TEXT, PRIMARY_TEXT)
        else:
            idle = _rounded(w, h, RADIUS_SM, BG, LINE)
            hover = _rounded(w, h, RADIUS_SM, BG_RAISED, "#3f3f46")
            self._fg = (TEXT_DIM, TEXT)
        self._imgs = (idle, hover)
        self._bg_item = self.create_image(0, 0, image=idle, anchor="nw")
        self._label = self.create_text(w // 2, h // 2, text=text, fill=self._fg[0], font=font)
        self.bind("<Enter>", lambda _e: self._paint(1))
        self.bind("<Leave>", lambda _e: self._paint(0))
        self.bind("<Button-1>", lambda _e: command())

    def _paint(self, state: int) -> None:
        self.itemconfigure(self._bg_item, image=self._imgs[state])
        self.itemconfigure(self._label, fill=self._fg[state])


class _ToggleCard(tk.Canvas):
    """Card with a caption on the left and a pill switch on the right."""

    def __init__(self, parent: tk.Widget, width: int, label: str, command: Callable[[], None], sans: str) -> None:
        super().__init__(parent, width=width, height=44, bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        self._bg_img = _rounded(width, 44, RADIUS, BG_CARD, LINE)
        self._on = _switch(True, BG_CARD)
        self._off = _switch(False, BG_CARD)
        self.create_image(0, 0, image=self._bg_img, anchor="nw")
        self.create_text(14, 22, text=label, anchor="w", fill=TEXT_SECONDARY, font=(sans, 10))
        self._sw = self.create_image(width - 14, 22, image=self._on, anchor="e")
        self.bind("<Button-1>", lambda _e: command())

    def set(self, enabled: bool) -> None:
        self.itemconfigure(self._sw, image=self._on if enabled else self._off)


class _FileCard(tk.Canvas):
    """Card that shows a chosen file name (or a hint); click picks, right click clears."""

    def __init__(
        self,
        parent: tk.Widget,
        width: int,
        label: str,
        on_click: Callable[[], None],
        on_clear: Callable[[], None],
        sans: str,
        mono: str,
    ) -> None:
        super().__init__(parent, width=width, height=44, bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        self._idle = _rounded(width, 44, RADIUS, BG_CARD, LINE)
        self._hover = _rounded(width, 44, RADIUS, BG_CARD, "#3f3f46")
        self._bg_item = self.create_image(0, 0, image=self._idle, anchor="nw")
        self.create_text(14, 22, text=label, anchor="w", fill=TEXT_SECONDARY, font=(sans, 10))
        self._value = self.create_text(width - 14, 22, text="", anchor="e", fill=TEXT_MUTED_SM, font=(mono, 9))
        self.set("")
        self.bind("<Enter>", lambda _e: self.itemconfigure(self._bg_item, image=self._hover))
        self.bind("<Leave>", lambda _e: self.itemconfigure(self._bg_item, image=self._idle))
        self.bind("<Button-1>", lambda _e: on_click())
        self.bind("<Button-3>", lambda _e: on_clear())

    def set(self, name: str) -> None:
        if name:
            shown = name if len(name) <= 26 else name[:23] + "…"
            self.itemconfigure(self._value, text=shown, fill=ACCENT)
        else:
            self.itemconfigure(self._value, text="выбрать или перетащить", fill=TEXT_MUTED_SM)


class AppWindow:
    def __init__(
        self,
        on_toggle_auto: Callable[[bool], None],
        on_toggle_autostart: Callable[[bool], None],
        on_set_master: Callable[[], None],
        on_pick_autoexec: Callable[[str], None],
        on_clear_autoexec: Callable[[], None],
        on_apply: Callable[[], None],
        on_close: Callable[[], None],
        on_minimize: Callable[[], None],
    ) -> None:
        self._on_toggle_auto = on_toggle_auto
        self._on_toggle_autostart = on_toggle_autostart
        self._on_set_master = on_set_master
        self._on_pick_autoexec = on_pick_autoexec
        self._on_clear_autoexec = on_clear_autoexec
        self._on_apply = on_apply
        self._on_close = on_close
        self._on_minimize = on_minimize
        self._auto = True
        self._autostart = False
        self._drag_x = 0
        self._drag_y = 0

        self.root = tk.Tk()
        self.root.title("Largo Auto Config")
        self.root.configure(bg=BG)
        self.root.geometry(f"{WIDTH}x{HEIGHT}")
        self.root.resizable(False, False)
        self.root.overrideredirect(True)
        self.root.after(50, lambda: hide_from_taskbar(self.root))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        families = set(tkfont.families(self.root))
        self._sans = "Inter" if "Inter" in families else "Segoe UI"
        self._mono = "JetBrains Mono" if "JetBrains Mono" in families else "Consolas"

        self._logo_on = _logo(LOGO_ON)
        self._logo_off = _logo(LOGO_OFF) or self._logo_on

        shell = tk.Frame(self.root, bg=BG, highlightthickness=1, highlightbackground=LINE)
        shell.pack(fill="both", expand=True)
        self._build_titlebar(shell)
        self._build_body(shell)
        self.set_auto(True)
        try:
            windnd.hook_dropfiles(self.root, func=self._on_drop_files)
        except Exception:
            pass

    # ---- layout -----------------------------------------------------------

    def _build_titlebar(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=BG, height=40)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._logo_label = tk.Label(bar, bg=BG, bd=0, image=self._logo_on)
        self._logo_label.pack(side="left", padx=(PAD, 8))
        title = tk.Label(bar, text="// largo auto config", bg=BG, fg=TEXT_MUTED_SM, font=(self._mono, 9), anchor="w")
        title.pack(side="left")

        for w in (bar, self._logo_label, title):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        controls = tk.Frame(bar, bg=BG)
        controls.pack(side="right")
        self._mk_win_btn(controls, "—", self._on_minimize).pack(side="left")
        self._mk_win_btn(controls, "×", self._on_close, close=True).pack(side="left")
        tk.Frame(parent, bg=LINE, height=1).pack(fill="x")

    def _mk_win_btn(self, parent: tk.Frame, text: str, command, close: bool = False) -> tk.Label:
        btn = tk.Label(parent, text=text, bg=BG, fg=TEXT_DIM, width=4, font=("Segoe UI", 11), cursor="hand2")
        btn.bind("<Enter>", lambda _e: btn.configure(bg=CLOSE_HOVER if close else BG_RAISED, fg="#ffffff"))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=BG, fg=TEXT_DIM))
        btn.bind("<Button-1>", lambda _e: command())
        return btn

    def _build_body(self, parent: tk.Frame) -> None:
        # status goes first with side="bottom" so it keeps its space
        self.status_var = tk.StringVar(value="")
        self._status = tk.Label(
            parent, textvariable=self.status_var, bg=BG, fg=TEXT_MUTED_SM, font=(self._mono, 9), anchor="w"
        )
        self._status.pack(side="bottom", fill="x", padx=PAD, pady=(0, 12))

        body = tk.Frame(parent, bg=BG)
        body.pack(fill="both", expand=True, padx=PAD, pady=(14, 0))
        inner_w = WIDTH - 2 * PAD - 2

        self._account_card = _Card(body, inner_w, "Аккаунт", self._mono)
        self._account_card.pack(pady=(0, 8))
        self._master_card = _Card(body, inner_w, "Главный аккаунт", self._mono)
        self._master_card.pack(pady=(0, 8))
        self._toggle = _ToggleCard(body, inner_w, "Автозамена", self._toggle_auto, self._sans)
        self._toggle.pack(pady=(0, 8))
        self._autostart_toggle = _ToggleCard(
            body, inner_w, "Автозапуск с Windows", self._toggle_autostart, self._sans
        )
        self._autostart_toggle.pack(pady=(0, 8))
        self._autoexec_card = _FileCard(
            body, inner_w, "Autoexec CFG", self._pick_autoexec, self._on_clear_autoexec, self._sans, self._mono
        )
        self._autoexec_card.pack(pady=(0, 14))
        HoverTip(
            self._autoexec_card,
            "Нажми или перетащи сюда свой autoexec .cfg. Он будет записываться в Dota "
            "при каждом запуске приложения и смене аккаунта. Правая кнопка мыши — убрать.",
        )

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x")
        master_btn = _Button(actions, "Сделать главным", self._on_set_master, (self._sans, 9, "bold"), primary=True)
        master_btn.pack(side="left")
        HoverTip(
            master_btn,
            "Войди в аккаунт с нужными хоткеями и настройками и нажми сюда. "
            "На остальных аккаунтах они подставятся сами.",
        )
        _Button(actions, "Применить сейчас", self._on_apply, (self._sans, 9), primary=False).pack(
            side="left", padx=(8, 0)
        )

    # ---- drag / actions ----------------------------------------------------

    def _start_drag(self, event) -> None:
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _on_drag(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    def _toggle_auto(self) -> None:
        self.set_auto(not self._auto)
        self._on_toggle_auto(self._auto)

    def _pick_autoexec(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Выбери autoexec CFG", filetypes=[("Config", "*.cfg"), ("Все файлы", "*.*")]
        )
        if path:
            self._on_pick_autoexec(path)

    def _on_drop_files(self, files) -> None:
        for raw in files or []:
            if isinstance(raw, bytes):
                for enc in ("utf-8", "mbcs", "cp1251"):
                    try:
                        raw = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    continue
            path = Path(str(raw))
            if path.is_file():
                self._on_pick_autoexec(str(path))
                return
        self.set_status("перетащи файл .cfg", ok=False)

    def _toggle_autostart(self) -> None:
        self.set_autostart(not self._autostart)
        self._on_toggle_autostart(self._autostart)

    # ---- API used by main --------------------------------------------------

    def set_account(self, name: str, account_id: str = "") -> None:
        self._account_card.set(name, LAMP_ONLINE if account_id else LAMP_OFF)

    def set_master(self, name: str = "") -> None:
        self._master_card.set(name or "не выбран", ACCENT if name else LAMP_OFF)

    def set_status(self, text: str, ok: bool = True) -> None:
        self.status_var.set(f"// {text}" if text else "")
        self._status.configure(fg=TEXT_MUTED_SM if ok else DANGER)

    def set_auto(self, enabled: bool) -> None:
        self._auto = enabled
        self._toggle.set(enabled)
        self._logo_label.configure(image=self._logo_on if enabled else self._logo_off)

    def set_autostart(self, enabled: bool) -> None:
        self._autostart = enabled
        self._autostart_toggle.set(enabled)

    def set_autoexec(self, name: str) -> None:
        self._autoexec_card.set(name)

    def show(self) -> None:
        self.root.deiconify()
        hide_from_taskbar(self.root)
        self.root.lift()
        self.root.focus_force()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(120, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass

    def hide(self) -> None:
        self.root.withdraw()

    def run(self) -> None:
        self.root.mainloop()

    def after(self, ms: int, cb: Callable[[], None]) -> None:
        self.root.after(ms, cb)

    def destroy(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass
