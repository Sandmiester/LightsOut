import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import os
import threading
import ctypes
import ctypes.wintypes as wt
import math

if sys.platform == "win32":
    import winreg

# ─── Platform ─────────────────────────────────────────────────────────────────
IS_WIN = sys.platform == "win32"

# ─── Win32 types for 64-bit compatibility ─────────────────────────────────────
LRESULT = ctypes.c_ssize_t
WPARAM  = ctypes.c_size_t
LPARAM  = ctypes.c_ssize_t

# Bind Win32 functions globally to ensure types stick
_user32 = ctypes.windll.user32
_DefWindowProcW = _user32.DefWindowProcW
_DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, WPARAM, LPARAM]
_DefWindowProcW.restype  = LRESULT

# ─── Win32 constants ───────────────────────────────────────────────────────────
WM_APP           = 0x8000
TRAY_MSG         = WM_APP + 1
NIM_ADD          = 0
NIM_MODIFY       = 1
NIM_DELETE       = 2
NIF_MESSAGE      = 0x1
NIF_ICON         = 0x2
NIF_TIP          = 0x4
WM_LBUTTONDBLCLK = 0x203
WM_RBUTTONUP     = 0x205
WM_DESTROY       = 0x02
CS_HREDRAW       = 0x0002
CS_VREDRAW       = 0x0001
IDI_APPLICATION  = 32512
IDC_ARROW        = 32512
WS_OVERLAPPED    = 0
COLOR_WINDOW     = 5
HWND_MESSAGE     = ctypes.cast(ctypes.c_void_p(-3), wt.HWND)


# ─── Shell_NotifyIcon structure ────────────────────────────────────────────────
class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize",           ctypes.c_uint32),
        ("hWnd",             wt.HWND),
        ("uID",              ctypes.c_uint32),
        ("uFlags",           ctypes.c_uint32),
        ("uCallbackMessage", ctypes.c_uint32),
        ("hIcon",            wt.HICON),
        ("szTip",            ctypes.c_wchar * 128),
        ("dwState",          ctypes.c_uint32),
        ("dwStateMask",      ctypes.c_uint32),
        ("szInfo",           ctypes.c_wchar * 256),
        ("uVersion",         ctypes.c_uint32),
        ("szInfoTitle",      ctypes.c_wchar * 64),
        ("dwInfoFlags",      ctypes.c_uint32),
    ]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize",        ctypes.c_uint),
        ("style",         ctypes.c_uint),
        ("lpfnWndProc",   ctypes.c_void_p),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     wt.HINSTANCE),
        ("hIcon",         wt.HICON),
        ("hCursor",       wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName",  wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
        ("hIconSm",       wt.HICON),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize",          ctypes.c_uint32),
        ("biWidth",         ctypes.c_int32),
        ("biHeight",        ctypes.c_int32),
        ("biPlanes",        ctypes.c_uint16),
        ("biBitCount",      ctypes.c_uint16),
        ("biCompression",   ctypes.c_uint32),
        ("biSizeImage",     ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed",       ctypes.c_uint32),
        ("biClrImportant",  ctypes.c_uint32),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon",    ctypes.c_bool),
        ("xHotspot", ctypes.c_uint32),
        ("yHotspot", ctypes.c_uint32),
        ("hbmMask",  wt.HBITMAP),
        ("hbmColor", wt.HBITMAP),
    ]


# ─── Native tray implementation ────────────────────────────────────────────────
class Win32Tray:
    """
    System tray icon using Shell_NotifyIcon (ctypes) for the icon,
    and a Tkinter Menu for the popup — guaranteed to render correctly.
    The tray thread only handles the icon and click detection;
    all menu display happens on the Tk main thread.
    """

    PRESETS = [
        ("5 min",   0,  5),
        ("15 min",  0, 15),
        ("30 min",  0, 30),
        ("1 hour",  1,  0),
        ("2 hours", 2,  0),
    ]

    def __init__(self, on_action, tk_root):
        self._on_action = on_action
        self._root      = tk_root
        self._hwnd      = None
        self._nid       = None
        self._menu      = None          # built lazily on Tk thread
        self._ready     = threading.Event()
        self._thread    = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)

    # ── Tray icon thread ─────────────────────────────────────────────────────
    def _run(self):
        user32   = ctypes.windll.user32
        shell32  = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        hinstance  = kernel32.GetModuleHandleW(None)
        class_name = "LightsOutTray"

        WNDPROC  = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, ctypes.c_uint, WPARAM, LPARAM)
        wnd_proc = WNDPROC(self._wnd_proc)   # keep reference alive

        wc = WNDCLASSEX()
        wc.cbSize        = ctypes.sizeof(WNDCLASSEX)
        wc.style         = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc   = ctypes.cast(wnd_proc, ctypes.c_void_p)
        wc.hInstance     = hinstance
        wc.hIcon         = user32.LoadIconW(None, IDI_APPLICATION)
        wc.hCursor       = user32.LoadCursorW(None, IDC_ARROW)
        wc.hbrBackground = COLOR_WINDOW + 1
        wc.lpszClassName = class_name

        user32.RegisterClassExW(ctypes.byref(wc))
        self._hwnd = user32.CreateWindowExW(
            0, class_name, "Tray", WS_OVERLAPPED,
            0, 0, 0, 0, HWND_MESSAGE, None, hinstance, None)

        nid = NOTIFYICONDATA()
        nid.cbSize           = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd             = self._hwnd
        nid.uID              = 1
        nid.uFlags           = NIF_ICON | NIF_MESSAGE | NIF_TIP
        nid.uCallbackMessage = TRAY_MSG
        nid.hIcon            = self._make_power_icon()
        nid.szTip            = "Lights Out"
        self._nid = nid

        # Retry Shell_NotifyIconW until the shell is ready (returns 1 on success).
        # This handles the case where the app starts before the tray shell is
        # fully initialised on Windows boot.
        for _ in range(30):
            if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                break
            import time
            time.sleep(1)
        self._ready.set()

        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def _make_power_icon(self):
        """Draw a 32x32 power-button HICON. No external libs required."""
        SIZE = 32
        buf  = [0x0F0F1A00] * (SIZE * SIZE)   # flat BGRA buffer

        BG  = (0x1a, 0x1a, 0x2e, 255)
        RED = (0xe9, 0x45, 0x60, 255)
        cx  = cy = SIZE / 2

        def set_px(x, y, color):
            if 0 <= x < SIZE and 0 <= y < SIZE:
                b, g, r, a = color[2], color[1], color[0], color[3]
                buf[y * SIZE + x] = (a << 24) | (r << 16) | (g << 8) | b

        def draw_aa_circle(radius, thickness, color, gap_deg=60):
            half_gap = gap_deg / 2
            inner, outer = radius - thickness / 2, radius + thickness / 2
            for y in range(SIZE):
                for x in range(SIZE):
                    dx, dy = x - cx, y - cy
                    dist = math.hypot(dx, dy)
                    if inner - 1 < dist < outer + 1:
                        coverage = max(0.0, min(dist - (inner - 1), 1.0, (outer + 1) - dist))
                        angle = math.degrees(math.atan2(dx, -dy)) % 360
                        if angle < half_gap or angle > 360 - half_gap:
                            continue
                        if coverage > 0:
                            set_px(x, y, (*color[:3], int(color[3] * coverage)))

        def draw_aa_line(x1, y1, x2, y2, thickness, color):
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy) or 1
            half   = thickness / 2
            ll     = length * length
            for y in range(SIZE):
                for x in range(SIZE):
                    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / ll))
                    dist = math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))
                    if dist < half + 1:
                        coverage = max(0.0, min(1.0, (half + 1) - dist))
                        set_px(x, y, (*color[:3], int(color[3] * coverage)))

        # Background circle
        for y in range(SIZE):
            for x in range(SIZE):
                if math.hypot(x - cx, y - cy) < cx - 0.5:
                    set_px(x, y, BG)

        draw_aa_circle(radius=11, thickness=3.0, color=RED, gap_deg=70)
        draw_aa_line(cx, cy - 14, cx, cy - 7, thickness=3.0, color=RED)

        # Flatten to bytes efficiently
        pixel_bytes = bytearray(SIZE * SIZE * 4)
        for i, px in enumerate(buf):
            pixel_bytes[i*4:i*4+4] = px.to_bytes(4, "little")

        user32 = ctypes.windll.user32
        gdi32  = ctypes.windll.gdi32
        hdc    = user32.GetDC(None)

        bih = BITMAPINFOHEADER()
        bih.biSize     = ctypes.sizeof(BITMAPINFOHEADER)
        bih.biWidth    = SIZE
        bih.biHeight   = -SIZE   # top-down
        bih.biPlanes   = 1
        bih.biBitCount = 32

        hbm_color = gdi32.CreateDIBitmap(
            hdc, ctypes.byref(bih), 0x4,
            bytes(pixel_bytes), ctypes.byref(bih), 0)

        mask_bytes = bytes(SIZE * SIZE // 8)
        hbm_mask   = gdi32.CreateBitmap(SIZE, SIZE, 1, 1, mask_bytes)

        ii          = ICONINFO()
        ii.fIcon    = True
        ii.hbmMask  = hbm_mask
        ii.hbmColor = hbm_color
        hicon = user32.CreateIconIndirect(ctypes.byref(ii))

        gdi32.DeleteObject(hbm_color)
        gdi32.DeleteObject(hbm_mask)
        user32.ReleaseDC(None, hdc)
        return hicon

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        # Python 3.14 delivers raw ints that can exceed c_ssize_t range — mask them.
        wparam = ctypes.c_size_t(wparam).value
        lparam = ctypes.c_ssize_t(ctypes.c_size_t(lparam).value).value
        if msg == TRAY_MSG:
            if lparam in (WM_RBUTTONUP, WM_LBUTTONDBLCLK):
                self._root.after(0, self._show_tk_menu)
            return 0
        if msg == WM_DESTROY:
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        return _DefWindowProcW(hwnd, msg, wparam, lparam)

    # ── Menu (runs on Tk main thread) ────────────────────────────────────────
    def _build_menu(self):
        """Build the tk.Menu once and reuse it."""
        root = self._root
        m = tk.Menu(root, tearoff=0)

        sd_sub = tk.Menu(m, tearoff=0)
        rs_sub = tk.Menu(m, tearoff=0)
        sl_sub = tk.Menu(m, tearoff=0)

        for label, h, mins in self.PRESETS:
            total = h * 60 + mins
            for sub, action in ((sd_sub, "shutdown"), (rs_sub, "restart"), (sl_sub, "sleep")):
                sub.add_command(label=label,
                                command=lambda t=total, a=action: self._on_action(a, t))

        m.add_cascade(label="Shutdown", menu=sd_sub)
        m.add_cascade(label="Restart",  menu=rs_sub)
        m.add_cascade(label="Sleep",    menu=sl_sub)
        m.add_separator()
        m.add_command(label="Cancel scheduled",
                      command=lambda: self._on_action("cancel", None))
        m.add_separator()
        m.add_command(label="Open", command=lambda: self._on_action("open", None))
        m.add_command(label="Quit", command=lambda: self._on_action("quit", None))
        return m

    def _show_tk_menu(self):
        if self._menu is None:
            self._menu = self._build_menu()
        # Get cursor position and show the Tk popup there
        x = self._root.winfo_pointerx()
        y = self._root.winfo_pointery()
        try:
            self._menu.tk_popup(x, y)
        finally:
            self._menu.grab_release()

    # ── Public API ───────────────────────────────────────────────────────────
    def set_tooltip(self, text):
        if self._nid and self._hwnd:
            self._nid.szTip  = text[:127]
            self._nid.uFlags = NIF_TIP
            ctypes.windll.shell32.Shell_NotifyIconW(
                NIM_MODIFY, ctypes.byref(self._nid))

    def remove(self):
        if self._nid and self._hwnd:
            ctypes.windll.shell32.Shell_NotifyIconW(
                NIM_DELETE, ctypes.byref(self._nid))
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)


# ─── Platform helpers ──────────────────────────────────────────────────────────
def shutdown_command(total_minutes, mode="shutdown"):
    if IS_WIN:
        flag = "/r" if mode == "restart" else "/s"
        return ["shutdown", flag, "/t", str(total_minutes * 60)]
    else:
        flag = "-r" if mode == "restart" else "-h"
        return ["shutdown", flag, f"+{total_minutes}"]


def cancel_shutdown():
    if IS_WIN:
        subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=0x08000000)
    else:
        result = subprocess.run(["shutdown", "-c"], capture_output=True)
        if result.returncode != 0:
            subprocess.run(["sudo", "shutdown", "-c"], capture_output=True)


# ─── Custom Message Dialog ───────────────────────────────────────────────────
class CustomMessage(tk.Toplevel):
    """A stylized replacement for standard messagebox dialogs."""
    def __init__(self, parent, title, message, type="info", colors=None):
        super().__init__(parent)
        self.colors = colors
        self.result = None
        
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self.colors["bg_dark"])
        
        # Build layout
        self.frame = tk.Frame(self, bg=self.colors["bg_card"], 
                              highlightbackground=self.colors["accent"], highlightthickness=1)
        self.frame.pack(fill="both", expand=True)
        
        # Title bar
        tb = tk.Frame(self.frame, bg=self.colors["titlebar"], height=30)
        tb.pack(fill="x", side="top")
        tk.Label(tb, text=title, font=("Segoe UI", 9, "bold"),
                 fg=self.colors["text_primary"], bg=self.colors["titlebar"]).pack(side="left", padx=10)
        
        # Content
        content = tk.Frame(self.frame, bg=self.colors["bg_card"], padx=20, pady=20)
        content.pack(fill="both", expand=True)
        
        # Icon/Symbol
        symbol = "ℹ" if type == "info" else "?"
        tk.Label(content, text=symbol, font=("Segoe UI", 24),
                 fg=self.colors["accent"], bg=self.colors["bg_card"]).pack(side="left", padx=(0, 15))
        
        msg_label = tk.Label(content, text=message, font=("Segoe UI", 10),
                             fg=self.colors["text_primary"], bg=self.colors["bg_card"],
                             justify="left", wraplength=250)
        msg_label.pack(side="left", fill="both", expand=True)
        
        # Buttons
        btn_frame = tk.Frame(self.frame, bg=self.colors["bg_card"], pady=15)
        btn_frame.pack(fill="x")
        
        # Symmetrical width for all dialog buttons
        BW = 12

        if type == "info":
            ttk.Button(btn_frame, text="OK", style="Shutdown.TButton", 
                       width=BW, command=self._ok).pack()
        elif type == "yesno":
            # Yes on left, No on right — both same width
            ttk.Button(btn_frame, text="Yes", style="Shutdown.TButton", 
                       width=BW, command=self._yes).pack(side="left", padx=(40, 5))
            ttk.Button(btn_frame, text="No", style="Cancel.TButton", 
                       width=BW, command=self._no).pack(side="left", padx=5)
        elif type == "yesnocancel":
            # Symmetrical layout for all three
            ttk.Button(btn_frame, text="Yes", style="Shutdown.TButton", 
                       width=BW, command=self._yes).pack(side="left", padx=(20, 5))
            ttk.Button(btn_frame, text="No", style="Cancel.TButton", 
                       width=BW, command=self._no).pack(side="left", padx=5)
            ttk.Button(btn_frame, text="Cancel", style="Cancel.TButton", 
                       width=BW, command=self._cancel).pack(side="left", padx=5)

        self._center()
        self.grab_set() # Make modal
        self.wait_window()

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        px, py = self.master.winfo_x(), self.master.winfo_y()
        pw, ph = self.master.winfo_width(), self.master.winfo_height()
        x = px + (pw // 2) - (w // 2)
        y = py + (ph // 2) - (h // 2)
        self.geometry(f"+{x}+{y}")

    def _ok(self): self.destroy()
    def _yes(self): self.result = True; self.destroy()
    def _no(self): self.result = False; self.destroy()
    def _cancel(self): self.result = None; self.destroy()


# ─── Dark-themed spinner widget ───────────────────────────────────────────────
class DarkSpinner(tk.Frame):
    """
    A fully themed up/down spinner that replaces ttk.Combobox.
    Exposes .get(), .set(val), and .config(state=) to match the old interface.
    """
    def __init__(self, parent, min_val=0, max_val=59, initial=0,
                 width=56, colors=None, **kwargs):
        self.colors = colors or {}
        c = self.colors
        bg       = c.get("input_bg",      "#12121f")
        fg       = c.get("text_primary",  "#ffffff")
        muted    = c.get("text_muted",    "#5a5a72")
        accent   = c.get("accent",        "#e94560")
        border   = c.get("border",        "#2a2a40")
        card     = c.get("bg_card",       "#1a1a2e")

        super().__init__(parent, bg=card, **kwargs)

        self._min   = min_val
        self._max   = max_val
        self._value = initial
        self._enabled = True

        # Border frame
        border_f = tk.Frame(self, bg=border, padx=1, pady=1)
        border_f.pack()

        inner = tk.Frame(border_f, bg=bg)
        inner.pack()

        # Up button
        self._up_btn = tk.Button(
            inner, text="▲", font=("Segoe UI", 6), fg=muted, bg=bg,
            activeforeground=accent, activebackground=bg,
            relief="flat", bd=0, cursor="hand2", width=3,
            command=self._increment)
        self._up_btn.pack(pady=(2, 0))

        # Value label
        self._label = tk.Label(
            inner, text=str(self._value),
            font=("Consolas", 16, "bold"),
            fg=fg, bg=bg, width=2, anchor="center")
        self._label.pack(padx=6)

        # Down button
        self._down_btn = tk.Button(
            inner, text="▼", font=("Segoe UI", 6), fg=muted, bg=bg,
            activeforeground=accent, activebackground=bg,
            relief="flat", bd=0, cursor="hand2", width=3,
            command=self._decrement)
        self._down_btn.pack(pady=(0, 2))

        # Mouse wheel support
        self._label.bind("<MouseWheel>", self._on_scroll)
        inner.bind("<MouseWheel>", self._on_scroll)

    def update_theme(self):
        """Called by parent when theme transition occurs."""
        c = self.colors
        bg = c.get("input_bg", "#12121f")
        accent = c.get("accent", "#e94560")
        self._up_btn.config(activebackground=bg, activeforeground=accent)
        self._down_btn.config(activebackground=bg, activeforeground=accent)

    def _increment(self):
        if not self._enabled: return
        self._value = self._min if self._value >= self._max else self._value + 1
        self._label.config(text=str(self._value))

    def _decrement(self):
        if not self._enabled: return
        self._value = self._max if self._value <= self._min else self._value - 1
        self._label.config(text=str(self._value))

    def _on_scroll(self, event):
        if event.delta > 0:
            self._increment()
        else:
            self._decrement()

    def get(self):
        return str(self._value)

    def set(self, val):
        self._value = max(self._min, min(self._max, int(val)))
        self._label.config(text=str(self._value))

    def config(self, **kwargs):
        state = kwargs.get("state")
        if state == "disabled":
            self._enabled = False
            self._up_btn.config(state="disabled")
            self._down_btn.config(state="disabled")
        elif state in ("normal", "readonly"):
            self._enabled = True
            self._up_btn.config(state="normal")
            self._down_btn.config(state="normal")



# ─── Dark-themed toggle switch ────────────────────────────────────────────────
class DarkCheckbox(tk.Frame):
    """Animated pill-style toggle switch matching the dark UI."""
    W, H = 44, 22   # track dimensions

    def __init__(self, parent, text="", variable=None, command=None, colors=None, **kwargs):
        self.colors = colors if colors is not None else {}
        super().__init__(parent, bg=self._bg, cursor="hand2", **kwargs)

        self._var     = variable if variable is not None else tk.BooleanVar()
        self._command = command
        self._anim_id = None
        self._knob_x  = 4  # current animated x position of knob

        # Canvas for the toggle track + knob
        self._cv = tk.Canvas(self, width=self.W, height=self.H,
                             bg=self._bg, highlightthickness=0)
        self._cv.pack(side="left", padx=(0, 8))

        # Label - use initial state to set correct foreground
        initial_fg = self._fg_on if self._var.get() else self._fg
        self._lbl = tk.Label(self, text=text, font=("Segoe UI", 9),
                             fg=initial_fg, bg=self._bg)
        self._lbl.pack(side="left")

        self._draw()

        for w in (self, self._cv, self._lbl):
            w.bind("<Button-1>", self._toggle)
            w.bind("<Enter>",    self._on_enter)
            w.bind("<Leave>",    self._on_leave)

    @property
    def _bg(self): return self.colors.get("bg_dark", "#0f0f1a")
    @property
    def _accent(self): return self.colors.get("accent", "#e94560")
    @property
    def _border(self): return self.colors.get("border", "#2a2a40")
    @property
    def _fg(self): return self.colors.get("text_muted", "#5a5a72")
    @property
    def _fg_on(self): return self.colors.get("text_primary", "#ffffff")
    @property
    def _track_off(self): return self.colors.get("bg_card_alt", "#16213e")
    @property
    def _hover_bg(self): return self.colors.get("bg_card", "#1a1a2e")

    def update_theme(self):
        """Called by parent when theme transition occurs."""
        # Update label tag so it receives the correct interpolated color
        self._lbl._theme_fg = "text_primary" if self._var.get() else "text_muted"
        self._draw()

    # ── Drawing ──────────────────────────────────────────────────────────────
    @staticmethod
    def _pill_points(x0, y0, x1, y1, steps=40):
        """Return a flat list of (x,y) points forming a smooth pill/stadium shape."""
        import math
        r  = (y1 - y0) / 2
        cy = (y0 + y1) / 2
        pts = []
        # Left semicircle (90° → 270°)
        cx = x0 + r
        for i in range(steps + 1):
            a = math.radians(90 + 180 * i / steps)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        # Right semicircle (270° → 90°)
        cx = x1 - r
        for i in range(steps + 1):
            a = math.radians(270 + 180 * i / steps)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        return pts

    def _draw(self, knob_x=None):
        cv = self._cv
        cv.delete("all")
        on   = self._var.get()
        W, H = self.W, self.H
        pad  = 2          # inset from canvas edge
        kr   = H / 2 - pad - 1   # knob radius

        if knob_x is None:
            knob_x = W - pad - kr * 2 if on else pad + 2
            self._knob_x = knob_x

        track_color = self._accent if on else self._track_off

        # Smooth pill track — outline matches fill to avoid 1px fringe
        pts = self._pill_points(pad, pad, W - pad, H - pad)
        cv.create_polygon(pts, fill=track_color, outline=track_color, smooth=False)

        # Knob — drawn 1px larger than the track radius so it fully covers
        # any track edge bleed beneath it
        kx = knob_x + kr
        ky = H / 2
        cv.create_oval(kx - kr - 1, ky - kr - 1, kx + kr + 1, ky + kr + 1,
                       fill="white", outline="white")

    # ── Animation ────────────────────────────────────────────────────────────
    def _animate(self):
        on    = self._var.get()
        pad   = 2
        kr    = self.H / 2 - pad - 1
        t_on  = self.W - pad - kr * 2
        t_off = pad + 2
        target_x = t_on if on else t_off
        step     = 3 if on else -3
        new_x    = self._knob_x + step

        if (on and new_x >= target_x) or (not on and new_x <= target_x):
            new_x = target_x

        self._knob_x = new_x
        self._draw(knob_x=new_x)

        if new_x != target_x:
            self._anim_id = self.after(12, self._animate)
        else:
            self._anim_id = None

    # ── Interaction ──────────────────────────────────────────────────────────
    def _toggle(self, event=None):
        if self._anim_id:
            self.after_cancel(self._anim_id)
        self._var.set(not self._var.get())
        self._lbl.config(fg=self._fg_on if self._var.get() else self._fg)
        self._animate()
        if self._command:
            self._command()

    def _on_enter(self, event=None):
        for w in (self, self._lbl):
            w.config(bg=self._hover_bg)
        self._cv.config(bg=self._hover_bg)
        self._draw(knob_x=self._knob_x)

    def _on_leave(self, event=None):
        for w in (self, self._lbl):
            w.config(bg=self._bg)
        self._cv.config(bg=self._bg)
        self._draw(knob_x=self._knob_x)


# ─── Main application ──────────────────────────────────────────────────────────
class ShutdownApp:
    PRESETS = [
        ("5 min",   0,  5),
        ("15 min",  0, 15),
        ("30 min",  0, 30),
        ("1 hour",  1,  0),
        ("2 hours", 2,  0),
    ]

    def __init__(self, root):
        self.root = root
        self.root.title("⏻ Lights Out")
        self.root.geometry("380x540")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        # Remove the OS title bar — we draw our own.
        # overrideredirect and withdraw are already applied at the entry point
        # before this class is constructed, so we don't repeat them here.
        self.root.overrideredirect(True)
        _start_minimized = "--minimized" in sys.argv

        self.colors = {
            "bg_dark":        "#0f0f1a",
            "bg_card":        "#1a1a2e",
            "bg_card_alt":    "#16213e",
            "accent":         "#e94560",
            "accent_hover":   "#ff6b81",
            "text_primary":   "#ffffff",
            "text_secondary": "#a0a0b8",
            "text_muted":     "#5a5a72",
            "success":        "#00d2d3",
            "warning":        "#feca57",
            "border":         "#2a2a40",
            "input_bg":       "#12121f",
            "titlebar":       "#0a0a14",
        }

        # Dark mode colours (active timer)
        self.colors_normal = dict(self.colors)
        self.colors_dark = {
            "bg_dark":        "#050508",
            "bg_card":        "#0a0a0f",
            "bg_card_alt":    "#080810",
            "accent":         "#e94560",
            "accent_hover":   "#ff6b81",
            "text_primary":   "#ffffff",
            "text_secondary": "#6a6a80",
            "text_muted":     "#333345",
            "success":        "#00d2d3",
            "warning":        "#feca57",
            "border":         "#111120",
            "input_bg":       "#050508",
            "titlebar":       "#030305",
        }

        self.content_frame = None
        self.is_scheduled      = False
        self.remaining_seconds = 0
        self.update_timer_id   = None
        self.mode_var          = "shutdown"
        self._tray             = None
        self._tray_hint_shown  = False
        self._drag_x           = 0
        self._drag_y           = 0
        self._theme_anim_id    = None
        self._theme_progress   = 0.0   # 0.0 = normal, 1.0 = dark
        self.preset_buttons    = []

        if IS_WIN:
            self.startup_var = tk.BooleanVar(value=self._is_startup_enabled())

        self._setup_styles()
        self._build_titlebar()
        self._apply_background()
        self._build_ui()
        self._center_window()
        self._tag_all_widgets()  # must be called after all UI is built

        # Start native tray on Windows only.
        # Win32Tray._run retries Shell_NotifyIconW until the shell is ready,
        # so it is safe to initialise immediately even on Windows boot.
        if IS_WIN:
            try:
                self._tray = Win32Tray(on_action=self._tray_action,
                                       tk_root=self.root)
            except Exception as e:
                print(f"[tray] init failed: {e}")

        if _start_minimized:
            self._tray_hint_shown = True  # Don't show the "Minimised to Tray" bubble on auto-start

    # ─── Custom title bar ─────────────────────────────────────────────────────
    def _build_titlebar(self):
        c = self.colors
        tb = tk.Frame(self.root, bg=c["titlebar"], height=32)
        tb.pack(fill="x", side="top")
        tb.pack_propagate(False)

        # Accent left edge stripe
        tk.Frame(tb, bg=c["accent"], width=3).pack(side="left", fill="y")

        # Power icon + title
        tk.Label(tb, text="⏻", font=("Segoe UI", 11),
                 fg=c["accent"], bg=c["titlebar"]).pack(side="left", padx=(8, 4))
        tk.Label(tb, text="Lights Out",
                 font=("Segoe UI", 9, "bold"),
                 fg=c["text_primary"], bg=c["titlebar"]).pack(side="left")

        # Window control buttons (right side)
        btn_cfg = dict(font=("Segoe UI", 9), bd=0, relief="flat",
                       activeforeground="white", pady=0, padx=10, cursor="hand2")

        close_btn = tk.Button(tb, text="✕",
                              fg=c["text_muted"], bg=c["titlebar"],
                              activebackground=c["accent"],
                              command=self._on_close, **btn_cfg)
        close_btn.pack(side="right")

        min_btn = tk.Button(tb, text="─",
                            fg=c["text_muted"], bg=c["titlebar"],
                            activebackground=c["bg_card_alt"],
                            command=self._minimise, **btn_cfg)
        min_btn.pack(side="right")

        # Drag to move
        tb.bind("<ButtonPress-1>",   self._drag_start)
        tb.bind("<B1-Motion>",       self._drag_move)
        for child in tb.winfo_children():
            # Only non-button children drag (labels/icon)
            if isinstance(child, tk.Label):
                child.bind("<ButtonPress-1>", self._drag_start)
                child.bind("<B1-Motion>",     self._drag_move)

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _drag_move(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _minimise(self):
        """Hide window to system tray."""
        self.root.withdraw()
        if self._tray and not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.root.after(200, lambda: self._show_info(
                "Minimised to Tray",
                "Lights Out is still running in the system tray.\n\n"
                "Right-click the tray icon to schedule or quit."))

    # ─── Tray callbacks ───────────────────────────────────────────────────────
    def _tray_action(self, action, data):
        """Called from the tray thread — marshal everything to Tk main thread."""
        self.root.after(0, lambda: self._handle_tray_action(action, data))

    def _handle_tray_action(self, action, data):
        if action == "open":
            self._show_window()
        elif action == "quit":
            self._quit_app()
        elif action == "cancel":
            self._cancel_shutdown()
        elif action in ("shutdown", "restart", "sleep"):
            self._quick_schedule(action, data)

    def _show_window(self):
        self.root.overrideredirect(True)
        self.root.withdraw()   # ensure clean state before showing
        self.root.after(10, self._do_show_window)

    def _do_show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit_app(self):
        if self.is_scheduled:
            if self.mode_var == "sleep":
                if not self._ask_yes_no("Quit",
                                           "A sleep timer is active. Quitting will CANCEL it.\n\nQuit anyway?"):
                    return
                self._cancel_shutdown()
            else:
                if self._ask_yes_no("Quit",
                                       f"A {self.mode_var} is scheduled. Cancel it before quitting?"):
                    self._cancel_shutdown()
        if self._tray:
            self._tray.remove()
        self.root.destroy()

    def _quick_schedule(self, mode, total_minutes):
        if self.is_scheduled:
            if not self._ask_yes_no("Replace?",
                                       "A schedule is already active. Replace it?"):
                return
            self._cancel_shutdown()
        self._set_mode(mode)
        hours, mins = divmod(total_minutes, 60)
        self._set_preset(hours, mins)
        self._schedule_shutdown()
        self._show_window()

    def _update_tray_tooltip(self):
        if not self._tray:
            return
        if self.is_scheduled:
            action = self.mode_var.capitalize()
            self._tray.set_tooltip(
                f"{action} in {self._format_time(self.remaining_seconds)}")
        else:
            self._tray.set_tooltip("Lights Out")

    def _setup_styles(self):
        s  = ttk.Style()
        c  = self.colors
        s.theme_use("clam")
        s.configure(".", background=c["bg_dark"])

        def btn(name, bg, fg, hover, **kw):
            s.configure(name, background=bg, foreground=fg,
                        borderwidth=0, focusthickness=0, focuscolor="", **kw)
            s.map(name,
                  background=[("active", hover)],
                  relief=[("focus", "flat"), ("!focus", "flat")])

        btn("Shutdown.TButton",     c["accent"],       "white",             c["accent_hover"],
            font=("Segoe UI", 11, "bold"), padding=(5, 5), width=18, anchor="center")
        btn("Cancel.TButton",       "#2d3436",         c["text_secondary"], "#3d4446",
            font=("Segoe UI", 11, "bold"), padding=(5, 5), width=18, anchor="center")
        btn("Quick.TButton",        c["bg_card_alt"],  c["text_secondary"], c["accent"],
            font=("Segoe UI", 8, "bold"),  padding=(0, 5))
        s.map("Quick.TButton",
              background=[("active", c["accent"])],
              foreground=[("active", "white")])
        btn("ModeActive.TButton",   c["accent"],       "white",             c["accent_hover"],
            font=("Segoe UI", 9, "bold"),  padding=(10, 5))
        btn("ModeInactive.TButton", c["bg_card_alt"],  c["text_muted"],     c["border"],
            font=("Segoe UI", 9, "bold"),  padding=(10, 5))
        s.map("ModeInactive.TButton",
              background=[("active", c["border"])],
              foreground=[("active", c["text_secondary"])])

        s.configure("Custom.TSeparator", background=c["border"])
        self.style = s

    # ─── Background ───────────────────────────────────────────────────────────
    def _apply_background(self):
        self.canvas = tk.Canvas(self.root, highlightthickness=0,
                                bg=self.colors["bg_dark"])
        self.canvas.place(x=0, y=32, relwidth=1, height=508)
        # Bottom accent bar
        self.canvas.create_rectangle(0, 505, 380, 508,
                                     fill=self.colors["accent"], outline="",
                                     tags="accent_bar")
        self.content_frame = tk.Frame(self.root, bg=self.colors["bg_dark"])
        self.content_frame.place(relx=0.5, y=32, anchor="n")

    # ─── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        c = self.colors

        # Header
        hf = tk.Frame(self.content_frame, bg=c["bg_dark"])
        hf.pack(pady=(0, 6))
        tk.Label(hf, text="⏻  Lights Out",
                 font=("Segoe UI", 15, "bold"),
                 fg=c["text_primary"], bg=c["bg_dark"]).pack()
        tk.Label(hf, text="Schedule your PC to shut down, restart, or sleep",
                 font=("Segoe UI", 9),
                 fg=c["text_secondary"], bg=c["bg_dark"]).pack()

        # Card
        card = tk.Frame(self.content_frame, bg=c["bg_card"],
                        highlightbackground=c["border"], highlightthickness=1)
        card.pack(fill="x", padx=14, pady=(4, 0))

        # Mode toggle
        mf = tk.Frame(card, bg=c["bg_card"])
        mf.pack(pady=(10, 0))
        tk.Label(mf, text="MODE", font=("Segoe UI", 8, "bold"),
                 fg=c["text_muted"], bg=c["bg_card"]).pack(pady=(0, 4))
        tf = tk.Frame(mf, bg=c["bg_card"])
        tf.pack()
        def _mode_btn(parent, text, style, cmd, ring_col, padx):
            # highlightthickness=1 always reserved so focus ring never shifts layout;
            # highlightbackground starts matching card bg (invisible), becomes ring_col on focus
            wrap = tk.Frame(parent, bg=c["bg_card"],
                            highlightthickness=1,
                            highlightbackground=c["bg_card"],
                            highlightcolor=c["bg_card"])
            wrap.pack(side="left", padx=padx)
            b = ttk.Button(wrap, text=text, style=style, command=cmd)
            b.pack()
            def _show(e, w=wrap, col=ring_col):
                if self.is_scheduled:
                    return "break"
                w.config(highlightbackground=getattr(w, "_ring_col", col),
                         highlightcolor=getattr(w, "_ring_col", col))
            def _hide(e, w=wrap): w.config(highlightbackground=c["bg_card"], highlightcolor=c["bg_card"])
            b.bind("<FocusIn>",       _show)
            b.bind("<FocusOut>",      _hide)
            b.bind("<ButtonPress-1>", _show)
            return b

        self.shutdown_mode_btn = _mode_btn(
            tf, "⏻  Shutdown", "ModeActive.TButton",
            lambda: self._set_mode("shutdown"), "#ffffff", (0, 2))
        self.restart_mode_btn = _mode_btn(
            tf, "↺  Restart", "ModeInactive.TButton",
            lambda: self._set_mode("restart"), "#ffffff", 2)
        self.sleep_mode_btn = _mode_btn(
            tf, "🌙  Sleep", "ModeInactive.TButton",
            lambda: self._set_mode("sleep"), "#ffffff", (2, 0))

        # Time pickers
        time_frame = tk.Frame(card, bg=c["bg_card"])
        time_frame.pack(pady=(12, 6))
        hm = tk.Frame(time_frame, bg=c["bg_card"])
        hm.pack()

        h_frame = tk.Frame(hm, bg=c["bg_card"])
        h_frame.pack(side="left", padx=4)
        tk.Label(h_frame, text="HRS", font=("Segoe UI", 8),
                 fg=c["text_muted"], bg=c["bg_card"]).pack()
        self.hour_spin = DarkSpinner(h_frame, min_val=0, max_val=24, initial=0,
                                      colors=c)
        self.hour_spin.pack()

        tk.Label(hm, text=":", font=("Consolas", 22, "bold"),
                 fg=c["text_muted"], bg=c["bg_card"], padx=4
                 ).pack(side="left", pady=(10, 0))

        m_frame = tk.Frame(hm, bg=c["bg_card"])
        m_frame.pack(side="left", padx=4)
        tk.Label(m_frame, text="MIN", font=("Segoe UI", 8),
                 fg=c["text_muted"], bg=c["bg_card"]).pack()
        self.min_spin = DarkSpinner(m_frame, min_val=0, max_val=59, initial=0,
                                     colors=c)
        self.min_spin.pack()

        # Countdown
        self.countdown_label = tk.Label(card, text="00:00:00",
                                        font=("Consolas", 26, "bold"),
                                        fg=c["accent"], bg=c["bg_card"])
        self.countdown_label.pack(pady=(6, 2))
        self.status_label = tk.Label(card, text="Ready to schedule",
                                     font=("Segoe UI", 9),
                                     fg=c["text_muted"], bg=c["bg_card"])
        self.status_label.pack(pady=(0, 8))

        ttk.Separator(card, orient="horizontal",
                      style="Custom.TSeparator").pack(fill="x", padx=16, pady=2)

        # Quick presets
        pf = tk.Frame(card, bg=c["bg_card"])
        pf.pack(fill="x", padx=6, pady=6)
        tk.Label(pf, text="QUICK PRESETS", font=("Segoe UI", 8, "bold"),
                 fg=c["text_muted"], bg=c["bg_card"]).pack(pady=(0, 4))
        bf = tk.Frame(pf, bg=c["bg_card"])
        bf.pack(fill="x")
        for col in range(len(self.PRESETS)):
            bf.columnconfigure(col, weight=1)
        for col, (label, h, m) in enumerate(self.PRESETS):
            short = label.replace(" min", "m").replace(" hour", "h").replace("s", "")
            wrap = tk.Frame(bf, bg=c["bg_card"],
                            highlightthickness=1, highlightbackground=c["bg_card"],
                            highlightcolor=c["bg_card"])
            wrap.grid(row=0, column=col, sticky="ew", padx=1)
            b = ttk.Button(wrap, text=short, style="Quick.TButton",
                           command=lambda hh=h, mm=m: self._set_preset(hh, mm))
            b.pack(fill="x")
            b.bind("<FocusIn>",       lambda e, w=wrap: "break" if self.is_scheduled else w.config(highlightbackground="#ffffff", highlightcolor="#ffffff"))
            b.bind("<FocusOut>",      lambda e, w=wrap: w.config(highlightbackground=c["bg_card"], highlightcolor=c["bg_card"]))
            b.bind("<ButtonPress-1>", lambda e, w=wrap: "break" if self.is_scheduled else w.config(highlightbackground="#ffffff", highlightcolor="#ffffff"))
            self.preset_buttons.append(b)

        ttk.Separator(card, orient="horizontal",
                      style="Custom.TSeparator").pack(fill="x", padx=16, pady=2)

        # Action buttons
        af = tk.Frame(card, bg=c["bg_card"])
        af.pack(pady=10)

        shutdown_wrap = tk.Frame(af, bg=c["bg_card"],
                                 highlightthickness=1, highlightbackground=c["bg_card"],
                                 highlightcolor=c["bg_card"])
        shutdown_wrap.pack(side="left", padx=4)
        self.shutdown_wrap = shutdown_wrap
        self.shutdown_btn = ttk.Button(shutdown_wrap, text="⏻  Schedule Lights Out",
                                       style="Shutdown.TButton",
                                       command=self._schedule_shutdown)
        self.shutdown_btn.pack()
        self.shutdown_btn.bind("<FocusIn>",       lambda e: "break" if self.is_scheduled else shutdown_wrap.config(highlightbackground="#ffffff", highlightcolor="#ffffff"))
        self.shutdown_btn.bind("<FocusOut>",      lambda e: shutdown_wrap.config(highlightbackground=c["bg_card"], highlightcolor=c["bg_card"]))
        self.shutdown_btn.bind("<ButtonPress-1>", lambda e: "break" if self.is_scheduled else shutdown_wrap.config(highlightbackground="#ffffff", highlightcolor="#ffffff"))

        cancel_wrap = tk.Frame(af, bg=c["bg_card"],
                               highlightthickness=1, highlightbackground=c["bg_card"],
                               highlightcolor=c["bg_card"])
        cancel_wrap.pack(side="left", padx=4)
        self.cancel_btn = ttk.Button(cancel_wrap, text="Cancel",
                                     style="Cancel.TButton",
                                     command=self._cancel_shutdown,
                                     state="disabled")
        self.cancel_btn.pack()
        self.cancel_btn.bind("<FocusIn>",       lambda e: cancel_wrap.config(highlightbackground="#ffffff", highlightcolor="#ffffff"))
        self.cancel_btn.bind("<FocusOut>",      lambda e: cancel_wrap.config(highlightbackground=c["bg_card"], highlightcolor=c["bg_card"]))
        self.cancel_btn.bind("<ButtonPress-1>", lambda e: cancel_wrap.config(highlightbackground="#ffffff", highlightcolor="#ffffff"))

        # Footer
        footer_f = tk.Frame(self.content_frame, bg=c["bg_dark"])
        footer_f.pack(pady=(5, 2))

        if IS_WIN:
            DarkCheckbox(footer_f, text="Run on Startup",
                         variable=self.startup_var, command=self._toggle_startup,
                         colors=c).pack(pady=(0, 2))


        tray_txt = "· Tray active" if IS_WIN else "· Tray: Windows only"
        tk.Label(footer_f,
                 text=f"{'WIN32' if IS_WIN else sys.platform.upper()}  {tray_txt}",
                 font=("Segoe UI", 8),
                 fg=c["text_muted"], bg=c["bg_dark"]).pack()

    # ─── Helpers ──────────────────────────────────────────────────────────────
    def _is_startup_enabled(self):
        if not IS_WIN: return False
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, "LightsOut")
            winreg.CloseKey(key)
            return True
        except Exception:
            return False





    def _toggle_startup(self):
        if not IS_WIN: return

        app_name = "LightsOut"
        if getattr(sys, 'frozen', False):
            app_path = sys.executable
        else:
            app_path = os.path.abspath(sys.argv[0])

        if self.startup_var.get():
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_WRITE)
                cmd = f'"{app_path}" --minimized'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
                winreg.CloseKey(key)
            except Exception as e:
                self._show_info("Error", f"Failed to enable startup: {e}")
                self.startup_var.set(False)
        else:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_WRITE)
                winreg.DeleteValue(key, app_name)
                winreg.CloseKey(key)
            except Exception as e:
                self._show_info("Error", f"Failed to disable startup: {e}")
                self.startup_var.set(True)

    # ─── Theme transition ─────────────────────────────────────────────────────
    @staticmethod
    def _hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    @staticmethod
    def _rgb_to_hex(r, g, b):
        return f"#{int(r):02x}{int(g):02x}{int(b):02x}"

    def _lerp(self, a, b, t):
        ra,ga,ba = self._hex_to_rgb(a)
        rb,gb,bb = self._hex_to_rgb(b)
        return self._rgb_to_hex(ra+(rb-ra)*t, ga+(gb-ga)*t, ba+(bb-ba)*t)

    def _tag_widget(self, widget, bg_role=None, fg_role=None, hb_role=None):
        """Store colour roles on a widget so transitions always use original values."""
        if bg_role: widget._theme_bg = bg_role
        if fg_role: widget._theme_fg = fg_role
        if hb_role: widget._theme_hb = hb_role
        if not hasattr(widget, '_theme_children'):
            widget._theme_children = []

    def _collect_tagged(self, parent, out):
        for w in parent.winfo_children():
            if hasattr(w, '_theme_bg') or hasattr(w, '_theme_fg') or hasattr(w, '_theme_hb'):
                out.append(w)
            self._collect_tagged(w, out)

    def _tag_all_widgets(self):
        """Walk all widgets after UI is built and tag them by their initial colour role."""
        normal = self.colors_normal
        # Build reverse map: hex colour -> role name
        bg_reverse = {v: k for k, v in normal.items()}

        def walk(parent):
            for w in parent.winfo_children():
                # bg
                try:
                    bg = w.cget("bg")
                    if bg in bg_reverse:
                        w._theme_bg = bg_reverse[bg]
                except Exception:
                    pass
                # fg
                try:
                    fg = w.cget("fg")
                    if fg in bg_reverse:
                        w._theme_fg = bg_reverse[fg]
                except Exception:
                    pass
                # highlightbackground
                try:
                    hb = w.cget("highlightbackground")
                    if hb in bg_reverse:
                        w._theme_hb = bg_reverse[hb]
                except Exception:
                    pass
                walk(w)

        walk(self.root)
        # Also tag root itself
        self.root._theme_bg = "bg_dark"

    def _apply_theme(self, t):
        """Apply interpolated colours to all tagged widgets."""
        n = self.colors_normal
        d = self.colors_dark

        def lc(role):
            return self._lerp(n[role], d[role], t)

        # Update live colours dict in-place so custom widgets see it
        for k in n:
            self.colors[k] = lc(k)

        # Apply to all tagged widgets
        def walk(parent):
            for w in parent.winfo_children():
                # Custom theme-aware widgets
                if hasattr(w, "update_theme"):
                    w.update_theme()

                try:
                    if hasattr(w, "_theme_bg"):
                        c = lc(w._theme_bg)
                        w.config(bg=c)
                        # Also update active background for standard buttons
                        if isinstance(w, (tk.Button, tk.Menu)):
                            w.config(activebackground=c)
                except Exception:
                    pass
                try:
                    if hasattr(w, "_theme_fg"):
                        c = lc(w._theme_fg)
                        w.config(fg=c)
                        if isinstance(w, (tk.Button, tk.Menu)):
                            w.config(activeforeground=c)
                except Exception:
                    pass
                try:
                    if hasattr(w, "_theme_hb"):
                        w.config(highlightbackground=lc(w._theme_hb))
                except Exception:
                    pass
                walk(w)

        # Root window
        try:
            self.root.config(bg=lc("bg_dark"))
        except Exception:
            pass

        walk(self.root)

        # Canvas background and accent bar
        try:
            self.canvas.config(bg=lc("bg_dark"))
            self.canvas.itemconfig("accent_bar", fill=lc("accent"), outline=lc("accent"))
        except Exception:
            pass

        # ttk styled buttons
        s = self.style
        try:
            s.configure("Cancel.TButton",
                background=self._lerp("#2d3436", "#0d1011", t),
                foreground=lc("text_secondary"))
            s.map("Cancel.TButton",
                  background=[("active", self._lerp("#3d4446", "#151819", t))])
            s.configure("ModeInactive.TButton",
                background=lc("bg_card_alt"),
                foreground=lc("text_muted"))
            s.map("ModeInactive.TButton",
                  background=[("active", lc("border"))])
            s.configure("Custom.TSeparator",
                background=lc("border"))
            s.configure("Quick.TButton",
                background=lc("bg_card_alt"),
                foreground=lc("text_secondary"))
            s.map("Quick.TButton",
                  background=[("active", lc("accent"))],
                  foreground=[("active", "white")])
        except Exception:
            pass

    def _run_theme_transition(self, to_dark, step=0.05):
        if self._theme_anim_id:
            self.root.after_cancel(self._theme_anim_id)
            self._theme_anim_id = None

        delta = step if to_dark else -step

        def tick():
            self._theme_progress = max(0.0, min(1.0, self._theme_progress + delta))
            self._apply_theme(self._theme_progress)
            if 0.0 < self._theme_progress < 1.0:
                self._theme_anim_id = self.root.after(16, tick)
            else:
                self._theme_anim_id = None

        tick()

    def _center_window(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth()  // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"+{x}+{y}")

    # Maps mode → (button_attr, schedule button label)
    _MODE_CFG = {
        "shutdown": ("shutdown_mode_btn", "⏻  Schedule Lights Out"),
        "restart":  ("restart_mode_btn",  "↺  Schedule Restart"),
        "sleep":    ("sleep_mode_btn",    "🌙  Schedule Sleep"),
    }

    def _set_mode(self, mode):
        self.mode_var = mode
        c = self.colors
        for m, (attr, _) in self._MODE_CFG.items():
            style = "ModeActive.TButton" if m == mode else "ModeInactive.TButton"
            btn = getattr(self, attr)
            btn.config(style=style)
            # Update the wrapper frame's ring colour to match new text colour
            ring_col = "#ffffff"
            wrap = btn.master
            wrap.config(highlightbackground=c["bg_card"])  # reset to invisible; focus will show it
            wrap._ring_col = ring_col  # store so FocusIn uses correct colour
        self.shutdown_btn.config(text=self._MODE_CFG[mode][1])

    def _set_mode_buttons_locked(self, locked):
        state = "disabled" if locked else "normal"
        for btn in (self.shutdown_mode_btn, self.restart_mode_btn, self.sleep_mode_btn):
            btn.config(state=state)
            wrap = btn.master
            wrap.config(highlightbackground=self.colors["bg_card"],
                        highlightcolor=self.colors["bg_card"])
        if locked:
            focused = self.root.focus_get()
            if focused in (self.shutdown_mode_btn, self.restart_mode_btn, self.sleep_mode_btn):
                self.root.focus_set()

    def _set_presets_locked(self, locked):
        state = "disabled" if locked else "normal"
        for btn in self.preset_buttons:
            btn.config(state=state)
            wrap = btn.master
            wrap.config(highlightbackground=self.colors["bg_card"],
                        highlightcolor=self.colors["bg_card"])
        if locked:
            focused = self.root.focus_get()
            if focused in self.preset_buttons:
                self.root.focus_set()

    def _set_schedule_button_ring_locked(self, locked):
        if hasattr(self, "shutdown_wrap"):
            self.shutdown_wrap.config(highlightbackground=self.colors["bg_card"],
                                      highlightcolor=self.colors["bg_card"])
        if locked and self.root.focus_get() == self.shutdown_btn:
            self.root.focus_set()

    def _show_info(self, title, message):
        CustomMessage(self.root, title, message, type="info", colors=self.colors)

    def _ask_yes_no(self, title, message):
        dialog = CustomMessage(self.root, title, message, type="yesno", colors=self.colors)
        return dialog.result

    def _ask_yes_no_cancel(self, title, message):
        dialog = CustomMessage(self.root, title, message, type="yesnocancel", colors=self.colors)
        return dialog.result

    def _set_preset(self, hours, minutes):
        self.hour_spin.set(hours)
        self.min_spin.set(minutes)

    def _get_total_minutes(self):
        try:
            return int(self.hour_spin.get()) * 60 + int(self.min_spin.get())
        except ValueError:
            return 0

    @staticmethod
    def _format_time(total_seconds):
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    # ─── Core logic ───────────────────────────────────────────────────────────
    def _schedule_shutdown(self):
        total_minutes = self._get_total_minutes()
        if total_minutes == 0:
            self._show_info("Invalid Time", "Please select a time greater than 0.")
            return
        if self.is_scheduled:
            if not self._ask_yes_no("Confirm", "An action is already scheduled.\nDo you want to replace it?"):
                return
            self._cancel_shutdown()

        # Only run OS shutdown command immediately if it's not sleep
        # (Shutdown/Restart have built-in timers, Sleep does not)
        if self.mode_var != "sleep":
            cmd = shutdown_command(total_minutes, self.mode_var)
            try:
                # 0x08000000 = CREATE_NO_WINDOW
                subprocess.Popen(cmd, creationflags=0x08000000)
            except Exception as e:
                messagebox.showerror("Error",
                                     f"Failed to schedule {self.mode_var}:\n{e}")
                return

        action = self.mode_var.capitalize()
        self.is_scheduled = True
        self.remaining_seconds = total_minutes * 60
        self.countdown_label.config(
            text=self._format_time(self.remaining_seconds))
        self.status_label.config(
            text=f"{action} in {total_minutes} minute(s)",
            fg=self.colors["warning"])
        self.shutdown_btn.config(state="disabled", style="Cancel.TButton")
        self.cancel_btn.config(state="normal", style="Shutdown.TButton")
        self.hour_spin.config(state="disabled")
        self.min_spin.config(state="disabled")
        self._set_mode_buttons_locked(True)
        self._set_presets_locked(True)
        self._set_schedule_button_ring_locked(True)
        self._run_theme_transition(to_dark=True)
        self._update_countdown()

    def _update_countdown(self):
        if not self.is_scheduled or self.remaining_seconds <= 0:
            return
        self.remaining_seconds -= 1
        self.countdown_label.config(
            text=self._format_time(self.remaining_seconds))
        self._update_tray_tooltip()
        if self.remaining_seconds <= 0:
            if self.mode_var == "sleep":
                self._execute_immediate_action()
            action = self.mode_var.capitalize()
            self._reset_ui(status=f"{action} command sent!",
                           status_color=self.colors["success"],
                           countdown_text=action.upper())
            return
        self.update_timer_id = self.root.after(1000, self._update_countdown)

    def _execute_immediate_action(self):
        """Used for Sleep mode (since it doesn't have a native OS timer)."""
        if IS_WIN:
            subprocess.Popen(["rundll32.exe", "powrprof.dll", "SetSuspendState", "0,1,0"],
                             creationflags=0x08000000)
        else:
            subprocess.Popen(["systemctl", "suspend"])

    def _cancel_shutdown(self):
        if self.update_timer_id:
            self.root.after_cancel(self.update_timer_id)
            self.update_timer_id = None
        
        # Only call OS cancel if it wasn't a Sleep timer
        if self.mode_var != "sleep":
            cancel_shutdown()
            
        self._reset_ui(status="Cancelled.",
                       status_color=self.colors["success"],
                       countdown_text="00:00:00")

    def _reset_ui(self, status, status_color, countdown_text):
        self.is_scheduled = False
        self.remaining_seconds = 0
        self.countdown_label.config(text=countdown_text)
        self.status_label.config(text=status, fg=status_color)
        self.shutdown_btn.config(state="normal", style="Shutdown.TButton")
        self.cancel_btn.config(state="disabled", style="Cancel.TButton")
        self.hour_spin.config(state="readonly")
        self.min_spin.config(state="readonly")
        self._set_mode_buttons_locked(False)
        self._set_presets_locked(False)
        self._set_schedule_button_ring_locked(False)
        self._update_tray_tooltip()
        self._run_theme_transition(to_dark=False)

    def _on_close(self):
        if self._tray:
            self._minimise()
        else:
            if self.is_scheduled:
                if self.mode_var == "sleep":
                    if self._ask_yes_no("Warning",
                                           "A sleep timer is active. Closing the app will CANCEL it.\n\n"
                                           "Cancel sleep and close?"):
                        self._cancel_shutdown()
                        self._quit_app()
                    return

                answer = self._ask_yes_no_cancel(
                    "Warning",
                    f"A {self.mode_var} is scheduled.\n\n"
                    f"• Yes  — cancel the {self.mode_var} and close\n"
                    f"• No   — close and keep the {self.mode_var} running\n"
                    "• Cancel — go back")
                if answer is None:
                    return
                elif answer:
                    self._cancel_shutdown()
            self._quit_app()


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Single instance check using a named Mutex
    if IS_WIN:
        kernel32 = ctypes.windll.kernel32
        mutex_name = "Global\\LightsOut_SingleInstance_Mutex"
        # CreateMutexW(security_attributes, initial_owner, name)
        mutex = kernel32.CreateMutexW(None, False, mutex_name)
        last_error = kernel32.GetLastError()
        
        # ERROR_ALREADY_EXISTS = 183
        if last_error == 183:
            # Another instance is already running.
            # Only bring the window to front if the user launched this manually
            # (not via the startup registry with --minimized).
            if "--minimized" not in sys.argv:
                hwnd = ctypes.windll.user32.FindWindowW("TkTopLevel", "⏻ Lights Out")
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            sys.exit(0)

    root = tk.Tk()
    root.configure(bg="#0f0f1a")

    # Suppress the window immediately — before ShutdownApp builds any UI —
    # so the default Tk window never flashes on screen at startup.
    # overrideredirect must come first; on Windows it calls deiconify internally
    # which would undo a preceding withdraw().
    root.overrideredirect(True)
    if "--minimized" in sys.argv:
        root.withdraw()

    app = ShutdownApp(root)
    root.mainloop()
    
    #uvx pyinstaller Lights_Out.spec