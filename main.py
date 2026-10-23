"""SlideFlow desktop application."""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.parse import parse_qs, urlparse

import customtkinter as ctk
from PIL import Image

from online_extractor import ExtractionResult, OnlineSlideExtractor, PlaylistResult, PlaylistVideo
from slide_history import SlideHistoryStore
from slide_queue import SequentialSlideQueue, SlideQueueJob
from slideflow_ipc import LocalIPCServer
from transcript_extractor import AudioTranscriptBatchExtractor, TranscriptBatchExtractor, TranscriptBatchResult


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class SlideFlowApp(ctk.CTk):
    BG = "#191614"
    CARD = "#24201D"
    TOPBAR = "#211D1A"
    SURFACE = "#322B27"
    SURFACE_HOVER = "#433731"
    BORDER = "#52443D"
    NAVY = "#FFFFFF"
    ORANGE = "#D24726"
    ORANGE_HOVER = "#B83A20"
    MUTED = "#C6B9B2"
    ACCENT_SOFT = "#4A251B"
    ACCENT_TEXT = "#FFB59E"
    # macOS and Tk 9 already report high-resolution scroll deltas. Keeping a
    # 1:1 pixel mapping preserves the trackpad's native acceleration.
    SCROLL_PIXELS_PER_DELTA = 1
    MAX_SCROLL_PIXELS = 96
    SCROLL_BUTTON_PIXELS = 42
    HISTORY_VISIBLE_LIMIT = 10
    PROCESSING_PRESETS = {
        "Nhanh": (15, 0.84),
        "Cân bằng": (7, 0.92),
        "Chi tiết": (4, 0.96),
    }
    COOKIE_BROWSER_OPTIONS = {
        "Chrome": "chrome",
        "Safari": "safari",
        "Firefox": "firefox",
        "Tắt": None,
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("SlideFlow")
        self.geometry("1080x720")
        self.minsize(920, 640)
        self.configure(fg_color=self.BG)
        self.cancel_event = threading.Event()
        self.result: ExtractionResult | None = None
        self.script_result: TranscriptBatchResult | None = None
        self.active_job_kind = "slide"
        self.worker_running = False
        self.slide_jobs = SequentialSlideQueue()
        self.slide_batch_total = 0
        self.slide_batch_completed = 0
        self.slide_batch_failures: list[tuple[str, str]] = []
        self.stopping_slide_queue = False
        self.playlist_loading = False
        self.playlist_dialog: ctk.CTkToplevel | None = None
        self.ipc_server = LocalIPCServer(self.dispatch_extension_message)
        self.preview_images: list[ctk.CTkImage] = []
        self.output_var = ctk.StringVar(value=str(Path.home() / "Documents" / "SlideFlow"))
        self.slide_history = SlideHistoryStore()
        self.slide_history.bootstrap(self.output_var.get())
        self.playlist_scroll_canvas = None
        self.preset_var = ctk.StringVar(value="Cân bằng")
        self.custom_interval_var = ctk.IntVar(value=7)
        self.custom_threshold_var = ctk.DoubleVar(value=0.92)
        self.quality_var = ctk.StringVar(value="1080p")
        self.transcript_var = ctk.BooleanVar(value=True)
        self.cookie_browser_var = ctk.StringVar(value="Chrome")
        with Image.open(self.resource_path("assets/SlideFlowToolbar.png")) as icon:
            brand_icon = icon.copy()
        self.brand_image = ctk.CTkImage(
            light_image=brand_icon,
            dark_image=brand_icon,
            size=(36, 36),
        )
        self.build_ui()
        self.bind("<Return>", self.start_from_keyboard)
        self.bind("<Escape>", lambda _event: self.cancel_extraction())
        self.install_scroll_bindings()
        self.bind("<Prior>", lambda _event: self.scroll_page(-1))
        self.bind("<Next>", lambda _event: self.scroll_page(1))
        self.install_window_lifecycle()
        self.after_idle(lambda: self.body._parent_canvas.yview_moveto(0))
        try:
            self.ipc_server.start()
        except OSError:
            # Another SlideFlow instance may already own the private local socket.
            pass

    @staticmethod
    def resource_path(relative_path: str) -> Path:
        """Resolve bundled assets in both source and PyInstaller builds."""
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return base / relative_path

    def build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # A compact command bar keeps the primary action visible, following
        # the desktop-utility pattern used by mature download managers.
        toolbar = ctk.CTkFrame(self, corner_radius=0, height=76, fg_color=self.TOPBAR)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.grid_columnconfigure(2, weight=1)
        toolbar.grid_propagate(False)

        brand = ctk.CTkFrame(toolbar, fg_color="transparent")
        brand.grid(row=0, column=0, padx=(22, 20), pady=14, sticky="w")
        ctk.CTkLabel(
            brand,
            text="",
            image=self.brand_image,
            width=36,
            height=36,
            fg_color="transparent",
        ).grid(row=0, column=0, rowspan=2, padx=(0, 10))
        ctk.CTkLabel(
            brand,
            text="SlideFlow",
            text_color=self.NAVY,
            font=ctk.CTkFont(size=19, weight="bold"),
        ).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(
            brand,
            text="VIDEO  →  SLIDE",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=1, column=1, sticky="nw")

        ctk.CTkButton(
            toolbar,
            text="＋  Dán liên kết",
            width=146,
            height=44,
            corner_radius=9,
            fg_color="#FFFFFF",
            hover_color="#F4EAE6",
            text_color=self.ORANGE,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.paste_url,
        ).grid(row=0, column=1, padx=(0, 10), pady=16)

        self.url_entry = ctk.CTkEntry(
            toolbar,
            height=44,
            corner_radius=9,
            border_width=1,
            border_color=self.BORDER,
            fg_color="#151210",
            text_color=self.NAVY,
            placeholder_text="Dán liên kết YouTube…",
            placeholder_text_color="#968982",
            font=ctk.CTkFont(size=14),
        )
        self.url_entry.grid(row=0, column=2, sticky="ew", padx=(0, 10), pady=16)

        self.script_button = ctk.CTkButton(
            toolbar,
            text="Lấy script",
            width=116,
            height=44,
            corner_radius=9,
            fg_color=self.SURFACE,
            hover_color=self.SURFACE_HOVER,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.NAVY,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.start_transcripts,
        )
        self.script_button.grid(row=0, column=3, padx=(0, 8), pady=16)

        self.audio_script_button = ctk.CTkButton(
            toolbar,
            text="Chép từ âm thanh",
            width=146,
            height=44,
            corner_radius=9,
            fg_color=self.SURFACE,
            hover_color=self.SURFACE_HOVER,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.NAVY,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self.start_transcripts(audio=True),
        )
        self.audio_script_button.grid(row=0, column=4, padx=(0, 8), pady=16)

        self.start_button = ctk.CTkButton(
            toolbar,
            text="Tạo slide",
            width=124,
            height=44,
            corner_radius=9,
            fg_color=self.ORANGE,
            hover_color=self.ORANGE_HOVER,
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.start_extraction,
        )
        self.start_button.grid(row=0, column=5, padx=(0, 22), pady=16)

        settings_bar = ctk.CTkFrame(
            self,
            corner_radius=0,
            height=94,
            fg_color="#211D1A",
            border_width=0,
        )
        settings_bar.grid(row=1, column=0, sticky="ew")
        settings_bar.grid_columnconfigure(4, weight=1)
        settings_bar.grid_propagate(False)

        self.preset = self.setting_menu(
            settings_bar,
            0,
            "MỨC CHI TIẾT",
            self.preset_var,
            [*self.PROCESSING_PRESETS, "Tùy chỉnh"],
            148,
        )
        self.preset.configure(command=self.on_preset_change)
        self.quality = self.setting_menu(
            settings_bar, 1, "CHẤT LƯỢNG", self.quality_var, ["480p", "720p", "1080p"], 132
        )
        self.cookie_browser_menu = self.setting_menu(
            settings_bar, 2, "XÁC THỰC", self.cookie_browser_var, list(self.COOKIE_BROWSER_OPTIONS), 132
        )

        transcript_box = ctk.CTkFrame(settings_bar, fg_color="transparent")
        transcript_box.grid(row=0, column=3, padx=(18, 16), pady=(8, 12), sticky="w")
        ctk.CTkLabel(
            transcript_box,
            text="PHỤ ĐỀ",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.transcript_check = ctk.CTkSwitch(
            transcript_box,
            text="Kèm slide nếu có",
            variable=self.transcript_var,
            width=112,
            progress_color=self.ORANGE,
            button_color="#FFFFFF",
            button_hover_color="#E8E8EC",
            text_color=self.NAVY,
            font=ctk.CTkFont(size=12),
        )
        self.transcript_check.grid(row=1, column=0, sticky="w")

        save_box = ctk.CTkFrame(settings_bar, fg_color="transparent")
        save_box.grid(row=0, column=4, padx=(8, 22), pady=(8, 12), sticky="e")
        ctk.CTkLabel(
            save_box,
            text="LƯU VÀO",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.output_button = ctk.CTkButton(
            save_box,
            text="",
            width=220,
            height=34,
            corner_radius=8,
            fg_color=self.SURFACE,
            hover_color=self.SURFACE_HOVER,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.NAVY,
            anchor="w",
            command=self.choose_output,
        )
        self.output_button.grid(row=1, column=0, sticky="ew")
        self.refresh_output_button()

        self.custom_panel = ctk.CTkFrame(
            self,
            corner_radius=0,
            height=92,
            fg_color="#2A231F",
            border_width=0,
        )
        self.custom_panel.grid(row=2, column=0, sticky="ew")
        self.custom_panel.grid_columnconfigure((1, 2), weight=1)
        self.custom_panel.grid_propagate(False)

        custom_intro = ctk.CTkFrame(self.custom_panel, fg_color="transparent")
        custom_intro.grid(row=0, column=0, padx=(22, 24), pady=(8, 10), sticky="w")
        ctk.CTkLabel(
            custom_intro,
            text="Tùy chỉnh",
            text_color=self.NAVY,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            custom_intro,
            text="Tinh chỉnh để ưu tiên tốc độ hoặc độ nhạy",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        interval_box = ctk.CTkFrame(self.custom_panel, fg_color="transparent")
        interval_box.grid(row=0, column=1, padx=(0, 28), pady=(8, 12), sticky="ew")
        interval_box.grid_columnconfigure(0, weight=1)
        self.custom_interval_label = ctk.CTkLabel(
            interval_box,
            text="Khoảng lấy mẫu  •  7 giây",
            text_color=self.NAVY,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.custom_interval_label.grid(row=0, column=0, sticky="w")
        self.custom_interval_slider = ctk.CTkSlider(
            interval_box,
            from_=2,
            to=60,
            number_of_steps=58,
            variable=self.custom_interval_var,
            height=16,
            button_length=16,
            progress_color=self.ORANGE,
            fg_color="#493A33",
            button_color="#FFFFFF",
            button_hover_color="#FFE1D6",
            command=self.update_custom_labels,
        )
        self.custom_interval_slider.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        threshold_box = ctk.CTkFrame(self.custom_panel, fg_color="transparent")
        threshold_box.grid(row=0, column=2, padx=(0, 24), pady=(8, 12), sticky="ew")
        threshold_box.grid_columnconfigure(0, weight=1)
        self.custom_threshold_label = ctk.CTkLabel(
            threshold_box,
            text="Độ nhạy thay đổi  •  92%",
            text_color=self.NAVY,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.custom_threshold_label.grid(row=0, column=0, sticky="w")
        self.custom_threshold_slider = ctk.CTkSlider(
            threshold_box,
            from_=0.75,
            to=0.99,
            number_of_steps=24,
            variable=self.custom_threshold_var,
            height=16,
            button_length=16,
            progress_color=self.ORANGE,
            fg_color="#493A33",
            button_color="#FFFFFF",
            button_hover_color="#FFE1D6",
            command=self.update_custom_labels,
        )
        self.custom_threshold_slider.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.custom_reset_button = ctk.CTkButton(
            self.custom_panel,
            text="Đặt lại",
            width=76,
            height=32,
            corner_radius=8,
            fg_color="transparent",
            hover_color=self.SURFACE_HOVER,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.MUTED,
            command=self.reset_custom_settings,
        )
        self.custom_reset_button.grid(row=0, column=3, padx=(0, 22), pady=(24, 26))
        self.custom_panel.grid_remove()

        navigation = ctk.CTkFrame(self, corner_radius=0, height=50, fg_color=self.BG)
        navigation.grid(row=3, column=0, sticky="ew", padx=22)
        navigation.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            navigation,
            text="TIẾN TRÌNH",
            text_color=self.NAVY,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w", pady=(18, 10))
        quick_actions = ctk.CTkFrame(navigation, fg_color="transparent")
        quick_actions.grid(row=0, column=1, sticky="e", pady=(10, 4))
        ctk.CTkButton(
            quick_actions,
            text="Mở PDF gần nhất",
            width=132,
            height=30,
            corner_radius=7,
            fg_color="transparent",
            hover_color=self.SURFACE,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.MUTED,
            command=self.open_latest_pdf,
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            quick_actions,
            text="Mở thư mục",
            width=108,
            height=30,
            corner_radius=7,
            fg_color="transparent",
            hover_color=self.SURFACE,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.MUTED,
            command=self.open_output_folder,
        ).grid(row=0, column=1)

        body = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color="#765040",
            scrollbar_button_hover_color="#98604B",
        )
        body.grid(row=4, column=0, sticky="nsew", padx=22, pady=(0, 10))
        body.grid_columnconfigure(0, weight=1)
        self.body = body
        # Keep canvas positioning fine-grained.  Pointer input is converted to
        # pixels below so a short trackpad gesture is not lost to unit rounding.
        self.body._parent_canvas.configure(yscrollincrement=1)

        self.action_card = self.card(body)
        self.action_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.action_card.grid_columnconfigure(1, weight=1)
        status_icon = ctk.CTkLabel(
            self.action_card,
            text="▶",
            width=54,
            height=54,
            corner_radius=12,
            fg_color=self.ACCENT_SOFT,
            text_color=self.ACCENT_TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        status_icon.grid(row=0, column=0, rowspan=3, padx=(22, 16), pady=22, sticky="n")
        self.status_label = ctk.CTkLabel(
            self.action_card,
            text="Sẵn sàng",
            text_color=self.NAVY,
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.status_label.grid(row=0, column=1, padx=(0, 16), pady=(22, 2), sticky="w")
        self.status_detail = ctk.CTkLabel(
            self.action_card,
            text="Dán một liên kết YouTube ở thanh trên để bắt đầu.",
            text_color=self.MUTED,
            justify="left",
            anchor="w",
        )
        self.status_detail.grid(row=1, column=1, padx=(0, 16), pady=(0, 13), sticky="ew")
        self.progress_bar = ctk.CTkProgressBar(
            self.action_card,
            height=8,
            corner_radius=4,
            progress_color=self.ORANGE,
            fg_color="#493A33",
        )
        self.progress_bar.grid(row=2, column=1, sticky="ew", padx=(0, 16), pady=(0, 22))
        self.progress_bar.set(0)
        self.cancel_button = ctk.CTkButton(
            self.action_card,
            text="Dừng",
            width=86,
            height=36,
            corner_radius=8,
            fg_color=self.SURFACE,
            hover_color=self.SURFACE_HOVER,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.MUTED,
            state="disabled",
            command=self.cancel_extraction,
        )
        self.cancel_button.grid(row=0, column=2, rowspan=3, padx=(0, 22), pady=22)

        self.queue_card = self.card(body)
        self.queue_card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.queue_card.grid_columnconfigure(0, weight=1)
        self.queue_card.grid_remove()

        self.result_card = self.card(body)
        self.result_card.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.result_card.grid_columnconfigure(0, weight=1)
        self.result_card.grid_remove()

        self.history_card = self.card(body)
        self.history_card.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self.history_card.grid_columnconfigure(0, weight=1)
        self.refresh_history_card()

        privacy_card = ctk.CTkFrame(
            body,
            fg_color="#211D1A",
            corner_radius=12,
            border_width=1,
            border_color="#433731",
        )
        privacy_card.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        privacy_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            privacy_card,
            text="✓",
            width=28,
            height=28,
            corner_radius=14,
            fg_color=self.ACCENT_SOFT,
            text_color=self.ACCENT_TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=(16, 10), pady=14)
        ctk.CTkLabel(
            privacy_card,
            text=(
                "Xử lý trực tuyến, không lưu video đầy đủ  •  "
                "Cookie chỉ được đọc khi YouTube yêu cầu xác thực và không được xuất ra tệp"
            ),
            text_color=self.MUTED,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, sticky="w", pady=14)

        footer = ctk.CTkFrame(self, corner_radius=0, height=30, fg_color="#151210")
        footer.grid(row=5, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer,
            text="ONLINE • RIÊNG TƯ",
            text_color="#9A8E88",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, padx=22, pady=7, sticky="w")
        ctk.CTkLabel(
            footer,
            text="Enter để bắt đầu  •  Esc để dừng",
            text_color="#9A8E88",
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=1, padx=22, pady=7, sticky="e")

    def setting_menu(self, parent, column: int, title: str, variable, values: list[str], width: int):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(
            row=0,
            column=column,
            padx=(22 if column == 0 else 8, 8),
            pady=(8, 12),
            sticky="w",
        )
        ctk.CTkLabel(
            box,
            text=title,
            text_color=self.MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        menu = ctk.CTkOptionMenu(
            box,
            values=values,
            variable=variable,
            width=width,
            height=34,
            corner_radius=8,
            fg_color=self.SURFACE,
            button_color=self.SURFACE_HOVER,
            button_hover_color="#5A4941",
            text_color=self.NAVY,
            dropdown_fg_color=self.SURFACE,
            dropdown_hover_color=self.SURFACE_HOVER,
            dropdown_text_color=self.NAVY,
        )
        menu.grid(row=1, column=0, sticky="w")
        return menu

    def refresh_output_button(self) -> None:
        if not hasattr(self, "output_button"):
            return
        path = Path(self.output_var.get()).expanduser()
        label = path.name or str(path)
        self.output_button.configure(text=f"⌄  {label}")

    def on_preset_change(self, value: str) -> None:
        if value == "Tùy chỉnh":
            self.update_custom_labels()
            self.custom_panel.grid()
        else:
            self.custom_panel.grid_remove()

    def update_custom_labels(self, _value=None) -> None:
        interval = int(round(self.custom_interval_var.get()))
        sensitivity = int(round(self.custom_threshold_var.get() * 100))
        self.custom_interval_label.configure(text=f"Khoảng lấy mẫu  •  {interval} giây")
        self.custom_threshold_label.configure(text=f"Độ nhạy thay đổi  •  {sensitivity}%")

    def reset_custom_settings(self) -> None:
        self.custom_interval_var.set(7)
        self.custom_threshold_var.set(0.92)
        self.update_custom_labels()

    @classmethod
    def processing_settings(
        cls,
        preset: str,
        custom_interval: float = 7,
        custom_threshold: float = 0.92,
    ) -> tuple[int, float]:
        if preset in cls.PROCESSING_PRESETS:
            return cls.PROCESSING_PRESETS[preset]
        interval = max(2, min(60, int(round(custom_interval))))
        threshold = max(0.75, min(0.99, float(custom_threshold)))
        return interval, threshold

    def card(self, parent) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            parent,
            fg_color=self.CARD,
            corner_radius=12,
            border_width=1,
            border_color=self.BORDER,
        )

    def section_title(self, parent, number: str, title: str, subtitle: str, span: int = 1) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=0, column=0, columnspan=span, sticky="ew", padx=28, pady=(22, 4))
        ctk.CTkLabel(
            frame,
            text=number,
            width=30,
            height=30,
            corner_radius=15,
            fg_color=self.ACCENT_SOFT,
            text_color=self.ACCENT_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, rowspan=2, padx=(0, 12))
        ctk.CTkLabel(frame, text=title, text_color=self.NAVY, font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(frame, text=subtitle, text_color=self.MUTED, font=ctk.CTkFont(size=12)).grid(row=1, column=1, sticky="w")

    def field_label(self, parent, text: str, row: int, column: int) -> None:
        ctk.CTkLabel(parent, text=text, text_color=self.NAVY, font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=row, column=column, sticky="w", padx=28, pady=(12, 0)
        )

    def scroll_with_trackpad(self, event) -> str | None:
        """Handle mouse wheels and trackpads without amplifying momentum."""
        delta = int(getattr(event, "delta", 0))
        if not delta:
            return None
        direction = -1 if delta > 0 else 1
        pixels = min(self.MAX_SCROLL_PIXELS, max(1, abs(delta)) * self.SCROLL_PIXELS_PER_DELTA)
        return self.scroll_body_pixels(direction * pixels)

    @staticmethod
    def decode_touchpad_delta(raw_delta: int) -> tuple[int, int]:
        """Decode Tk 9's packed signed 16-bit horizontal and vertical deltas."""
        raw = int(raw_delta) & 0xFFFFFFFF
        delta_y = raw & 0xFFFF
        delta_x = (raw >> 16) & 0xFFFF
        if delta_y >= 0x8000:
            delta_y -= 0x10000
        if delta_x >= 0x8000:
            delta_x -= 0x10000
        return delta_x, delta_y

    def scroll_with_touchpad(self, event) -> str | None:
        """Handle the TouchpadScroll event introduced by Tk 9 on macOS."""
        _delta_x, delta_y = self.decode_touchpad_delta(getattr(event, "delta", 0))
        if not delta_y:
            return None
        # Tk reports a positive Y delta when the content should move towards
        # the top.  Canvas yview_scroll uses the opposite sign: negative moves
        # towards the top and positive towards the bottom.
        direction = -1 if delta_y > 0 else 1
        pixels = min(
            self.MAX_SCROLL_PIXELS,
            abs(delta_y) * self.SCROLL_PIXELS_PER_DELTA,
        )
        return self.scroll_body_pixels(direction * pixels)

    def active_scroll_canvas(self):
        """Prefer the modal playlist list while that dialog is open."""
        dialog = getattr(self, "playlist_dialog", None)
        playlist_canvas = getattr(self, "playlist_scroll_canvas", None)
        if dialog is not None and playlist_canvas is not None:
            try:
                if dialog.winfo_exists():
                    return playlist_canvas
            except Exception:
                pass
        return self.body._parent_canvas

    def scroll_body_pixels(self, amount: int) -> str:
        """Scroll by physical pixels and clamp trackpad momentum at the edges."""
        canvas = SlideFlowApp.active_scroll_canvas(self)
        first, last = (float(value) for value in canvas.yview())
        epsilon = 1e-6
        visible_fraction = max(epsilon, last - first)
        content_fits = first <= epsilon and last >= 1.0 - epsilon
        reached_top = amount < 0 and first <= epsilon
        reached_bottom = amount > 0 and last >= 1.0 - epsilon
        if content_fits or reached_top or reached_bottom:
            return "break"

        viewport_height = max(1, int(canvas.winfo_height()))
        fraction_delta = amount * visible_fraction / viewport_height
        max_first = max(0.0, 1.0 - visible_fraction)
        canvas.yview_moveto(min(max(first + fraction_delta, 0.0), max_first))
        return "break"

    def scroll_body(self, amount: int, mode: str = "units") -> str:
        """Consume momentum at either edge instead of letting it overscroll."""
        canvas = SlideFlowApp.active_scroll_canvas(self)
        first, last = (float(value) for value in canvas.yview())
        epsilon = 1e-6
        content_fits = first <= epsilon and last >= 1.0 - epsilon
        reached_top = amount < 0 and first <= epsilon
        reached_bottom = amount > 0 and last >= 1.0 - epsilon
        if not content_fits and not reached_top and not reached_bottom:
            canvas.yview_scroll(amount, mode)
        return "break"

    def install_scroll_bindings(self) -> None:
        """Install wheel handling and Tk 9's app-wide trackpad workaround."""
        tag = "SlideFlowTrackpad"
        self.bind_class(tag, "<MouseWheel>", self.scroll_with_trackpad)
        self.bind_class(tag, "<Button-4>", lambda event: self.scroll_with_buttons(event, -1))
        self.bind_class(tag, "<Button-5>", lambda event: self.scroll_with_buttons(event, 1))

        # On macOS with Tk 9, trackpads emit TouchpadScroll instead of
        # MouseWheel.  Binding this event to individual CustomTkinter widgets
        # is unreliable; Tk's working pattern is an application-wide binding.
        # Keep the binding idempotent because result widgets are rebuilt after
        # every extraction and call this method again.
        if not getattr(self, "_touchpad_scroll_bound", False):
            try:
                self.bind_all("<TouchpadScroll>", self.scroll_with_touchpad, add="+")
            except Exception:
                # Tk 8 does not recognize this Tk 9 event type.
                pass
            else:
                self._touchpad_scroll_bound = True

        def attach(widget) -> None:
            tags = widget.bindtags()
            if tag not in tags:
                widget.bindtags((tag, *tags))
            for child in widget.winfo_children():
                attach(child)

        attach(self)

    def scroll_with_buttons(self, _event, direction: int) -> str:
        return self.scroll_body_pixels(direction * self.SCROLL_BUTTON_PIXELS)

    def scroll_page(self, direction: int) -> str:
        return self.scroll_body(direction, "pages")

    def install_window_lifecycle(self) -> None:
        """Close the window while keeping SlideFlow alive in the Dock."""
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.bind_all("<Command-q>", self.quit_application, add="+")
        try:
            self.tk.createcommand("::tk::mac::ReopenApplication", self.show_window)
            self.tk.createcommand("::tk::mac::Quit", self.quit_application)
        except Exception:
            # These commands are specific to the macOS Tk windowing system.
            pass

    def hide_window(self) -> None:
        self.withdraw()

    def show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.after_idle(self.focus_force)

    def quit_application(self, _event=None) -> str:
        self.cancel_event.set()
        if ipc_server := getattr(self, "ipc_server", None):
            ipc_server.stop()
        self.destroy()
        return "break"

    def paste_url(self) -> None:
        try:
            value = self.clipboard_get().strip()
        except Exception:
            messagebox.showinfo("Clipboard", "Không tìm thấy liên kết trong clipboard.")
            return
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, value)
        self.url_entry.focus_set()

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get(), title="Chọn thư mục lưu kết quả")
        if selected:
            self.output_var.set(selected)
            self.refresh_output_button()

    @staticmethod
    def history_time_label(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y • %H:%M")

    def refresh_history_card(self) -> None:
        if not hasattr(self, "history_card"):
            return
        for child in self.history_card.winfo_children():
            child.destroy()

        entries = self.slide_history.entries
        visible_entries = entries[: self.HISTORY_VISIBLE_LIMIT]
        header = ctk.CTkFrame(self.history_card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(20, 10))
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            header,
            text="↺",
            width=30,
            height=30,
            corner_radius=15,
            fg_color=self.ACCENT_SOFT,
            text_color=self.ACCENT_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, rowspan=2, padx=(0, 12))
        ctk.CTkLabel(
            header,
            text="Lịch sử",
            text_color=self.NAVY,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            header,
            text=f"{len(entries)} bài đã tạo slide" if entries else "Các bài đã tạo slide sẽ xuất hiện tại đây",
            text_color=self.MUTED,
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=1, sticky="w")
        ctk.CTkButton(
            header,
            text="Xóa lịch sử",
            width=86,
            height=28,
            corner_radius=7,
            fg_color="transparent",
            hover_color=self.SURFACE_HOVER,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.MUTED,
            font=ctk.CTkFont(size=11),
            state="normal" if entries else "disabled",
            command=self.clear_slide_history,
        ).grid(row=0, column=2, rowspan=2, padx=(12, 0))

        if not entries:
            ctk.CTkLabel(
                self.history_card,
                text="Chưa có PDF slide nào trong lịch sử.",
                height=46,
                corner_radius=9,
                fg_color="#211D1A",
                text_color=self.MUTED,
                anchor="w",
            ).grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 22), ipadx=14)
            return

        for row_number, entry in enumerate(visible_entries, start=1):
            row = ctk.CTkFrame(self.history_card, fg_color="#211D1A", corner_radius=9)
            row.grid(row=row_number, column=0, sticky="ew", padx=28, pady=(0, 7))
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                row,
                text=str(row_number),
                width=28,
                height=28,
                corner_radius=14,
                fg_color=self.ACCENT_SOFT,
                text_color=self.ACCENT_TEXT,
                font=ctk.CTkFont(size=10, weight="bold"),
            ).grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
            ctk.CTkLabel(
                row,
                text=entry.title,
                text_color=self.NAVY,
                font=ctk.CTkFont(size=13, weight="bold"),
                anchor="w",
            ).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(8, 0))
            ctk.CTkLabel(
                row,
                text=f"{self.history_time_label(entry.created_at)} • {Path(entry.output_dir).name}",
                text_color=self.MUTED,
                font=ctk.CTkFont(size=11),
                anchor="w",
            ).grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(0, 8))
            ctk.CTkButton(
                row,
                text="Mở PDF",
                width=76,
                height=30,
                corner_radius=7,
                fg_color=self.SURFACE,
                hover_color=self.SURFACE_HOVER,
                text_color=self.NAVY,
                command=lambda path=entry.pdf_path: self.open_path(path),
            ).grid(row=0, column=2, rowspan=2, padx=(0, 12), pady=10)
        ctk.CTkFrame(self.history_card, height=9, fg_color="transparent").grid(
            row=len(visible_entries) + 1,
            column=0,
        )
        if len(entries) > len(visible_entries):
            ctk.CTkLabel(
                self.history_card,
                text=f"Đang hiển thị {len(visible_entries)} bài gần nhất trong {len(entries)} bài.",
                text_color=self.MUTED,
                font=ctk.CTkFont(size=11),
            ).grid(row=len(visible_entries) + 2, column=0, pady=(0, 14))
        self.after_idle(self.install_scroll_bindings)

    def clear_slide_history(self) -> None:
        if not self.slide_history.entries:
            return
        confirmed = messagebox.askyesno(
            "Xóa lịch sử",
            "Xóa danh sách lịch sử? Các tệp PDF đã tạo vẫn được giữ nguyên.",
            parent=self,
        )
        if not confirmed:
            return
        if not self.slide_history.clear():
            messagebox.showerror("Không thể xóa lịch sử", "SlideFlow không thể cập nhật tệp lịch sử.")
            return
        self.refresh_history_card()
        self.status_detail.configure(text="Đã xóa lịch sử; các tệp PDF vẫn được giữ nguyên.")

    @staticmethod
    def find_latest_pdf(output_dir: str) -> Path | None:
        root = Path(output_dir).expanduser()
        if not root.is_dir():
            return None
        candidates = [path for path in root.rglob("* - Slides.pdf") if path.is_file()]
        return max(candidates, key=lambda path: path.stat().st_mtime, default=None)

    def open_latest_pdf(self) -> None:
        latest = self.find_latest_pdf(self.output_var.get().strip())
        if latest is None:
            messagebox.showinfo(
                "Chưa có slide",
                "Chưa tìm thấy PDF slide trong nơi lưu đang chọn.",
            )
            return
        self.open_path(str(latest))

    def open_output_folder(self) -> None:
        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror("Thiếu nơi lưu", "Hãy chọn thư mục lưu kết quả.")
            return
        folder = Path(output).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Không thể mở thư mục", str(error))
            return
        self.open_path(str(folder))

    @staticmethod
    def valid_url(value: str) -> bool:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and host in {
            "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"
        }

    @staticmethod
    def is_playlist_url(value: str) -> bool:
        return bool(parse_qs(urlparse(value).query).get("list"))

    def prepare_output_directory(self) -> str | None:
        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror("Thiếu nơi lưu", "Hãy chọn thư mục lưu kết quả.")
            return None
        try:
            Path(output).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Không thể lưu", str(error))
            return None
        return output

    def dispatch_extension_message(self, message: dict) -> dict:
        """Move a Native Messaging request safely onto Tk's main thread."""
        completed = threading.Event()
        response: dict = {}

        def handle() -> None:
            try:
                response.update(self.handle_extension_message(message))
            except Exception as error:
                response.update({"ok": False, "error": str(error)})
            finally:
                completed.set()

        try:
            self.after(0, handle)
        except Exception as error:
            return {"ok": False, "error": str(error)}
        if not completed.wait(4.0):
            return {"ok": False, "error": "SlideFlow đang bận và chưa nhận được liên kết."}
        return response

    def handle_extension_message(self, message: dict) -> dict:
        action = str(message.get("action") or "")
        url = str(message.get("url") or "").strip()
        if action not in {"enqueue", "playlist"}:
            return {"ok": False, "error": "Yêu cầu từ extension không được hỗ trợ."}
        if not self.valid_url(url):
            return {"ok": False, "error": "Extension không gửi một liên kết YouTube hợp lệ."}
        if self.stopping_slide_queue:
            return {"ok": False, "error": "SlideFlow đang dừng hàng chờ; hãy thử lại sau."}

        output = self.output_var.get().strip()
        if not output:
            return {"ok": False, "error": "SlideFlow chưa có thư mục lưu kết quả."}
        try:
            Path(output).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return {"ok": False, "error": f"Không thể dùng thư mục lưu: {error}"}

        if action == "playlist":
            if self.playlist_loading:
                return {"ok": False, "error": "SlideFlow đang đọc một playlist khác."}
            self.show_window()
            self.start_playlist_lookup(url, output)
            return {
                "ok": True,
                "message": "Đã gửi playlist; chọn video trong SlideFlow.",
                "requires_selection": True,
            }

        raw_title = str(message.get("title") or "").strip()
        display_title = OnlineSlideExtractor.safe_name(
            raw_title,
            self.single_video_label(url),
        )
        self.enqueue_slide_jobs([self.make_slide_job(url, display_title, output)])
        return {
            "ok": True,
            "message": "Đã thêm video vào hàng chờ SlideFlow.",
        }

    def make_slide_job(self, video_url: str, display_title: str, output: str) -> SlideQueueJob:
        interval, threshold = self.processing_settings(
            self.preset_var.get(),
            self.custom_interval_var.get(),
            self.custom_threshold_var.get(),
        )
        return SlideQueueJob(
            video_url=video_url,
            display_title=display_title,
            output_dir=output,
            interval=interval,
            similarity_threshold=threshold,
            quality=int(self.quality_var.get().removesuffix("p")),
            save_transcript=self.transcript_var.get(),
            cookie_browser=self.COOKIE_BROWSER_OPTIONS[self.cookie_browser_var.get()],
        )

    @staticmethod
    def single_video_label(url: str) -> str:
        parsed = urlparse(url)
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if not video_id and (parsed.hostname or "").lower() == "youtu.be":
            video_id = parsed.path.strip("/")
        return f"Video {video_id}" if video_id else "Video YouTube"

    def start_extraction(self) -> None:
        url = self.url_entry.get().strip()
        if not self.valid_url(url):
            messagebox.showerror("Liên kết chưa đúng", "Hãy nhập một liên kết YouTube hợp lệ.")
            return
        if self.stopping_slide_queue:
            messagebox.showinfo("Đang dừng", "Hãy đợi tác vụ hiện tại dừng hẳn trước khi thêm video mới.")
            return
        output = self.prepare_output_directory()
        if output is None:
            return

        if self.is_playlist_url(url):
            self.start_playlist_lookup(url, output)
            return

        job = self.make_slide_job(url, self.single_video_label(url), output)
        self.enqueue_slide_jobs([job])

    def start_playlist_lookup(self, url: str, output: str) -> None:
        if self.playlist_loading:
            return
        self.playlist_loading = True
        self.update_slide_start_button()
        if not self.worker_running:
            self.progress_bar.set(0.02)
            self.status_label.configure(text="Đang đọc playlist…")
            self.status_detail.configure(text="Đang lấy danh sách video; chưa tải nội dung video.")
        extractor = OnlineSlideExtractor(
            video_url=url,
            output_dir=output,
            cookie_browser=self.COOKIE_BROWSER_OPTIONS[self.cookie_browser_var.get()],
        )
        threading.Thread(
            target=self.run_playlist_lookup,
            args=(extractor,),
            daemon=True,
        ).start()

    def run_playlist_lookup(self, extractor: OnlineSlideExtractor) -> None:
        try:
            result = extractor.resolve_playlist()
        except Exception as error:
            self.after(0, self.finish_playlist_lookup_error, str(error))
        else:
            self.after(0, self.finish_playlist_lookup, result)

    def finish_playlist_lookup_error(self, error: str) -> None:
        self.playlist_loading = False
        self.update_slide_start_button()
        if not self.worker_running:
            self.progress_bar.set(0)
            self.status_label.configure(text="Không đọc được playlist")
            self.status_detail.configure(text=error[:180])
        messagebox.showerror("SlideFlow", error)

    def finish_playlist_lookup(self, result: PlaylistResult) -> None:
        self.playlist_loading = False
        self.update_slide_start_button()
        if not self.worker_running:
            self.progress_bar.set(0)
            self.status_label.configure(text=f"Đã tìm thấy {len(result.videos)} video")
            self.status_detail.configure(text="Chọn các video cần tạo slide rồi bấm Bắt đầu.")
        self.show_playlist_selection(result)

    @staticmethod
    def duration_label(duration: float | None) -> str:
        if not duration:
            return ""
        total = max(0, int(duration))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"

    def show_playlist_selection(self, result: PlaylistResult) -> None:
        if self.playlist_dialog is not None and self.playlist_dialog.winfo_exists():
            self.playlist_dialog.destroy()

        dialog = ctk.CTkToplevel(self)
        self.playlist_dialog = dialog
        dialog.title("Chọn video trong playlist")
        dialog.geometry("780x620")
        dialog.minsize(620, 460)
        dialog.configure(fg_color=self.BG)
        dialog.transient(self)
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        heading = ctk.CTkFrame(dialog, fg_color=self.TOPBAR, corner_radius=0)
        heading.grid(row=0, column=0, sticky="ew")
        heading.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            heading,
            text=result.title,
            text_color=self.NAVY,
            font=ctk.CTkFont(size=19, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 2))
        detail = f"{len(result.videos)} video có thể chọn"
        if result.unavailable_count:
            detail += f" • bỏ qua {result.unavailable_count} mục không khả dụng"
        ctk.CTkLabel(
            heading,
            text=detail,
            text_color=self.MUTED,
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 16))

        video_list = ctk.CTkScrollableFrame(
            dialog,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color="#765040",
            scrollbar_button_hover_color="#98604B",
        )
        video_list.grid(row=1, column=0, sticky="nsew", padx=18, pady=14)
        video_list.grid_columnconfigure(0, weight=1)
        video_list._parent_canvas.configure(yscrollincrement=1)
        self.playlist_scroll_canvas = video_list._parent_canvas

        selections: list[tuple[PlaylistVideo, ctk.BooleanVar]] = []
        for row_number, video in enumerate(result.videos):
            variable = ctk.BooleanVar(value=False)
            selections.append((video, variable))
            row = ctk.CTkFrame(
                video_list,
                fg_color=self.CARD,
                corner_radius=9,
                border_width=1,
                border_color=self.BORDER,
            )
            row.grid(row=row_number, column=0, sticky="ew", pady=(0, 7))
            row.grid_columnconfigure(1, weight=1)
            checkbox = ctk.CTkCheckBox(
                row,
                text="",
                variable=variable,
                width=28,
                checkbox_width=21,
                checkbox_height=21,
                fg_color=self.ORANGE,
                hover_color=self.ORANGE_HOVER,
                border_color=self.BORDER,
            )
            checkbox.grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=13)
            ctk.CTkLabel(
                row,
                text=f"{video.position:03d}. {video.title}",
                text_color=self.NAVY,
                font=ctk.CTkFont(size=13, weight="bold"),
                anchor="w",
                justify="left",
                wraplength=500,
            ).grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 1))
            metadata = video.video_id
            if duration := self.duration_label(video.duration):
                metadata += f" • {duration}"
            ctk.CTkLabel(
                row,
                text=metadata,
                text_color=self.MUTED,
                font=ctk.CTkFont(size=11),
                anchor="w",
            ).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 10))

        footer = ctk.CTkFrame(dialog, fg_color=self.TOPBAR, corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(2, weight=1)
        selection_label = ctk.CTkLabel(footer, text="Chưa chọn video", text_color=self.MUTED)
        selection_label.grid(row=0, column=2, padx=14, sticky="w")
        start_button = ctk.CTkButton(
            footer,
            text="Bắt đầu",
            width=132,
            height=40,
            corner_radius=8,
            fg_color=self.ORANGE,
            hover_color=self.ORANGE_HOVER,
            text_color="#FFFFFF",
        )

        def update_selection_count() -> None:
            selected_count = sum(variable.get() for _video, variable in selections)
            selection_label.configure(
                text=f"Đã chọn {selected_count}/{len(selections)} video"
                if selected_count else "Chưa chọn video"
            )
            start_button.configure(text=f"Bắt đầu ({selected_count})" if selected_count else "Bắt đầu")

        for _video, variable in selections:
            variable.trace_add("write", lambda *_args: update_selection_count())

        def select_all(selected: bool) -> None:
            for _video, variable in selections:
                variable.set(selected)

        def close_dialog() -> None:
            self.playlist_dialog = None
            self.playlist_scroll_canvas = None
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        def queue_selected() -> None:
            selected_videos = [video for video, variable in selections if variable.get()]
            if not selected_videos:
                messagebox.showinfo("Chưa chọn video", "Hãy chọn ít nhất một video trong playlist.", parent=dialog)
                return
            output = self.prepare_output_directory()
            if output is None:
                return
            jobs = [self.make_slide_job(video.url, video.title, output) for video in selected_videos]
            close_dialog()
            self.enqueue_slide_jobs(jobs)

        ctk.CTkButton(
            footer,
            text="Chọn tất cả",
            width=106,
            height=34,
            fg_color=self.SURFACE,
            hover_color=self.SURFACE_HOVER,
            command=lambda: select_all(True),
        ).grid(row=0, column=0, padx=(18, 7), pady=14)
        ctk.CTkButton(
            footer,
            text="Bỏ chọn",
            width=92,
            height=34,
            fg_color=self.SURFACE,
            hover_color=self.SURFACE_HOVER,
            command=lambda: select_all(False),
        ).grid(row=0, column=1, padx=(0, 4), pady=14)
        start_button.configure(command=queue_selected)
        start_button.grid(row=0, column=3, padx=(4, 18), pady=14)
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.grab_set()
        dialog.after_idle(self.install_scroll_bindings)
        dialog.after_idle(dialog.focus_force)

    def enqueue_slide_jobs(self, jobs: list[SlideQueueJob]) -> None:
        if not jobs:
            return
        new_batch = (
            self.slide_jobs.active is None
            and not len(self.slide_jobs)
            and (not self.worker_running or self.active_job_kind == "script")
        )
        if new_batch:
            self.slide_batch_total = 0
            self.slide_batch_completed = 0
            self.slide_batch_failures.clear()
            self.result_card.grid_remove()
        self.slide_jobs.add(jobs)
        self.slide_batch_total += len(jobs)
        self.refresh_slide_queue_card()
        self.update_slide_start_button()
        if not self.worker_running:
            self.start_next_slide_job()

    def start_next_slide_job(self) -> None:
        if self.worker_running or self.stopping_slide_queue:
            return
        job = self.slide_jobs.take_next()
        if job is None:
            self.finish_slide_batch()
            return

        self.active_job_kind = "slide"
        self.cancel_event.clear()
        self.set_running(True)
        self.progress_bar.set(0.01)
        self.status_label.configure(text="Đang chuẩn bị…")
        self.status_detail.configure(
            text=f"{job.display_title} • còn {len(self.slide_jobs)} video trong hàng chờ."
        )
        self.refresh_slide_queue_card()
        extractor = OnlineSlideExtractor(
            video_url=job.video_url,
            output_dir=job.output_dir,
            interval=job.interval,
            similarity_threshold=job.similarity_threshold,
            quality=job.quality,
            save_transcript=job.save_transcript,
            cookie_browser=job.cookie_browser,
            progress_callback=self.queue_progress,
            cancel_event=self.cancel_event,
        )
        threading.Thread(target=self.run_extraction, args=(extractor,), daemon=True).start()

    def start_transcripts(self, audio: bool = False) -> None:
        url = self.url_entry.get().strip()
        if not self.valid_url(url):
            messagebox.showerror("Liên kết chưa đúng", "Hãy nhập một liên kết YouTube hợp lệ.")
            return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror("Thiếu nơi lưu", "Hãy chọn thư mục lưu kết quả.")
            return
        try:
            Path(output).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Không thể lưu", str(error))
            return

        self.active_job_kind = "script"
        self.cancel_event.clear()
        self.set_running(True)
        self.result_card.grid_remove()
        self.progress_bar.set(0.01)
        self.status_label.configure(
            text="Đang chuẩn bị chép từ âm thanh…" if audio else "Đang chuẩn bị lấy script…"
        )
        self.status_detail.configure(
            text=(
                "Dùng lại script đã có; lần đầu tải mô hình Whisper khoảng 460 MB. "
                "Tiến độ tải và số phút đã nhận dạng sẽ hiển thị tại đây."
                if audio else (
                    "Dùng lại script đã có; khi bị 429 sẽ thử phụ đề gốc qua client iOS, "
                    "rồi proxy và website dự phòng. "
                    "Chỉ gửi mã video công khai, không gửi cookie."
                )
            )
        )
        extractor_class = AudioTranscriptBatchExtractor if audio else TranscriptBatchExtractor
        extractor = extractor_class(
            video_url=url,
            output_dir=output,
            cookie_browser=self.COOKIE_BROWSER_OPTIONS[self.cookie_browser_var.get()],
            progress_callback=self.queue_progress,
            cancel_event=self.cancel_event,
        )
        threading.Thread(target=self.run_transcripts, args=(extractor,), daemon=True).start()

    def start_from_keyboard(self, _event=None) -> None:
        if self.start_button.cget("state") != "disabled":
            self.start_extraction()

    def set_running(self, running: bool) -> None:
        self.worker_running = running
        lock_settings = running and self.active_job_kind == "script"
        setting_state = "disabled" if lock_settings else "normal"
        self.script_button.configure(state="disabled" if running else "normal")
        self.audio_script_button.configure(state="disabled" if running else "normal")
        self.url_entry.configure(state=setting_state)
        self.output_button.configure(state=setting_state)
        self.preset.configure(state=setting_state)
        self.quality.configure(state=setting_state)
        self.transcript_check.configure(state=setting_state)
        self.cookie_browser_menu.configure(state=setting_state)
        self.custom_interval_slider.configure(state=setting_state)
        self.custom_threshold_slider.configure(state=setting_state)
        self.custom_reset_button.configure(state=setting_state)
        self.cancel_button.configure(state="normal" if running else "disabled")
        self.cancel_button.configure(text="Dừng hàng chờ" if running and self.active_job_kind == "slide" else "Dừng")
        self.update_slide_start_button()

    def update_slide_start_button(self) -> None:
        if not hasattr(self, "start_button"):
            return
        if self.stopping_slide_queue:
            self.start_button.configure(text="Đang dừng…", state="disabled")
        elif self.playlist_loading:
            self.start_button.configure(text="Đọc playlist…", state="disabled")
        elif self.worker_running and self.active_job_kind == "script":
            self.start_button.configure(text="Tạo slide", state="disabled")
        elif self.slide_jobs.active is not None or len(self.slide_jobs):
            self.start_button.configure(text="Thêm hàng chờ", state="normal")
        else:
            self.start_button.configure(text="Tạo slide", state="normal")

    def refresh_slide_queue_card(self) -> None:
        if not hasattr(self, "queue_card"):
            return
        active = self.slide_jobs.active
        pending = self.slide_jobs.pending
        if active is None and not pending:
            self.queue_card.grid_remove()
            return

        for child in self.queue_card.winfo_children():
            child.destroy()
        self.queue_card.grid()
        attempted = self.slide_batch_completed + len(self.slide_batch_failures)
        self.section_title(
            self.queue_card,
            "≡",
            "Hàng chờ tạo slide",
            f"{1 if active else 0} đang chạy • {len(pending)} đang chờ • xử lý lần lượt",
        )

        items: list[tuple[str, str, SlideQueueJob]] = []
        position = attempted + 1
        if active is not None:
            items.append((str(position), "Đang chạy", active))
            position += 1
        for job in pending[:8]:
            items.append((str(position), "Đang chờ", job))
            position += 1

        for row_number, (number, status, job) in enumerate(items, start=1):
            row = ctk.CTkFrame(self.queue_card, fg_color="#211D1A", corner_radius=8)
            row.grid(row=row_number, column=0, sticky="ew", padx=28, pady=(0, 7))
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                row,
                text=number,
                width=30,
                height=30,
                corner_radius=15,
                fg_color=self.ACCENT_SOFT,
                text_color=self.ACCENT_TEXT,
                font=ctk.CTkFont(size=11, weight="bold"),
            ).grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
            ctk.CTkLabel(
                row,
                text=job.display_title,
                text_color=self.NAVY,
                font=ctk.CTkFont(size=13, weight="bold"),
                anchor="w",
            ).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(9, 0))
            ctk.CTkLabel(
                row,
                text=status,
                text_color=self.ACCENT_TEXT if status == "Đang chạy" else self.MUTED,
                font=ctk.CTkFont(size=11),
                anchor="w",
            ).grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(0, 9))

        next_row = len(items) + 1
        if len(pending) > 8:
            ctk.CTkLabel(
                self.queue_card,
                text=f"… và {len(pending) - 8} video khác đang chờ",
                text_color=self.MUTED,
                anchor="w",
            ).grid(row=next_row, column=0, sticky="ew", padx=30, pady=(0, 8))
            next_row += 1
        if pending:
            ctk.CTkButton(
                self.queue_card,
                text="Xóa các mục đang chờ",
                height=34,
                corner_radius=8,
                fg_color="transparent",
                hover_color=self.SURFACE_HOVER,
                border_width=1,
                border_color=self.BORDER,
                text_color=self.MUTED,
                command=self.clear_waiting_slide_jobs,
            ).grid(row=next_row, column=0, sticky="e", padx=28, pady=(2, 18))
        else:
            ctk.CTkFrame(self.queue_card, height=10, fg_color="transparent").grid(
                row=next_row, column=0, pady=(0, 8)
            )
        self.after_idle(self.install_scroll_bindings)

    def clear_waiting_slide_jobs(self) -> None:
        removed = self.slide_jobs.clear_pending()
        self.slide_batch_total = max(0, self.slide_batch_total - removed)
        self.refresh_slide_queue_card()
        self.update_slide_start_button()
        if removed:
            self.status_detail.configure(text=f"Đã xóa {removed} video đang chờ; tác vụ hiện tại vẫn tiếp tục.")

    def queue_progress(self, message: str, value: float) -> None:
        self.after(0, self.update_progress, message, value)

    def update_progress(self, message: str, value: float) -> None:
        self.status_label.configure(text=message)
        self.progress_bar.set(value)
        if "trực tuyến" in message:
            self.status_detail.configure(text="Đang lấy các khung hình cần thiết trực tiếp từ luồng video.")
        elif "script" in message.lower():
            self.status_detail.configure(text="Lưu phụ đề có sẵn; video không có phụ đề sẽ được ghi trong báo cáo.")
        elif "giới hạn tuổi" in message:
            self.status_detail.configure(text="SlideFlow chỉ dùng phiên đăng nhập trong bộ nhớ và không xuất cookie ra tệp.")
        if self.active_job_kind == "slide" and self.slide_jobs.active is not None:
            current = self.slide_jobs.active
            self.status_detail.configure(
                text=f"{current.display_title} • còn {len(self.slide_jobs)} video trong hàng chờ."
            )

    def run_extraction(self, extractor: OnlineSlideExtractor) -> None:
        try:
            result = extractor.extract()
        except InterruptedError:
            self.after(0, self.finish_cancelled)
        except Exception as error:
            self.after(0, self.finish_error, str(error))
        else:
            self.after(0, self.finish_success, result)

    def run_transcripts(self, extractor: TranscriptBatchExtractor) -> None:
        try:
            result = extractor.extract()
        except InterruptedError:
            self.after(0, self.finish_cancelled)
        except Exception as error:
            self.after(0, self.finish_error, str(error))
        else:
            self.after(0, self.finish_transcripts, result)

    def cancel_extraction(self) -> None:
        if self.cancel_button.cget("state") == "disabled":
            return
        if self.active_job_kind == "slide":
            removed = self.slide_jobs.clear_pending()
            self.slide_batch_total = max(0, self.slide_batch_total - removed)
            self.stopping_slide_queue = True
            self.refresh_slide_queue_card()
        self.cancel_event.set()
        self.status_label.configure(text="Đang dừng hàng chờ…" if self.active_job_kind == "slide" else "Đang dừng…")
        self.cancel_button.configure(state="disabled")
        self.update_slide_start_button()

    def finish_cancelled(self) -> None:
        if self.active_job_kind == "slide" and self.slide_jobs.active is not None:
            self.slide_jobs.finish_active()
            self.slide_jobs.clear_pending()
            self.set_running(False)
            self.stopping_slide_queue = False
            self.refresh_slide_queue_card()
            self.update_slide_start_button()
            self.progress_bar.set(0)
            self.status_label.configure(text="Đã dừng hàng chờ")
            self.status_detail.configure(text="Các mục đang chờ đã được xóa; kết quả hoàn tất trước đó vẫn được giữ.")
            return
        self.set_running(False)
        self.progress_bar.set(0)
        self.status_label.configure(text="Đã dừng")
        self.status_detail.configure(
            text=(
                "Script đã lưu và báo cáo tạm vẫn nằm trong thư mục kết quả."
                if self.active_job_kind == "script"
                else "Các kết quả chưa hoàn tất không được đưa vào PDF."
            )
        )
        if len(self.slide_jobs):
            self.after(0, self.start_next_slide_job)

    def finish_error(self, error: str) -> None:
        if self.active_job_kind == "slide" and self.slide_jobs.active is not None:
            failed_job = self.slide_jobs.finish_active()
            assert failed_job is not None
            self.slide_batch_failures.append((failed_job.display_title, error))
            self.set_running(False)
            self.refresh_slide_queue_card()
            if len(self.slide_jobs):
                self.status_label.configure(text="Một video gặp lỗi • đang tiếp tục hàng chờ")
                self.status_detail.configure(text=f"{failed_job.display_title}: {error[:140]}")
                self.after(0, self.start_next_slide_job)
            else:
                self.finish_slide_batch()
            return
        self.set_running(False)
        self.progress_bar.set(0)
        self.status_label.configure(text="Chưa thể hoàn tất")
        self.status_detail.configure(text=error[:180])
        messagebox.showerror("SlideFlow", error)
        if len(self.slide_jobs):
            self.after(0, self.start_next_slide_job)

    def finish_success(self, result: ExtractionResult) -> None:
        self.result = result
        self.slide_history.add(result.title, result.pdf_path, result.output_dir)
        if self.slide_jobs.active is not None:
            self.slide_jobs.finish_active()
            self.slide_batch_completed += 1
        self.set_running(False)
        self.progress_bar.set(1)
        self.show_result(result)
        self.refresh_history_card()
        self.refresh_slide_queue_card()
        if len(self.slide_jobs):
            self.status_label.configure(text="Đã xong một video • chuẩn bị video tiếp theo")
            self.status_detail.configure(
                text=f"{self.slide_batch_completed}/{self.slide_batch_total} video hoàn tất; "
                     f"còn {len(self.slide_jobs)} video đang chờ."
            )
            self.after(0, self.start_next_slide_job)
        else:
            self.finish_slide_batch()

    def finish_slide_batch(self) -> None:
        if self.slide_jobs.active is not None or len(self.slide_jobs):
            return
        self.stopping_slide_queue = False
        self.set_running(False)
        self.refresh_slide_queue_card()
        self.progress_bar.set(1 if self.slide_batch_completed else 0)
        failed_count = len(self.slide_batch_failures)
        if self.slide_batch_total <= 1 and self.slide_batch_completed == 1 and not failed_count:
            self.status_label.configure(text="Bộ slide đã sẵn sàng")
            self.status_detail.configure(text="PDF đã hoàn tất; ảnh chụp tạm đã được xóa để tiết kiệm dung lượng.")
            return
        self.status_label.configure(text="Đã hoàn tất hàng chờ")
        self.status_detail.configure(
            text=f"{self.slide_batch_completed}/{self.slide_batch_total} video đã tạo slide; "
                 f"{failed_count} video gặp lỗi."
        )
        if failed_count:
            details = "\n".join(
                f"• {title}: {error[:120]}"
                for title, error in self.slide_batch_failures[:4]
            )
            if failed_count > 4:
                details += f"\n• … và {failed_count - 4} lỗi khác"
            messagebox.showwarning(
                "Hàng chờ đã hoàn tất",
                f"{self.slide_batch_completed} video thành công, {failed_count} video gặp lỗi.\n\n{details}",
            )

    def finish_transcripts(self, result: TranscriptBatchResult) -> None:
        self.script_result = result
        self.set_running(False)
        self.progress_bar.set(
            (result.total_count - result.pending_count) / result.total_count
            if result.paused_reason else 1
        )
        if result.paused_reason:
            self.status_label.configure(text="Tạm dừng vì YouTube giới hạn")
            self.status_detail.configure(
                text=f"Có {result.saved_count} script; {result.pending_count} video đang chờ. "
                     "Hãy bấm Lấy script lại sau để tiếp tục."
            )
        else:
            self.status_label.configure(text="Đã lấy xong script")
            self.status_detail.configure(
                text=f"Có {result.saved_count}/{result.total_count} script "
                     f"({result.reused_count} đã có, {result.new_count} mới); "
                     f"{result.age_count} cần xác thực, {result.failed_count} lỗi khác."
            )
        self.show_transcript_result(result)
        if len(self.slide_jobs):
            self.after(0, self.start_next_slide_job)

    def show_transcript_result(self, result: TranscriptBatchResult) -> None:
        for child in self.result_card.winfo_children():
            child.destroy()
        self.preview_images.clear()
        self.result_card.grid()
        self.after_idle(self.install_scroll_bindings)
        self.section_title(self.result_card, "✓", "Script", result.title)

        metrics = ctk.CTkFrame(
            self.result_card, fg_color="#211D1A", corner_radius=10,
            border_width=1, border_color="#433731",
        )
        metrics.grid(row=1, column=0, sticky="ew", padx=28, pady=(12, 14))
        metrics.grid_columnconfigure((0, 1, 2), weight=1)
        for column, (value, label) in enumerate((
            (result.saved_count, "script hiện có"),
            (result.age_count, "cần xác thực"),
            (result.failed_count, "lỗi khác"),
        )):
            ctk.CTkLabel(
                metrics, text=str(value), text_color=self.NAVY,
                font=ctk.CTkFont(size=18, weight="bold"),
            ).grid(row=0, column=column, pady=(14, 0))
            ctk.CTkLabel(
                metrics, text=label, text_color=self.MUTED,
                font=ctk.CTkFont(size=12),
            ).grid(row=1, column=column, pady=(0, 14))

        ctk.CTkLabel(
            self.result_card,
            text=(
                f"{result.reused_count} script được dùng lại; {result.pending_count} video chưa xử lý. "
                f"Phương pháp: {result.method_label}. "
                "Báo cáo ghi rõ lý do từng mục."
            ),
            text_color=self.MUTED, anchor="w",
        ).grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 12))
        actions = ctk.CTkFrame(self.result_card, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 24))
        actions.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            actions,
            text="Mở báo cáo",
            height=44,
            corner_radius=10,
            fg_color=self.ORANGE,
            hover_color=self.ORANGE_HOVER,
            text_color="#FFFFFF",
            command=lambda: self.open_path(result.report_path),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            actions,
            text="Mở thư mục script",
            height=44,
            corner_radius=10,
            fg_color=self.SURFACE,
            hover_color=self.SURFACE_HOVER,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.NAVY,
            command=lambda: self.open_path(result.output_dir),
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

    def show_result(self, result: ExtractionResult) -> None:
        for child in self.result_card.winfo_children():
            child.destroy()
        self.preview_images.clear()
        self.result_card.grid()
        self.after_idle(self.install_scroll_bindings)
        self.section_title(self.result_card, "✓", "Hoàn tất", result.title)

        duration = f"{int(result.duration // 60)} phút {int(result.duration % 60)} giây"
        metrics = ctk.CTkFrame(
            self.result_card,
            fg_color="#211D1A",
            corner_radius=10,
            border_width=1,
            border_color="#433731",
        )
        metrics.grid(row=1, column=0, sticky="ew", padx=28, pady=(12, 14))
        metrics.grid_columnconfigure((0, 1, 2), weight=1)
        values = [
            (str(result.slide_count), "slide đã chọn"),
            (duration, "thời lượng"),
            (str(result.sampled_count), "mốc đã xem"),
        ]
        for column, (value, label) in enumerate(values):
            ctk.CTkLabel(metrics, text=value, text_color=self.NAVY, font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=column, pady=(14, 0))
            ctk.CTkLabel(metrics, text=label, text_color=self.MUTED, font=ctk.CTkFont(size=12)).grid(row=1, column=column, pady=(0, 14))

        preview = ctk.CTkFrame(self.result_card, fg_color="transparent")
        preview.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 14))
        for column in range(3):
            preview.grid_columnconfigure(column, weight=1)
        slide_paths = sorted((Path(result.output_dir) / "slides").glob("*.jpg"))[:3]
        if slide_paths:
            for column, path in enumerate(slide_paths):
                with Image.open(path) as image:
                    thumbnail = image.copy()
                thumbnail.thumbnail((250, 140))
                display = ctk.CTkImage(light_image=thumbnail, dark_image=thumbnail, size=thumbnail.size)
                self.preview_images.append(display)
                ctk.CTkLabel(preview, text="", image=display, corner_radius=10).grid(row=0, column=column, padx=6)
        else:
            ctk.CTkLabel(
                preview,
                text="✓  Đã ghép PDF và xóa toàn bộ ảnh chụp tạm",
                height=48,
                corner_radius=9,
                fg_color=self.ACCENT_SOFT,
                text_color=self.ACCENT_TEXT,
                font=ctk.CTkFont(size=12, weight="bold"),
            ).grid(row=0, column=0, columnspan=3, sticky="ew")

        actions = ctk.CTkFrame(self.result_card, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 24))
        actions.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            actions,
            text="Mở PDF",
            height=44,
            corner_radius=10,
            fg_color=self.ORANGE,
            hover_color=self.ORANGE_HOVER,
            text_color="#FFFFFF",
            command=lambda: self.open_path(result.pdf_path),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            actions,
            text="Mở thư mục kết quả",
            height=44,
            corner_radius=10,
            fg_color=self.SURFACE,
            hover_color=self.SURFACE_HOVER,
            border_width=1,
            border_color=self.BORDER,
            text_color=self.NAVY,
            command=lambda: self.open_path(result.output_dir),
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

    @staticmethod
    def open_path(path: str) -> None:
        subprocess.run(["open", path], check=False)


def main() -> None:
    app = SlideFlowApp()
    app.mainloop()


if __name__ == "__main__":
    main()
