# -*- coding: utf-8 -*-
"""GR1 关机 LOGO 工具 —— 打包成 exe 的入口。

双击即打开图形界面（不带参数时默认 --gui）；
也接受与 gr1_logo_text.py 完全相同的命令行参数。
"""
import sys


def _attach_parent_console() -> None:
    """打包成 --windowed exe 时没有控制台；从 cmd/PowerShell 带参数运行时，
    接回父进程的控制台，让 --help / 进度输出可见（双击运行不受影响）。"""
    if sys.stdout is not None:
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        pass


def _run() -> int:
    argv = sys.argv[1:]
    if not argv:
        argv = ["--gui"]
    else:
        _attach_parent_console()
    from gr1_logo_text import main
    return main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(_run())
    except SystemExit:
        raise
    except Exception as exc:  # 无控制台时也要让用户看到错误
        import traceback
        detail = traceback.format_exc()
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("GR1 关机 LOGO 工具 - 出错", "%s\n\n%s" % (exc, detail))
            root.destroy()
        except Exception:
            pass
        raise SystemExit(1)
