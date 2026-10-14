# -*- coding: utf-8 -*-
"""GR1 关机 LOGO 文字自定义工具。

把任意文字渲染成相机 640x480 / 16 级的 .brp 关机画面覆盖图，并生成
"安装 / 还原" SD 卡包（安装包会在下一次正常关机时，向相机内部 NAND 的
B:/dvf_TDSt.brp 写入一个文件；还原包把它删掉）。

默认参数（默认文字 + msyhbd.ttc + 自动字号）渲染出的载荷与已实机验证过的
成品逐字节一致：
    sha256 577bd3c2e05da73f967222138d7547009857a0ad98417e1dff890c0084f2c894

用法示例见文件末尾 EXAMPLES / `python gr1_logo_text.py --help`。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

# 打包成 --windowed exe 时没有控制台，sys.stdout/stderr 会是 None；
# 指向 devnull 以免 print() 抛 AttributeError。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent


def _resource(name: str) -> Path:
    """定位随程序分发的资源（打包成 exe 后从解包目录取）。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name
    return ROOT / name


def default_package_dir() -> Path:
    """默认安装包输出目录（打包成 exe 时用可写的临时目录）。"""
    if getattr(sys, "frozen", False):
        return Path(tempfile.gettempdir()) / "gr1-logo-package"
    return ROOT / "gr1-logo-package"

# --- 画面格式（来自对官方 A:/img/dvf_TDSt.brp 的实测）--------------------------
WIDTH, HEIGHT = 640, 480
LEVELS = 16
PAYLOAD_SIZE = WIDTH * HEIGHT * 2          # 614400 字节，小端 u16
CANVAS_MARGIN = 18                          # 左右各 18 -> 可用宽 604
DEFAULT_MAX_WIDTH = WIDTH - 2 * CANVAS_MARGIN   # 604
DEFAULT_MAX_HEIGHT = 150
MAX_FONT_SIZE = 160
MIN_FONT_SIZE = 16

DEFAULT_TEXT = "Ciallo\uff5e(\u2220\u30fb\u03c9< )\u2312\u2605"
DEFAULT_FONT = r"C:\Windows\Fonts\msyhbd.ttc"

MARKER = b"GR1 shutdown-logo package. A matching output proves only this copy completed.\r\n"
INSTALLER_NAME = "\u5b89\u88c5LOGO.ps1"     # 安装LOGO.ps1
PACKAGE_NAME = "gr1-shutdown-logo"
TARGET_FILE = "B:/dvf_TDSt.brp"
DEFAULT_PACKAGE_DIR = default_package_dir()


# --- 像素值编码 ---------------------------------------------------------------
def level_to_value(level: int) -> int:
    """level(0..15) -> 该级在 .brp 中的 16 位小端值。低半字节 = 显示亮度。"""
    l = level & 0xF
    return (l << 12) | 0x0F00 | (l << 4) | l


def value_to_level(value: int) -> int:
    return value & 0xF


LEGAL_VALUES = tuple(level_to_value(l) for l in range(LEVELS))


# --- 渲染 ---------------------------------------------------------------------
def _load_font(font_path: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(font_path, size)


def _measure_lines(font, lines):
    """返回 (bboxes, widths, heights)，均为逐行。"""
    from PIL import Image, ImageDraw
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    boxes = [probe.textbbox((0, 0), ln, font=font) for ln in lines]
    widths = [b[2] - b[0] for b in boxes]
    heights = [b[3] - b[1] for b in boxes]
    return boxes, widths, heights


def _block_size(widths, heights, gap):
    return max(widths), sum(heights) + gap * (len(heights) - 1)


def fit_size(font_path: str, lines, max_width: int = DEFAULT_MAX_WIDTH,
             max_height: int = DEFAULT_MAX_HEIGHT, gap: int = 0,
             lo: int = MIN_FONT_SIZE, hi: int = MAX_FONT_SIZE) -> int:
    """从大到小找第一个能塞进 max_width x max_height 的偶数号。"""
    for size in range(hi, lo - 1, -2):
        font = _load_font(font_path, size)
        _, widths, heights = _measure_lines(font, lines)
        bw, bh = _block_size(widths, heights, gap)
        if bw <= max_width and bh <= max_height:
            return size
    raise ValueError("文字太大，放不进 %dx%d 的框（把字号调小或换更短的字）"
                     % (max_width, max_height))


def _to_levels(img) -> bytes:
    """L 图 -> 每像素一字节的 level(0..15)。与原成品完全相同的映射。"""
    px = img.load()
    out = bytearray(WIDTH * HEIGHT)
    i = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            out[i] = max(0, min(LEVELS - 1, round(px[x, y] / 255 * (LEVELS - 1))))
            i += 1
    return bytes(out)


def render_levels(text: str, font_path: str = DEFAULT_FONT, size: int | None = None,
                  max_width: int = DEFAULT_MAX_WIDTH, max_height: int = DEFAULT_MAX_HEIGHT,
                  margin: int = CANVAS_MARGIN, align: str = "center",
                  valign: str = "middle", line_gap: int = 0,
                  invert: bool = False):
    """渲染文字，返回 (levels_bytes, used_size)。levels_bytes 长度 = 640*480。"""
    from PIL import Image, ImageDraw

    lines = text.split("\n")
    if not lines or all(not ln.strip() for ln in lines):
        lines = [""]

    if size is None or size <= 0:
        size = fit_size(font_path, lines, max_width, max_height, line_gap)

    font = _load_font(font_path, size)
    boxes, widths, heights = _measure_lines(font, lines)
    bw, bh = _block_size(widths, heights, line_gap)

    content_w = WIDTH - 2 * margin
    content_h = HEIGHT - 2 * margin

    if align == "left":
        left = margin
    elif align == "right":
        left = margin + (content_w - bw)
    else:
        left = margin + (content_w - bw) // 2

    if valign == "top":
        top = margin
    elif valign == "bottom":
        top = margin + (content_h - bh)
    else:
        top = margin + (content_h - bh) // 2

    img = Image.new("L", (WIDTH, HEIGHT), 255 if invert else 0)
    draw = ImageDraw.Draw(img)
    fill = 0 if invert else 255

    y = top
    for i, ln in enumerate(lines):
        bb, w, h = boxes[i], widths[i], heights[i]
        if align == "left":
            x = left
        elif align == "right":
            x = left + (bw - w)
        else:
            x = left + (bw - w) // 2
        draw.text((x - bb[0], y - bb[1]), ln, fill=fill, font=font)
        y += h + line_gap

    return _to_levels(img), size


# --- 载荷编解码 ---------------------------------------------------------------
def levels_to_bytes(levels: bytes) -> bytes:
    out = bytearray(len(levels) * 2)
    for i, l in enumerate(levels):
        v = level_to_value(l)
        out[2 * i] = v & 0xFF
        out[2 * i + 1] = (v >> 8) & 0xFF
    return bytes(out)


def bytes_to_levels(raw: bytes) -> bytes:
    return bytes(value_to_level(raw[i] | (raw[i + 1] << 8))
                 for i in range(0, len(raw), 2))


def validate_payload(raw: bytes) -> bool:
    if len(raw) != PAYLOAD_SIZE:
        raise ValueError("载荷必须是 %d 字节，实际 %d" % (PAYLOAD_SIZE, len(raw)))
    legal = set(LEGAL_VALUES)
    for i in range(0, len(raw), 2):
        v = raw[i] | (raw[i + 1] << 8)
        if v not in legal:
            raise ValueError("第 %d 个像素的值 0x%04X 不是 16 个合法值之一" % (i // 2, v))
    return True


def render_payload(text: str, font_path: str = DEFAULT_FONT, size: int | None = None,
                   **kw):
    levels, used = render_levels(text, font_path, size, **kw)
    raw = levels_to_bytes(levels)
    validate_payload(raw)
    return raw, used


def to_preview(raw: bytes, path=None, scale: int = 1):
    """把载荷变成灰度预览图（黑=透明键）。"""
    from PIL import Image
    levels = bytes_to_levels(raw)
    img = Image.new("L", (WIDTH, HEIGHT))
    img.putdata([l * 255 // (LEVELS - 1) for l in levels])
    if scale != 1:
        img = img.resize((WIDTH * scale, HEIGHT * scale), Image.NEAREST)
    if path is not None:
        img.save(path)
    return img


# --- TTL 脚本（与已实机验证的成品逐字节一致）----------------------------------
INSTALL = '''; GR1 shutdown logo - INSTALL. Replaces one file on internal NAND (B:/).
; B:/dvf_TDSt.brp is looked up BEFORE the built-in A:/img/dvf_TDSt.brp copy,
; so this file becomes the shutdown logo.
; Only B:/dvf_TDSt.brp is removed before the copy, so repeated installs replace
; the old logo instead of depending on copyfiles' overwrite behavior.
; The nanddrivewp / copyfiles / nanddrivewp sequence is the same one Ricoh's own
; firmware uses to write C:/adj (offline notes section 22.3).
filesearch "H:/GR1PIN.TXT"
a = result
filesearch "H:/GR1NONE.TXT"
b = result
if a != b then
\tfilesearch "H:/GR1START.TXT"
\tif result != a then
\t\tfilecopy "H:/GR1PIN.TXT" "H:/GR1START.TXT"
\tendif
\tsprintf "nanddrivewp B off"
\tsendln inputstr
\tsprintf "remove B:/dvf_TDSt.brp"
\tsendln inputstr
\tsprintf "copyfiles H:/gr1logo B:/"
\tsendln inputstr
\tsprintf "nanddrivewp B on"
\tsendln inputstr
\tfilesearch "B:/dvf_TDSt.brp"
\tif result != b then
\t\tfilesearch "H:/GR1WROTE.TXT"
\t\tif result != a then
\t\t\tfilecopy "H:/GR1PIN.TXT" "H:/GR1WROTE.TXT"
\t\tendif
\tendif
endif
exit
'''

RESTORE = '''; GR1 shutdown logo - RESTORE. Removes B:/dvf_TDSt.brp from internal NAND.
; B: did not contain this file before the install, so removing it returns the
; camera to the stock RICOH shutdown logo exactly.
filesearch "H:/GR1PIN.TXT"
a = result
filesearch "H:/GR1NONE.TXT"
b = result
if a != b then
\tsprintf "nanddrivewp B off"
\tsendln inputstr
\tsprintf "remove B:/dvf_TDSt.brp"
\tsendln inputstr
\tsprintf "nanddrivewp B on"
\tsendln inputstr
\tfilesearch "H:/GR1REST.TXT"
\tif result != a then
\t\tfilecopy "H:/GR1PIN.TXT" "H:/GR1REST.TXT"
\tendif
endif
exit
'''

INSTALL_SHELL = ("nanddrivewp B off", "remove B:/dvf_TDSt.brp",
                 "copyfiles H:/gr1logo B:/", "nanddrivewp B on")
RESTORE_SHELL = ("nanddrivewp B off", "remove B:/dvf_TDSt.brp", "nanddrivewp B on")
SEARCHES = ("H:/GR1PIN.TXT", "H:/GR1NONE.TXT", "H:/GR1START.TXT",
            "H:/GR1WROTE.TXT", "H:/GR1REST.TXT", "B:/dvf_TDSt.brp")
PAIRS = (("H:/GR1PIN.TXT", "H:/GR1START.TXT"),
         ("H:/GR1PIN.TXT", "H:/GR1WROTE.TXT"),
         ("H:/GR1PIN.TXT", "H:/GR1REST.TXT"))
OPERANDS = ("a", "b", "result")


def validate(text: str, shell) -> bytes:
    """白名单校验 TTL，并返回 CRLF + ASCII 的最终字节。"""
    depth = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        t = shlex.split(s)
        c = t[0]
        if c == "if":
            if len(t) != 5 or t[2] != "!=" or t[-1] != "then" \
                    or t[1] not in OPERANDS or t[3] not in OPERANDS:
                raise ValueError("Unapproved condition: " + s)
            depth += 1
        elif c == "endif":
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced")
        elif c == "exit":
            if len(t) != 1:
                raise ValueError("Invalid exit")
        elif c in ("a", "b"):
            if len(t) != 3 or t[1] != "=" or t[2] != "result":
                raise ValueError("Only '<var> = result'")
        elif c == "filesearch":
            if len(t) != 2 or t[1] not in SEARCHES:
                raise ValueError("Unapproved search: " + s)
        elif c == "sprintf":
            if len(t) != 2 or t[1] not in shell:
                raise ValueError("Unapproved shell command: " + s)
        elif c == "sendln":
            if t != ["sendln", "inputstr"]:
                raise ValueError("Only 'sendln inputstr'")
        elif c == "filecopy":
            if len(t) != 3 or (t[1], t[2]) not in PAIRS:
                raise ValueError("Unapproved copy: " + s)
        else:
            raise ValueError("Unapproved command: " + c)
    if depth:
        raise ValueError("Unbalanced")
    payload = text.replace("\n", "\r\n").encode("ascii")
    if max(map(len, payload.split(b"\r\n"))) >= 256:
        raise ValueError("Line too long")
    return payload


# --- 安装包 -------------------------------------------------------------------
README_TEMPLATE = """# GR1 关机 LOGO 替换包（写入内部 NAND，风险自担）

本包由 `gr1_logo_text.py` 生成。

- 文字：{text}
- 字体：{font} @ {size}px
- 载荷：`dvf_TDSt.brp`（640x480，{payload_size} 字节）
- sha256：`{sha}`

**这个包会写相机的内部 NAND。** 只写一个文件：`B:/dvf_TDSt.brp`。除此之外不碰任何东西。

## 原理

关机时固件按这个顺序找图：

```
B:/dvf_TDSt.brp   ->   C:/dvf_TDSt.brp   ->   A:/img/dvf_TDSt.brp（固件内置）
```

本机实测 B:/ 和 C: 根目录都没有这个文件，所以默认显示的是内置那份 RICOH。
往 B:/ 放一份，就会盖掉它。删掉，就完全回到默认。
安装时会先删除旧的 `B:/dvf_TDSt.brp`，再复制新文件，重复安装也能稳定替换。

## 用法

```
# 安装/替换（第一次正常关机时写入，第二次正常关机显示新图）
& .\\安装LOGO.ps1 -Drive E: -Stage install-logo -ConfirmGRCard -IUnderstandInternalWrite

# 还原（下一次正常关机时删除）
& .\\安装LOGO.ps1 -Drive E: -Stage restore-logo -ConfirmGRCard -IUnderstandInternalWrite
```

写入/删除发生在**关机脚本执行时**，也就是你正常关机的那一次。
新关机图不一定在写入的同一刻就显示：有些机器要等本次关机完全结束，下一次开机后再正常关机一次才会读取到新文件。
所以推荐流程是：部署 -> 第一次正常关机（写入）-> 开机 -> 第二次正常关机（看到新图）。

安装器会先写入，再强制刷新文件/卷缓存，并按清单里的 SHA256 重新读取校验。
**在工具提示"写入完成"前不要拔卡**；提示完成后即可安全拔出。

**看到新图后，记得删掉卡上的 `Shutdown.ttl`**，否则每次正常关机都会重写一遍。

## 判读

| 卡上出现的文件 | 含义 |
| --- | --- |
| `GR1START.TXT` | 关机脚本跑到了写 NAND 那一步 |
| `GR1WROTE.TXT` | 写完后 `filesearch "B:/dvf_TDSt.brp"` 命中 -> 文件在 B: 上 |
| `GR1REST.TXT` | 还原脚本执行完成 |

## 风险（如实说明）

- 这是内部 NAND 写入。相机自己在正常使用中也会写 B:，但"关机脚本里写完紧接着断电"这个时序没有先例。
- 如果 B: 文件系统因此损坏、且相机无法开机，SD 卡救不回来 —— 没有独立于 APP 的救砖链。
- 安装只会删除并替换 `B:/dvf_TDSt.brp` 这一个目标文件；还原就是删掉它，不覆盖其他文件。
- 最坏情况下的第一手止损依然是：**拔卡**（必要时再取电池），回到"没有 SD 脚本"的状态。

## 卡上不能有别的入口

安装器会拒绝任何残留的 `Startup.ttl` / `Shutdown.ttl` / `update.ttl` / `gr1logo` /
探针输出文件。请先把卡清干净（保留 `DCIM`、`MISC`）。
"""


def package_readme(text, font_path, size, raw) -> str:
    return README_TEMPLATE.format(
        text=text.replace("\n", " / "),
        font=os.path.basename(font_path),
        size=size,
        payload_size=len(raw),
        sha=hashlib.sha256(raw).hexdigest(),
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_package(out_dir, raw: bytes, text: str, font_path: str = DEFAULT_FONT,
                  size: int | None = None, installer_src=None,
                  write_preview: bool = True) -> Path:
    """生成完整安装包（install-logo / restore-logo / manifest / 安装器 / 说明）。"""
    out_dir = Path(out_dir)
    install_dir = out_dir / "install-logo"
    restore_dir = out_dir / "restore-logo"
    for d in (install_dir, restore_dir):
        d.mkdir(parents=True, exist_ok=True)
        for stale in list(d.glob("*.ttl")) + list(d.glob("*.ttl.disabled")):
            stale.unlink()

    validate_payload(raw)
    if size is None:
        size = 0

    logo = install_dir / "gr1logo" / "dvf_TDSt.brp"
    logo.parent.mkdir(parents=True, exist_ok=True)
    logo.write_bytes(raw)
    (install_dir / "GR1PIN.TXT").write_bytes(MARKER)
    (restore_dir / "GR1PIN.TXT").write_bytes(MARKER)
    (install_dir / "Shutdown.ttl.disabled").write_bytes(validate(INSTALL, INSTALL_SHELL))
    (restore_dir / "Shutdown.ttl.disabled").write_bytes(validate(RESTORE, RESTORE_SHELL))

    manifest = {
        "package": PACKAGE_NAME,
        "writes_internal_nand": True,
        "internal_write_stages": ["install-logo", "restore-logo"],
        "target_file": TARGET_FILE,
        "logo_source_text": text,
        "logo_font": os.path.basename(font_path),
        "logo_font_size": size,
        "stages": {
            "install-logo": {
                "Shutdown.ttl.disabled": {
                    "type": "file",
                    "size": (install_dir / "Shutdown.ttl.disabled").stat().st_size,
                    "sha256": _sha(install_dir / "Shutdown.ttl.disabled"),
                },
                "GR1PIN.TXT": {"type": "file", "size": len(MARKER),
                               "sha256": _sha(install_dir / "GR1PIN.TXT")},
                "gr1logo": {"type": "directory",
                            "inner": {"name": "dvf_TDSt.brp",
                                      "size": logo.stat().st_size,
                                      "sha256": _sha(logo)}},
            },
            "restore-logo": {
                "Shutdown.ttl.disabled": {
                    "type": "file",
                    "size": (restore_dir / "Shutdown.ttl.disabled").stat().st_size,
                    "sha256": _sha(restore_dir / "Shutdown.ttl.disabled"),
                },
                "GR1PIN.TXT": {"type": "file", "size": len(MARKER),
                               "sha256": _sha(restore_dir / "GR1PIN.TXT")},
            },
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    src = Path(installer_src) if installer_src else _resource("logo_installer.ps1")
    installer = src.read_text(encoding="utf-8-sig")
    (out_dir / INSTALLER_NAME).write_bytes(
        b"\xef\xbb\xbf" + installer.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    (out_dir / "\u8bf4\u660e.md").write_text(
        package_readme(text, font_path, size, raw), encoding="utf-8")
    if write_preview:
        to_preview(raw, out_dir / "logo-preview.png")
    return out_dir


def deploy(drive: str, stage: str, package_dir=DEFAULT_PACKAGE_DIR,
           what_if: bool = False, installer_src=None) -> str:
    """调用安装器把某个阶段写到 SD 卡（需要 PowerShell）。

    安装器返回成功前会强制刷新目标文件/卷缓存，并按 manifest 的
    SHA256 重新读取校验。这里保留安装器的完整输出，便于 GUI 和命令行
    在失败时显示真正原因。
    """
    installer = Path(package_dir) / INSTALLER_NAME
    if installer_src and not installer.is_file():
        installer = Path(installer_src)
    if not installer.is_file():
        raise FileNotFoundError("找不到安装器：" + str(installer))
    script_args = [
        "-Drive", "'%s'" % drive.replace("'", "''"),
        "-Stage", "'%s'" % stage.replace("'", "''"),
        "-ConfirmGRCard", "-IUnderstandInternalWrite",
    ]
    if what_if:
        script_args.append("-WhatIf")
    command = "& '%s' %s" % (
        str(installer).replace("'", "''"),
        " ".join(script_args),
    )
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-Command", command]
    print("执行：", " ".join(cmd))
    try:
        result = subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        exc.gr1_stdout = _decode_console(exc.stdout)
        exc.gr1_stderr = _decode_console(exc.stderr)
        raise
    output = _normalize_console_text(_decode_console(result.stdout))
    errors = _normalize_console_text(_decode_console(result.stderr))
    if output:
        print(output.rstrip())
    if errors:
        print(errors.rstrip(), file=sys.stderr)
    combined = (output + errors).strip()
    if not what_if and "GR1_INSTALL_VERIFIED:" not in combined:
        raise RuntimeError(
            "安装器没有返回已验证写入标记，未确认任何文件写入 SD 卡。"
            + (("\n" + combined) if combined else "")
        )
    return combined


def _decode_console(data: bytes) -> str:
    """Decode redirected PowerShell output across common Windows encodings."""
    if not data:
        return ""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    candidates = ["utf-8-sig", "utf-8", locale.getpreferredencoding(False)]
    if os.name == "nt":
        candidates.append("mbcs")
    for encoding in candidates:
        try:
            text = data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        if "\ufffd" not in text:
            return text
    return data.decode("utf-8", errors="replace")


def _normalize_console_text(text: str) -> str:
    return text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n").strip()


def deploy_error_text(exc: subprocess.CalledProcessError) -> str:
    """Return the useful installer output attached to a deploy failure."""
    parts = []
    for name in ("gr1_stderr", "gr1_stdout", "stderr", "stdout"):
        value = getattr(exc, name, "")
        if isinstance(value, bytes):
            value = _decode_console(value)
        if value:
            text = _normalize_console_text(value)
        else:
            text = ""
        if text:
            if text not in parts:
                parts.append(text)
    if not parts:
        return "安装器返回错误码 %s" % exc.returncode
    return "安装器返回错误码 %s：\n%s" % (exc.returncode, "\n".join(parts))


# --- 字体 ---------------------------------------------------------------------
def list_fonts():
    d = Path(r"C:\Windows\Fonts")
    files = sorted((p for p in d.iterdir()
                    if p.suffix.lower() in (".ttf", ".ttc", ".otf")),
                   key=lambda p: p.name.lower())
    for p in files:
        print(p.name, "->", p)
    return files


# --- 交互式 / 图形界面 --------------------------------------------------------
def run_interactive() -> int:
    print("GR1 关机 LOGO 文字自定义（直接回车用默认值）")
    text = input("文字 [%s]: " % DEFAULT_TEXT).strip() or DEFAULT_TEXT
    font = input("字体 [%s]: " % DEFAULT_FONT).strip() or DEFAULT_FONT
    size_s = input("字号（0=自动） [0]: ").strip()
    size = int(size_s) if size_s.isdigit() else 0
    out = input("输出 .brp 路径 [dvf_TDSt.brp]: ").strip() or "dvf_TDSt.brp"

    raw, used = render_payload(text, font, size or None)
    Path(out).write_bytes(raw)
    preview = Path(out).with_suffix(".png")
    to_preview(raw, preview)
    print("已写出 %s（%d 字节，字号 %dpx）" % (out, len(raw), used))
    print("预览：%s" % preview)
    print("sha256：%s" % hashlib.sha256(raw).hexdigest())

    if input("生成安装包到 gr1-logo-package? (y/N): ").strip().lower() == "y":
        build_package(DEFAULT_PACKAGE_DIR, raw, text, font, used)
        print("安装包已生成：" + str(DEFAULT_PACKAGE_DIR))
    return 0


def run_gui(initial_text: str | None = None, initial_font: str | None = None) -> int:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import ImageTk

    state = {
        "text": initial_text or DEFAULT_TEXT,
        "font": initial_font or DEFAULT_FONT,
        "size": 0,
        "align": "center",
        "valign": "middle",
        "gap": 0,
        "margin": CANVAS_MARGIN,
        "invert": False,
    }
    cache = {"raw": None, "size": 0, "tkimg": None}
    pending = {"job": None}

    root = tk.Tk()
    root.title("GR1 关机 LOGO 文字自定义")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=10)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Label(frame, text="文字（可多行）：").grid(row=0, column=0, sticky="w")
    text_widget = tk.Text(frame, width=54, height=3)
    text_widget.insert("1.0", state["text"])
    text_widget.grid(row=1, column=0, columnspan=4, sticky="we")

    ttk.Label(frame, text="字体：").grid(row=2, column=0, sticky="w")
    font_var = tk.StringVar(value=state["font"])
    ttk.Entry(frame, textvariable=font_var, width=40).grid(row=2, column=1, columnspan=2, sticky="we")

    def pick_font():
        p = filedialog.askopenfilename(title="选择字体", initialdir=r"C:\Windows\Fonts",
                                       filetypes=[("字体", "*.ttf *.ttc *.otf"), ("全部", "*.*")])
        if p:
            font_var.set(p)

    ttk.Button(frame, text="浏览…", command=pick_font).grid(row=2, column=3, sticky="e")

    opts = ttk.Frame(frame)
    opts.grid(row=3, column=0, columnspan=4, sticky="we", pady=(8, 0))
    ttk.Label(opts, text="字号(0=自动)：").grid(row=0, column=0, sticky="w")
    size_var = tk.IntVar(value=0)
    ttk.Spinbox(opts, from_=0, to=MAX_FONT_SIZE, textvariable=size_var, width=5).grid(row=0, column=1)
    ttk.Label(opts, text="水平：").grid(row=0, column=2, sticky="e")
    align_var = tk.StringVar(value="center")
    ttk.Combobox(opts, textvariable=align_var, values=("left", "center", "right"),
                 width=7, state="readonly").grid(row=0, column=3)
    ttk.Label(opts, text="垂直：").grid(row=0, column=4, sticky="e")
    valign_var = tk.StringVar(value="middle")
    ttk.Combobox(opts, textvariable=valign_var, values=("top", "middle", "bottom"),
                 width=7, state="readonly").grid(row=0, column=5)
    ttk.Label(opts, text="行距：").grid(row=0, column=6, sticky="e")
    gap_var = tk.IntVar(value=0)
    ttk.Spinbox(opts, from_=0, to=200, textvariable=gap_var, width=4).grid(row=0, column=7)
    invert_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(opts, text="黑字白底", variable=invert_var).grid(row=0, column=8, padx=(8, 0))

    canvas = tk.Canvas(frame, width=WIDTH, height=HEIGHT, bg="black", highlightthickness=1,
                       highlightbackground="#444")
    canvas.grid(row=4, column=0, columnspan=4, pady=8)

    status = ttk.Label(frame, text="", foreground="#555")
    status.grid(row=5, column=0, columnspan=4, sticky="w")

    bar = ttk.Frame(frame)
    bar.grid(row=6, column=0, columnspan=4, sticky="we", pady=(8, 0))

    def sync_state(*_):
        state["text"] = text_widget.get("1.0", "end-1c")
        state["font"] = font_var.get().strip() or DEFAULT_FONT
        try:
            state["size"] = int(size_var.get())
        except Exception:
            state["size"] = 0
        state["align"] = align_var.get()
        state["valign"] = valign_var.get()
        try:
            state["gap"] = int(gap_var.get())
        except Exception:
            state["gap"] = 0
        state["invert"] = bool(invert_var.get())

    def refresh(*_):
        sync_state()
        try:
            raw, used = render_payload(
                state["text"], state["font"], state["size"] or None,
                align=state["align"], valign=state["valign"],
                line_gap=state["gap"], invert=state["invert"])
        except Exception as e:  # 渲染失败时保留旧预览
            status.config(text="渲染失败：%s" % e, foreground="#b00")
            return
        cache["raw"], cache["size"] = raw, used
        cache["tkimg"] = ImageTk.PhotoImage(to_preview(raw))
        canvas.delete("all")
        canvas.create_image(0, 0, anchor="nw", image=cache["tkimg"])
        status.config(
            text="字号 %dpx   |   %d 字节   |   sha256 %s…"
                 % (used, len(raw), hashlib.sha256(raw).hexdigest()[:16]),
            foreground="#555")

    def schedule(*_):
        if pending["job"] is not None:
            root.after_cancel(pending["job"])
        pending["job"] = root.after(150, refresh)

    text_widget.bind("<KeyRelease>", schedule)
    for var in (font_var, align_var, valign_var, invert_var):
        var.trace_add("write", schedule)
    for var in (size_var, gap_var):
        var.trace_add("write", schedule)

    def do_save_brp():
        if cache["raw"] is None:
            refresh()
        p = filedialog.asksaveasfilename(defaultextension=".brp", initialfile="dvf_TDSt.brp")
        if not p:
            return
        Path(p).write_bytes(cache["raw"])
        to_preview(cache["raw"], Path(p).with_suffix(".png"))
        messagebox.showinfo("已保存", "%s\n（同名 .png 预览也一并保存）" % p)

    def do_package():
        if cache["raw"] is None:
            refresh()
        d = filedialog.askdirectory(title="选择安装包输出目录")
        if not d:
            return
        try:
            build_package(d, cache["raw"], state["text"], state["font"], cache["size"])
        except Exception as e:
            messagebox.showerror("生成失败", str(e))
            return
        messagebox.showinfo("已生成安装包", d)
        status.config(text="已生成安装包：%s" % d, foreground="#080")

    def do_deploy(stage):
        if cache["raw"] is None:
            refresh()
        drive = drive_var.get().strip() or "E:"
        if not messagebox.askyesno(
                "确认写入内部 NAND",
                "即将把「%s」阶段写入 %s 上的 SD 卡。\n"
                "真正的写入发生在相机下一次正常关机时，会修改内部 NAND 的 B:/dvf_TDSt.brp。\n\n继续？"
                % (stage, drive)):
            return
        status.config(text="正在写入并校验，请勿拔卡…", foreground="#b60")
        root.config(cursor="watch")
        root.update_idletasks()
        try:
            build_package(DEFAULT_PACKAGE_DIR, cache["raw"], state["text"],
                          state["font"], cache["size"])
            deploy(drive, stage, DEFAULT_PACKAGE_DIR)
        except subprocess.CalledProcessError as e:
            detail = deploy_error_text(e)
            status.config(text="部署失败", foreground="#b00")
            messagebox.showerror("部署失败", detail)
            return
        except Exception as e:
            status.config(text="部署失败：%s" % e, foreground="#b00")
            messagebox.showerror("部署失败", str(e))
            return
        finally:
            root.config(cursor="")
        status.config(text="已刷盘并校验：%s" % drive, foreground="#080")
        messagebox.showinfo(
            "写入完成",
            "已写入 %s，并已完成强制刷盘和 SHA256 校验。\n"
            "现在可以拔卡；插到相机后先正常关机一次完成内部写入，\n"
            "再开机并正常关机一次查看新图。\n\n"
            "注意：生效后请删除卡上的 Shutdown.ttl，避免每次关机重复写入。" % drive)

    ttk.Button(bar, text="保存 .brp", command=do_save_brp).grid(row=0, column=0)
    ttk.Button(bar, text="生成安装包…", command=do_package).grid(row=0, column=1, padx=6)
    ttk.Label(bar, text="卡盘符：").grid(row=0, column=2, padx=(12, 0))
    drive_var = tk.StringVar(value="E:")
    ttk.Entry(bar, textvariable=drive_var, width=4).grid(row=0, column=3)
    ttk.Button(bar, text="部署·替换", command=lambda: do_deploy("install-logo")).grid(row=0, column=4, padx=6)
    ttk.Button(bar, text="部署·还原", command=lambda: do_deploy("restore-logo")).grid(row=0, column=5)

    root.after(50, refresh)
    root.mainloop()
    return 0


# --- 命令行 -------------------------------------------------------------------
EXAMPLES = r"""示例：
  # 图形界面（推荐）
  python gr1_logo_text.py --gui

  # 默认文字，只出一个 .brp + 预览
  python gr1_logo_text.py --out dvf_TDSt.brp --preview logo.png

  # 自定义文字，生成完整安装包
  python gr1_logo_text.py --text "Hello GR1" --package gr1-logo-package

  # 生成并直接写到 E: 卡（安装阶段）
  python gr1_logo_text.py --text "Hello GR1" --deploy E:

  # 生成还原包并写到 E: 卡（删除 B:/dvf_TDSt.brp）
  python gr1_logo_text.py --deploy-restore E:
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="GR1 关机 LOGO 文字自定义 / 打包 / 部署工具",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EXAMPLES)
    src = p.add_mutually_exclusive_group()
    src.add_argument("--text", help=r"要显示的文字（用 \n 表示换行）")
    src.add_argument("--text-file", help="从 UTF-8 文本文件读取文字")
    p.add_argument("--font", default=DEFAULT_FONT, help="字体文件（默认 %(default)s）")
    p.add_argument("--size", type=int, default=0, help="字号；0=自动适配（默认）")
    p.add_argument("--max-width", type=int, default=DEFAULT_MAX_WIDTH)
    p.add_argument("--max-height", type=int, default=DEFAULT_MAX_HEIGHT)
    p.add_argument("--margin", type=int, default=CANVAS_MARGIN)
    p.add_argument("--line-gap", type=int, default=0)
    p.add_argument("--align", choices=("left", "center", "right"), default="center")
    p.add_argument("--valign", choices=("top", "middle", "bottom"), default="middle")
    p.add_argument("--invert", action="store_true", help="黑字白底")
    p.add_argument("--out", help="输出 .brp 文件路径")
    p.add_argument("--preview", help="输出预览 PNG 路径")
    p.add_argument("--package", nargs="?", const=str(DEFAULT_PACKAGE_DIR),
                   help="生成安装包到该目录（默认 %(const)s）")
    p.add_argument("--installer", help="安装器模板 logo_installer.ps1 路径")
    p.add_argument("--deploy", metavar="DRIVE", help="把安装包部署到该盘（如 E:）")
    p.add_argument("--deploy-restore", metavar="DRIVE", help="把还原包部署到该盘（如 E:）")
    p.add_argument("--what-if", action="store_true", help="部署时只检查，不真正写卡")
    p.add_argument("--gui", action="store_true", help="打开图形界面")
    p.add_argument("--list-fonts", action="store_true", help="列出可用字体")
    args = p.parse_args(argv)

    if args.list_fonts:
        list_fonts()
        return 0

    if args.gui:
        return run_gui()

    has_task = any([args.text, args.text_file, args.out, args.preview,
                    args.package, args.deploy, args.deploy_restore])
    if not has_task:
        if sys.stdin and sys.stdin.isatty():
            return run_interactive()
        p.print_help()
        return 0

    if args.text_file:
        # utf-8-sig 去掉记事本/PowerShell 可能写入的 BOM；再去掉一个行尾换行。
        text = Path(args.text_file).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        if text.endswith("\n"):
            text = text[:-1]
    elif args.text is not None:
        text = args.text
    else:
        text = DEFAULT_TEXT

    raw, used = render_payload(
        text, args.font, args.size or None,
        max_width=args.max_width, max_height=args.max_height,
        margin=args.margin, align=args.align, valign=args.valign,
        line_gap=args.line_gap, invert=args.invert)

    print("字号 %dpx | %d 字节 | sha256 %s"
          % (used, len(raw), hashlib.sha256(raw).hexdigest()))

    if args.out:
        Path(args.out).write_bytes(raw)
        print("已写出 .brp：" + args.out)
    if args.preview:
        to_preview(raw, args.preview)
        print("已写出预览：" + args.preview)

    need_package = bool(args.package) or bool(args.deploy) or bool(args.deploy_restore)
    pkg_dir = Path(args.package) if args.package else DEFAULT_PACKAGE_DIR
    if need_package:
        build_package(pkg_dir, raw, text, args.font, used, installer_src=args.installer)
        print("已生成安装包：" + str(pkg_dir))

    if args.deploy:
        try:
            deploy(args.deploy, "install-logo", pkg_dir, what_if=args.what_if,
                   installer_src=args.installer)
        except subprocess.CalledProcessError as e:
            print("部署失败：" + deploy_error_text(e))
            return 1
    if args.deploy_restore:
        try:
            deploy(args.deploy_restore, "restore-logo", pkg_dir, what_if=args.what_if,
                   installer_src=args.installer)
        except subprocess.CalledProcessError as e:
            print("部署失败：" + deploy_error_text(e))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
