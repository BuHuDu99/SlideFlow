"""A bounded update progress view: one queued event and one UI poll timer."""
from __future__ import annotations

import queue
import sys
import threading
import time

import customtkinter as ctk


class UpdateProgressDialog(ctk.CTkToplevel):
    def __init__(self, parent, version: str):
        super().__init__(parent)
        self.cancel_event = threading.Event()
        self.events = queue.Queue(maxsize=1)
        self._poll_id = None
        self._phase = "download"
        self._last_event = time.monotonic()
        self._waiting = False
        self.title("Cập nhật SlideFlow")
        self.resizable(False, False)
        self.configure(fg_color=parent.CARD)
        self.transient(parent)
        if sys.platform == "darwin":
            try:
                self.tk.call("::tk::unsupported::MacWindowStyle", "appearance", self._w, "dark")
            except Exception:
                pass
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text=f"Cập nhật SlideFlow {version}", text_color=parent.TEXT,
            font=ctk.CTkFont(size=19, weight="bold"), anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 14))
        self.phase_label = ctk.CTkLabel(
            self, text="Đang kết nối để tải bản mới…", text_color=parent.TEXT,
            font=ctk.CTkFont(size=13), anchor="w",
        )
        self.phase_label.grid(row=1, column=0, sticky="ew", padx=24)
        self.bar = ctk.CTkProgressBar(
            self, height=8, progress_color=parent.ORANGE, fg_color=parent.TRACK,
            mode="indeterminate",
        )
        self.bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(14, 8))
        self.bar.start()
        self.amount_label = ctk.CTkLabel(
            self, text="Đang chờ dữ liệu…", text_color=parent.MUTED,
            font=ctk.CTkFont(size=12), anchor="w",
        )
        self.amount_label.grid(row=3, column=0, sticky="ew", padx=24)
        self.cancel_button = ctk.CTkButton(
            self, text="Hủy cập nhật", width=120, height=34,
            fg_color=parent.SURFACE, hover_color=parent.SURFACE_HOVER,
            text_color=parent.TEXT, command=self.request_cancel,
        )
        self.cancel_button.grid(row=4, column=0, sticky="e", padx=24, pady=(18, 20))
        self.protocol("WM_DELETE_WINDOW", self.request_cancel)
        parent.update_idletasks()
        self.geometry(f"460x260+{max(0, parent.winfo_rootx() + (parent.winfo_width()-460)//2)}+{max(0, parent.winfo_rooty() + (parent.winfo_height()-260)//2)}")
        self.grab_set()
        self.lift()
        self._poll_id = self.after(150, self.poll_progress)

    def post_progress(self, phase: str, downloaded: int, total: int) -> None:
        """Worker-safe: keep only the latest event, never call Tk here."""
        event = (phase, downloaded, total)
        try:
            self.events.put_nowait(event)
        except queue.Full:
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            try:
                self.events.put_nowait(event)
            except queue.Full:
                pass

    def poll_progress(self) -> None:
        self._poll_id = None
        try:
            phase, downloaded, total = self.events.get_nowait()
        except queue.Empty:
            if not self.cancel_event.is_set() and self._phase == "download" and not self._waiting and time.monotonic()-self._last_event > 8:
                self.phase_label.configure(text="Đang chờ dữ liệu từ mạng…")
                self._waiting = True
        else:
            self._last_event = time.monotonic()
            self._waiting = False
            if not self.cancel_event.is_set():
                if phase == "download":
                    if downloaded:
                        self.bar.stop()
                        self.bar.configure(mode="determinate")
                    fraction = min(1.0, downloaded / total) if total else 0
                    if downloaded:
                        self.bar.set(fraction)
                    self.phase_label.configure(text=f"Đang tải bản mới · {fraction:.0%}")
                    self.amount_label.configure(text=f"{downloaded/1048576:.1f} MB / {total/1048576:.1f} MB")
                else:
                    if self._phase == "download":
                        self.bar.stop()
                        self.bar.configure(mode="indeterminate")
                        self.bar.start()
                    labels = {
                        "verify": "Đang xác minh bản cập nhật…",
                        "prepare": "Đang chuẩn bị cài đặt…",
                        "copy": "Đang chuẩn bị ứng dụng mới…",
                        "ready": "Đã sẵn sàng khởi động lại…",
                    }
                    self.phase_label.configure(text=labels.get(phase, "Đang cập nhật…"))
                    self.amount_label.configure(text="Tải xong · đang hoàn tất chuẩn bị")
                self._phase = phase
        self._poll_id = self.after(150, self.poll_progress)

    def request_cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(text="Đang hủy…", state="disabled")
        self.phase_label.configure(text="Đang hủy cập nhật…")
        self.amount_label.configure(text="Đợi kết nối phản hồi và dọn tệp tạm.")

    def show_installing(self) -> None:
        self.cancel_button.configure(state="disabled")
        self.bar.stop()
        self.bar.configure(mode="determinate")
        self.bar.set(1)
        self.phase_label.configure(text="Đang cài đặt và khởi động lại…")
        self.amount_label.configure(text="Đã tải và xác minh xong bản cập nhật")
        self.update_idletasks()

    def destroy(self) -> None:
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        self.bar.stop()
        try:
            self.grab_release()
        except Exception:
            pass
        super().destroy()
