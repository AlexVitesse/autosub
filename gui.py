#!/usr/bin/env python3
"""Subtitle Creator - Interfaz Grafica"""

import os
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

import config
from subtitle_creator import process_video


class SubtitleCreatorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Subtitle Creator")
        self.root.geometry("700x680")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e1e2e")

        self.processing = False
        self.generated_files = []

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#1e1e2e")
        style.configure("TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 10))
        style.configure("Header.TLabel", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 14, "bold"))
        style.configure("Warning.TLabel", background="#1e1e2e", foreground="#f9e2af", font=("Segoe UI", 8))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"))
        style.configure("TCheckbutton", background="#1e1e2e", foreground="#cdd6f4", font=("Segoe UI", 10))
        style.configure("TLabelframe", background="#1e1e2e", foreground="#89b4fa")
        style.configure("TLabelframe.Label", background="#1e1e2e", foreground="#89b4fa", font=("Segoe UI", 10, "bold"))
        style.configure("TCombobox", font=("Segoe UI", 10))

        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=20)
        main.pack(fill="both", expand=True)

        # Header
        ttk.Label(main, text="Subtitle Creator", style="Header.TLabel").pack(anchor="w")
        ttk.Label(main, text="Genera subtitulos SRT desde video con Whisper + LLM",
                  style="TLabel").pack(anchor="w", pady=(0, 10))

        # --- Archivo ---
        file_frame = ttk.Frame(main)
        file_frame.pack(fill="x", pady=5)
        ttk.Label(file_frame, text="Archivo de video:").pack(anchor="w")

        file_row = ttk.Frame(file_frame)
        file_row.pack(fill="x", pady=2)
        self.file_var = tk.StringVar()
        self.file_entry = ttk.Entry(file_row, textvariable=self.file_var, font=("Segoe UI", 10))
        self.file_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(file_row, text="Examinar", command=self._browse_file).pack(side="right")

        # --- Idioma del video ---
        lang_frame = ttk.Frame(main)
        lang_frame.pack(fill="x", pady=5)
        ttk.Label(lang_frame, text="Idioma del video:").pack(side="left")
        self.source_lang_var = tk.StringVar(value="en")
        lang_values = [f"{code} - {name}" for code, name in config.LANGUAGES.items()]
        self.source_combo = ttk.Combobox(lang_frame, textvariable=self.source_lang_var,
                                         values=lang_values, state="readonly", width=20)
        self.source_combo.set("en - English")
        self.source_combo.pack(side="left", padx=10)

        # --- Traducir a ---
        trans_frame = ttk.LabelFrame(main, text="Traducir a:", padding=10)
        trans_frame.pack(fill="x", pady=5)

        self.lang_vars = {}
        row_frame = None
        for i, (code, name) in enumerate(config.LANGUAGES.items()):
            if i % 4 == 0:
                row_frame = ttk.Frame(trans_frame)
                row_frame.pack(fill="x", pady=1)
            var = tk.BooleanVar(value=(code == "es"))
            cb = ttk.Checkbutton(row_frame, text=name, variable=var)
            cb.pack(side="left", padx=(0, 15))
            self.lang_vars[code] = var

        # --- API Key ---
        key_frame = ttk.Frame(main)
        key_frame.pack(fill="x", pady=5)
        ttk.Label(key_frame, text="Groq API Keys (separadas por coma):").pack(anchor="w")
        self.key_var = tk.StringVar(value=",".join(config.GROQ_API_KEYS))
        self.key_entry = ttk.Entry(key_frame, textvariable=self.key_var, font=("Segoe UI", 9), show="*")
        self.key_entry.pack(fill="x", pady=2)

        # --- Advertencia ---
        ttk.Label(main,
                  text="Los subtitulos generados pueden no ser 100% precisos. Se recomienda revisarlos.",
                  style="Warning.TLabel").pack(anchor="w", pady=(5, 5))

        # --- Boton generar ---
        self.gen_btn = ttk.Button(main, text="Generar Subtitulos", style="Accent.TButton",
                                  command=self._start_processing)
        self.gen_btn.pack(fill="x", pady=5, ipady=5)

        # --- Progreso ---
        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.pack(fill="x", pady=(5, 0))

        # --- Log ---
        log_frame = ttk.Frame(main)
        log_frame.pack(fill="both", expand=True, pady=5)

        self.log_text = tk.Text(log_frame, height=12, bg="#313244", fg="#cdd6f4",
                                font=("Consolas", 9), relief="flat", wrap="word",
                                insertbackground="#cdd6f4", state="disabled")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- Abrir carpeta ---
        self.open_btn = ttk.Button(main, text="Abrir carpeta de salida", command=self._open_folder,
                                   state="disabled")
        self.open_btn.pack(fill="x", pady=(0, 5))

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Seleccionar video",
            filetypes=[
                ("Videos", "*.mp4 *.mkv *.avi *.mov *.webm"),
                ("Todos", "*.*"),
            ]
        )
        if path:
            self.file_var.set(path)

    def _get_source_lang(self) -> str:
        val = self.source_combo.get()
        return val.split(" - ")[0] if " - " in val else "en"

    def _get_target_langs(self) -> list[str]:
        source = self._get_source_lang()
        return [code for code, var in self.lang_vars.items() if var.get() and code != source]

    def _get_api_keys(self) -> list[str]:
        raw = self.key_var.get().strip()
        return [k.strip() for k in raw.split(",") if k.strip()]

    def _log(self, msg: str):
        def _append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(0, _append)

    def _start_processing(self):
        video_path = self.file_var.get().strip()
        if not video_path or not os.path.exists(video_path):
            messagebox.showerror("Error", "Selecciona un archivo de video valido.")
            return

        api_keys = self._get_api_keys()
        if not api_keys:
            messagebox.showerror("Error", "Ingresa al menos una API key de Groq.")
            return

        target_langs = self._get_target_langs()
        source_lang = self._get_source_lang()

        # Limpiar log
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.gen_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.processing = True
        self.progress.start(15)
        self.generated_files = []

        def run():
            try:
                files = process_video(
                    video_path=video_path,
                    source_lang=source_lang,
                    target_langs=target_langs,
                    api_keys=api_keys,
                    log=self._log,
                )
                self.generated_files = files
                self._log("\nArchivos generados:")
                for f in files:
                    self._log(f"  {Path(f).name}")
            except Exception as e:
                self._log(f"\nERROR: {e}")
            finally:
                self.root.after(0, self._done)

        threading.Thread(target=run, daemon=True).start()

    def _done(self):
        self.processing = False
        self.progress.stop()
        self.gen_btn.configure(state="normal")
        if self.generated_files:
            self.open_btn.configure(state="normal")

    def _open_folder(self):
        if self.generated_files:
            folder = str(Path(self.generated_files[0]).parent)
            subprocess.Popen(["explorer", folder])


def main():
    root = tk.Tk()
    SubtitleCreatorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
