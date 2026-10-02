"""Tiny Largo-styled frameless window for Largo Auto Config."""

from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog

import windnd
from PIL import Image, ImageEnhance, ImageTk

from win_shell import hide_from_taskbar


BG = "#121212"
BG_RAISED = "#1C1C1C"
BG_INPUT = "#262626"
LINE = "#2A2A2A"
TEXT = "#ECECEC"
TEXT_DIM = "#B0B0B0"
TEXT_FAINT = "#8A8A8A"
BRAND = "#B794F6"
OK = "#C4B5FD"
BTN_SOLID = "#C8C8CC"
BTN_TEXT = "#18181B"
DANGER = "#FF6B6B"
CLOSE_HOVER = "#C42B1C"
ICON_WHITE = "#FFFFFF"
ICON_DIM = "#C4C4C4"

ROOT = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
BRAND_PATH = ROOT / "assets" / "brand_icon.png"
BRAND_PATH_FALLBACK = ROOT / "assets" / "brand.png"
BRAND_FRAMES_DIR = ROOT / "assets" / "brand_frames"
FALLBACK_BRAND = Path(r"D:\funpayautobot\docs\brand\brand.png")
BRAND_SIZE = 22

SETTINGS_HINT = (
    "Перенеси сюда папку 570 — настройки, которые ты хочешь.\n\n"
    "Обычно лежит здесь:\n"
    "C:\\Program Files (x86)\\Steam\\userdata\\<AccountID>\\570\n\n"
    "AccountID — числовая папка твоего Steam-аккаунта."
)


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
        x = self._widget.winfo_rootx() + 12
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
            bg=BG_RAISED,
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


class AppWindow:
    def __init__(
        self,
        on_toggle_auto: Callable[[bool], None],
        on_copy: Callable[[], None],
        on_paths_changed: Callable[[str, str], None],
        on_close: Callable[[], None],
        on_minimize: Callable[[], None],
    ) -> None:
        self._on_toggle_auto = on_toggle_auto
        self._on_copy = on_copy
        self._on_paths_changed = on_paths_changed
        self._on_close = on_close
        self._on_minimize = on_minimize
        self._auto = True
        self._drag_x = 0
        self._drag_y = 0
        self._cfg_path = ""
        self._settings_path = ""
        self._brand_photo: ImageTk.PhotoImage | None = None
        self._brand_frames: list[ImageTk.PhotoImage] = []
        self._brand_offline: ImageTk.PhotoImage | None = None
        self._pulse_i = 0
        self._pulse_job: str | None = None
        self._brand_label: tk.Label | None = None
        self._shake_job: str | None = None

        self.root = tk.Tk()
        self.root.title("Largo Auto Config")
        self.root.configure(bg=BG)
        self.root.geometry("400x248")
        self.root.minsize(380, 230)
        self.root.resizable(False, False)
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass
        self.root.after(50, lambda: hide_from_taskbar(self.root))

        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)

        shell = tk.Frame(self.root, bg=BG, highlightthickness=1, highlightbackground=LINE)
        shell.pack(fill="both", expand=True)

        self._build_titlebar(shell)
        self._build_body(shell)
        self._build_status(shell)
        self._build_note(shell)
        self._build_actions(shell)
        self._enable_drop()
        self._apply_brand_online_state()

    def _brand_image_path(self) -> Path | None:
        for candidate in (BRAND_PATH, BRAND_PATH_FALLBACK, FALLBACK_BRAND):
            if candidate.is_file():
                return candidate
        return None

    def _load_css_brand_frames(self) -> list[ImageTk.PhotoImage]:
        frames: list[ImageTk.PhotoImage] = []
        if not BRAND_FRAMES_DIR.is_dir():
            return frames
        for path in sorted(BRAND_FRAMES_DIR.glob("frame_*.png")):
            img = Image.open(path).convert("RGBA")
            bg = Image.new("RGBA", img.size, (18, 18, 18, 255))
            bg.alpha_composite(img)
            frames.append(ImageTk.PhotoImage(bg.convert("RGB")))
        return frames

    def _make_offline_brand(self, size: tuple[int, int] = (64, 64)) -> ImageTk.PhotoImage | None:
        """Match .largo-brand-mark.is-offline: grayscale + dim, no glow."""
        path = self._brand_image_path()
        if not path:
            return None
        img = Image.open(path).convert("RGBA")
        # Fit mark like titlebar 22px inside 64px canvas
        mark = BRAND_SIZE
        glyph = img.resize((mark, mark), Image.Resampling.LANCZOS)
        # grayscale(1) brightness(0.45) contrast(0.9) opacity 0.72
        rgb = ImageEnhance.Color(glyph.convert("RGB")).enhance(0.0)
        rgb = ImageEnhance.Brightness(rgb).enhance(0.45)
        rgb = ImageEnhance.Contrast(rgb).enhance(0.9)
        alpha = glyph.split()[-1].point(lambda a: int(a * 0.72))
        offline = Image.merge("RGBA", (*rgb.split(), alpha))
        canvas = Image.new("RGBA", size, (18, 18, 18, 255))
        ox = (size[0] - mark) // 2
        oy = (size[1] - mark) // 2
        canvas.alpha_composite(offline, (ox, oy))
        return ImageTk.PhotoImage(canvas.convert("RGB"))

    def _fallback_static_brand(self) -> list[ImageTk.PhotoImage]:
        path = self._brand_image_path()
        if not path:
            return []
        img = Image.open(path).convert("RGBA").resize((BRAND_SIZE, BRAND_SIZE), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (64, 64), (18, 18, 18, 255))
        canvas.alpha_composite(img, ((64 - BRAND_SIZE) // 2, (64 - BRAND_SIZE) // 2))
        return [ImageTk.PhotoImage(canvas.convert("RGB"))]

    def _build_brand_frames(self) -> None:
        frames = self._load_css_brand_frames()
        if not frames:
            frames = self._fallback_static_brand()
        self._brand_frames = frames
        self._brand_offline = self._make_offline_brand()
        if self._brand_offline is None and frames:
            self._brand_offline = frames[0]

    def _stop_brand_pulse(self) -> None:
        if self._pulse_job:
            try:
                self.root.after_cancel(self._pulse_job)
            except Exception:
                pass
            self._pulse_job = None

    def _start_brand_pulse(self) -> None:
        self._stop_brand_pulse()
        if not self._auto or not self._brand_label or not self._brand_frames:
            return

        def tick() -> None:
            if not self._auto or not self._brand_label or not self._brand_frames:
                self._pulse_job = None
                return
            self._brand_photo = self._brand_frames[self._pulse_i % len(self._brand_frames)]
            self._brand_label.configure(image=self._brand_photo)
            self._pulse_i += 1
            delay = 100 if len(self._brand_frames) > 1 else 3600
            self._pulse_job = self.root.after(delay, tick)

        tick()

    def _apply_brand_online_state(self) -> None:
        """Online = glowing pulse; offline = gray static mark (Rent Bot titlebar)."""
        if not self._brand_label:
            return
        if self._auto:
            self._start_brand_pulse()
        else:
            self._stop_brand_pulse()
            photo = self._brand_offline or (self._brand_frames[0] if self._brand_frames else None)
            if photo:
                self._brand_photo = photo
                self._brand_label.configure(image=self._brand_photo)

    def _enable_drop(self) -> None:
        try:
            windnd.hook_dropfiles(self.root, func=self._on_drop_files)
        except Exception:
            pass

    def _decode_drop_path(self, raw) -> str:
        if isinstance(raw, bytes):
            for enc in ("utf-8", "mbcs", "cp1251", "latin-1"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="ignore")
        return str(raw)

    def _on_drop_files(self, files) -> None:
        paths = [Path(self._decode_drop_path(f)) for f in (files or [])]
        got_cfg = False
        got_settings = False
        for path in paths:
            if not path.exists():
                continue
            if path.is_dir():
                self._settings_path = str(path)
                got_settings = True
            elif path.is_file() and path.suffix.lower() == ".cfg":
                self._cfg_path = str(path)
                got_cfg = True
        self._refresh_icon_state()
        if got_cfg or got_settings:
            self._on_paths_changed(self._cfg_path, self._settings_path)
            if got_cfg and got_settings:
                self.set_status("CFG и папка приняты")
            elif got_cfg:
                self.set_status("CFG принят")
            else:
                self.set_status("папка настроек принята")
        else:
            self.set_status("перетащи .cfg или папку", ok=False)

    def _build_titlebar(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=BG, height=40)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        drag = tk.Frame(bar, bg=BG)
        drag.pack(side="left", fill="both", expand=True)

        head = tk.Frame(drag, bg=BG)
        head.pack(side="left", padx=(8, 0), pady=4)

        self._build_brand_frames()
        self._brand_label = tk.Label(head, bg=BG, bd=0, highlightthickness=0)
        self._brand_label.pack(side="left")
        if self._brand_frames:
            self._brand_photo = self._brand_frames[0]
            self._brand_label.configure(image=self._brand_photo)

        title = tk.Label(
            head,
            text="LARGO AUTO CONFIG",
            bg=BG,
            fg=TEXT_DIM,
            font=("Segoe UI Semibold", 8),
            anchor="w",
        )
        title.pack(side="left", padx=(2, 0))

        for w in (drag, head, title, bar, self._brand_label):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        controls = tk.Frame(bar, bg=BG)
        controls.pack(side="right")
        self._mk_win_btn(controls, "—", self._on_minimize).pack(side="left")
        self._mk_win_btn(controls, "×", self._handle_close, close=True).pack(side="left")
        tk.Frame(parent, bg=LINE, height=1).pack(fill="x")

    def _mk_win_btn(self, parent: tk.Frame, text: str, command, close: bool = False) -> tk.Label:
        btn = tk.Label(
            parent,
            text=text,
            bg=BG,
            fg="#CFCFCF",
            width=4,
            font=("Segoe UI", 11),
            cursor="hand2",
        )

        def enter(_e, b=btn, c=close):
            b.configure(bg=CLOSE_HOVER if c else "#2A2A2A", fg="#FFFFFF")

        def leave(_e, b=btn):
            b.configure(bg=BG, fg="#CFCFCF")

        btn.bind("<Enter>", enter)
        btn.bind("<Leave>", leave)
        btn.bind("<Button-1>", lambda _e: command())
        return btn

    def _build_body(self, parent: tk.Frame) -> None:
        body = tk.Frame(parent, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 6))

        row = tk.Frame(body, bg=BG)
        row.pack(fill="x")

        left = tk.Frame(row, bg=BG)
        left.pack(side="left", fill="x", expand=True)

        tk.Label(left, text="Аккаунт", bg=BG, fg=TEXT_FAINT, font=("Segoe UI", 8), anchor="w").pack(fill="x")
        self.account_var = tk.StringVar(value="—")
        tk.Label(
            left,
            textvariable=self.account_var,
            bg=BG,
            fg=TEXT,
            font=("Consolas", 10),
            anchor="w",
        ).pack(fill="x")

        icons = tk.Frame(row, bg=BG)
        icons.pack(side="right")

        self._cfg_btn = self._icon_button(icons, kind="cfg", command=self._pick_cfg, caption="CFG")
        self._cfg_btn.pack(side="left", padx=(0, 10))
        self._settings_btn = self._icon_button(
            icons, kind="folder", command=self._pick_settings, caption="Настройки", help_text=SETTINGS_HINT
        )
        self._settings_btn.pack(side="left")

    def _icon_button(
        self,
        parent: tk.Frame,
        kind: str,
        command,
        caption: str,
        help_text: str = "",
    ) -> tk.Frame:
        wrap = tk.Frame(parent, bg=BG)
        chip = tk.Frame(wrap, bg=BG, highlightthickness=1, highlightbackground=LINE, cursor="hand2")
        chip.pack()
        canvas = tk.Canvas(chip, width=22, height=22, bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        canvas.pack(padx=5, pady=5)
        if kind == "cfg":
            self._draw_cfg_icon(canvas)
        else:
            self._draw_folder_icon(canvas)

        cap_row = tk.Frame(wrap, bg=BG)
        cap_row.pack(pady=(3, 0))
        label = tk.Label(cap_row, text=caption, bg=BG, fg=TEXT_FAINT, font=("Segoe UI", 7))
        label.pack(side="left")
        if help_text:
            q = tk.Label(
                cap_row,
                text="?",
                bg=BG,
                fg=TEXT_FAINT,
                font=("Segoe UI", 7),
                cursor="question_arrow",
                padx=3,
            )
            q.pack(side="left")
            HoverTip(q, help_text)

        def enter(_e):
            chip.configure(highlightbackground="#3F3F46")

        def leave(_e):
            selected = bool(self._cfg_path if kind == "cfg" else self._settings_path)
            chip.configure(highlightbackground=BRAND if selected else LINE)

        for w in (chip, canvas, label, wrap):
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)
            w.bind("<Button-1>", lambda _e: command())
        wrap._chip = chip  # type: ignore[attr-defined]
        return wrap

    def _draw_cfg_icon(self, c: tk.Canvas) -> None:
        # Minimal document: outline + two lines
        c.create_rectangle(6, 3, 16, 19, outline=ICON_DIM, width=1)
        c.create_line(8, 8, 14, 8, fill=ICON_DIM, width=1)
        c.create_line(8, 12, 14, 12, fill=ICON_DIM, width=1)

    def _draw_folder_icon(self, c: tk.Canvas) -> None:
        # Minimal folder outline
        c.create_line(3, 8, 8, 8, 10, 5, 19, 5, fill=ICON_DIM, width=1)
        c.create_rectangle(3, 8, 19, 18, outline=ICON_DIM, width=1)

    def _refresh_icon_state(self) -> None:
        self._cfg_btn._chip.configure(highlightbackground=BRAND if self._cfg_path else LINE)  # type: ignore
        self._settings_btn._chip.configure(highlightbackground=BRAND if self._settings_path else LINE)  # type: ignore

    def _build_status(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=16, pady=(8, 4))

        self._status_chip = tk.Frame(wrap, bg=BG_RAISED, highlightthickness=1, highlightbackground=LINE)
        self._status_chip.pack(fill="x")

        inner = tk.Frame(self._status_chip, bg=BG_RAISED)
        inner.pack(fill="x", padx=10, pady=6)

        tk.Label(inner, text="Статус", bg=BG_RAISED, fg=TEXT_FAINT, font=("Segoe UI", 8)).pack(side="left")

        self.status_var = tk.StringVar(value="")
        tk.Label(
            inner,
            textvariable=self.status_var,
            bg=BG_RAISED,
            fg=TEXT_DIM,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def _build_note(self, parent: tk.Frame) -> None:
        self.note_var = tk.StringVar(value="")
        self._note = tk.Label(
            parent,
            textvariable=self.note_var,
            bg=BG,
            fg=DANGER,
            font=("Segoe UI", 8),
            anchor="w",
            padx=16,
        )
        self._note.pack(fill="x", pady=(0, 2))

    def _build_actions(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=16, pady=(0, 14))

        self._auto_btn = tk.Button(
            bar,
            text="Автозамена · вкл",
            command=self._toggle_auto,
            bg=BTN_SOLID,
            fg=BTN_TEXT,
            activebackground="#E4E4E7",
            activeforeground=BTN_TEXT,
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self._auto_btn.pack(side="left")

        outline = dict(
            bg=BG,
            fg=TEXT_DIM,
            activebackground="#E4E4E7",
            activeforeground=BTN_TEXT,
            relief="solid",
            bd=1,
            highlightthickness=0,
            padx=10,
            pady=3,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        tk.Button(bar, text="Скопировать настройки с аккаунта", command=self._on_copy, **outline).pack(
            side="left", padx=(8, 0)
        )
    def _start_drag(self, event) -> None:
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _on_drag(self, event) -> None:
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _pick_cfg(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Выбери CFG",
            filetypes=[("Config", "*.cfg"), ("Все файлы", "*.*")],
        )
        if path:
            self._cfg_path = path
            self._refresh_icon_state()
            self._on_paths_changed(self._cfg_path, self._settings_path)
            self.set_status("CFG выбран")

    def _pick_settings(self) -> None:
        path = filedialog.askdirectory(
            parent=self.root,
            title="Выбери папку 570 (Steam\\userdata\\<id>\\570)",
        )
        if path:
            self._settings_path = path
            self._refresh_icon_state()
            self._on_paths_changed(self._cfg_path, self._settings_path)
            self.set_status("папка настроек выбрана")

    def _toggle_auto(self) -> None:
        self._auto = not self._auto
        self.set_auto(self._auto)
        self._on_toggle_auto(self._auto)

    def _handle_close(self) -> None:
        self._on_close()

    def get_cfg_path(self) -> str:
        return self._cfg_path.strip()

    def set_account(self, name: str, account_id: str = "") -> None:
        self.account_var.set(name)

    def set_paths(self, cfg: str = "", settings: str = "") -> None:
        self._cfg_path = cfg or ""
        self._settings_path = settings or ""
        self._refresh_icon_state()

    def set_status(self, text: str, ok: bool = True) -> None:
        self.status_var.set(text)

    def set_note(self, text: str = "") -> None:
        self.note_var.set(text)

    def set_auto(self, enabled: bool) -> None:
        self._auto = enabled
        if enabled:
            self._auto_btn.configure(text="Автозамена · вкл", bg=BTN_SOLID, fg=BTN_TEXT, relief="flat", bd=0)
        else:
            self._auto_btn.configure(text="Автозамена · выкл", bg=BG, fg=TEXT_DIM, relief="solid", bd=1)
        self._apply_brand_online_state()

    def warn_relaunch_dota(self) -> None:
        self.set_note("Нужно перезайти в Dota для применения настроек")
        self.set_status("применено · перезайди в Dota")
        self._shake_window()

    def _shake_window(self) -> None:
        if self._shake_job:
            try:
                self.root.after_cancel(self._shake_job)
            except Exception:
                pass
        offsets = [0, -3, 3, -2, 2, -1, 1, 0]
        base_x = self.root.winfo_x()
        base_y = self.root.winfo_y()

        def step(i: int = 0) -> None:
            if i >= len(offsets):
                self.root.geometry(f"+{base_x}+{base_y}")
                self._shake_job = None
                return
            self.root.geometry(f"+{base_x + offsets[i]}+{base_y}")
            self._shake_job = self.root.after(40, lambda: step(i + 1))

        step(0)

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
