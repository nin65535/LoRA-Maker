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


def select_project_save_path(initial_path: Path | None = None) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("Windowsのファイル保存ダイアログを利用できません") from exc

    initial_directory = initial_path if initial_path and initial_path.is_dir() else initial_path.parent if initial_path else None
    initial_file = initial_path.name if initial_path and not initial_path.is_dir() else "lora_maker.json"

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.asksaveasfilename(
            parent=root,
            title="LoRA Maker プロジェクト設定の保存先を選択",
            initialdir=str(initial_directory) if initial_directory and initial_directory.is_dir() else None,
            initialfile=initial_file,
            defaultextension=".json",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
    finally:
        root.destroy()
    return Path(selected) if selected else None
