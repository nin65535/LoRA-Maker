from pathlib import Path


def select_project_config(initial_path: Path | None = None) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("Windowsのファイル選択ダイアログを利用できません") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askopenfilename(
            parent=root,
            title="LoRA Maker プロジェクト設定を開く",
            initialdir=str(initial_path) if initial_path and initial_path.is_dir() else None,
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
    finally:
        root.destroy()
    return Path(selected) if selected else None
