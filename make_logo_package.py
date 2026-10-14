# -*- coding: utf-8 -*-
"""Generate the GR1 shutdown-logo deployment package (install + restore)."""
from pathlib import Path
import hashlib, json, shlex, os
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "gr1-logo-package"
FONT = r"C:\Windows\Fonts\msyhbd.ttc"
TEXT = "Ciallo\uff5e(\u2220\u30fb\u03c9< )\u2312\u2605"
WIDTH, HEIGHT = 640, 480
PAYLOAD_SIZE = WIDTH * HEIGHT * 2
MARKER = b"GR1 shutdown-logo package. A matching output proves only this copy completed.\r\n"

def level_to_value(l):
    return ((l & 0xF) << 12) | 0x0F00 | ((l & 0xF) << 4) | (l & 0xF)

def build_logo(path):
    best = None
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    for size in range(160, 15, -2):
        f = ImageFont.truetype(FONT, size)
        bb = probe.textbbox((0, 0), TEXT, font=f)
        if bb[2] - bb[0] <= 604 and bb[3] - bb[1] <= 150:
            best = (size, f, bb); break
    if best is None:
        raise ValueError("could not fit the logo text")
    size, f, bb = best
    img = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(img).text(((WIDTH - (bb[2]-bb[0]))//2 - bb[0], (HEIGHT - (bb[3]-bb[1]))//2 - bb[1]), TEXT, fill=255, font=f)
    px = img.load()
    values = [level_to_value(l) for l in range(16)]
    out = bytearray(PAYLOAD_SIZE); i = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            v = values[max(0, min(15, round(px[x, y] / 255 * 15)))]
            out[i] = v & 0xFF; out[i+1] = (v >> 8) & 0xFF; i += 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return size

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
SEARCHES = ("H:/GR1PIN.TXT", "H:/GR1NONE.TXT", "H:/GR1START.TXT", "H:/GR1WROTE.TXT", "H:/GR1REST.TXT", "B:/dvf_TDSt.brp")
PAIRS = (("H:/GR1PIN.TXT", "H:/GR1START.TXT"), ("H:/GR1PIN.TXT", "H:/GR1WROTE.TXT"), ("H:/GR1PIN.TXT", "H:/GR1REST.TXT"))
OPERANDS = ("a", "b", "result")

def validate(text, shell):
    depth = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        t = shlex.split(s)
        c = t[0]
        if c == "if":
            if len(t) != 5 or t[2] != "!=" or t[-1] != "then" or t[1] not in OPERANDS or t[3] not in OPERANDS:
                raise ValueError("Unapproved condition: " + s)
            depth += 1
        elif c == "endif":
            depth -= 1
            if depth < 0: raise ValueError("Unbalanced")
        elif c == "exit":
            if len(t) != 1: raise ValueError("Invalid exit")
        elif c in ("a", "b"):
            if len(t) != 3 or t[1] != "=" or t[2] != "result": raise ValueError("Only '<var> = result'")
        elif c == "filesearch":
            if len(t) != 2 or t[1] not in SEARCHES: raise ValueError("Unapproved search: " + s)
        elif c == "sprintf":
            if len(t) != 2 or t[1] not in shell: raise ValueError("Unapproved shell command: " + s)
        elif c == "sendln":
            if t != ["sendln", "inputstr"]: raise ValueError("Only 'sendln inputstr'")
        elif c == "filecopy":
            if len(t) != 3 or (t[1], t[2]) not in PAIRS: raise ValueError("Unapproved copy: " + s)
        else:
            raise ValueError("Unapproved command: " + c)
    if depth: raise ValueError("Unbalanced")
    payload = text.replace("\n", "\r\n").encode("ascii")
    if max(map(len, payload.split(b"\r\n"))) >= 256: raise ValueError("Line too long")
    return payload

README = """# GR1 关机 LOGO 替换包（写入内部 NAND，风险自担）

**这个包和 `sd-probe` 不一样：它会写相机的内部 NAND。**
只写一个文件：`B:/dvf_TDSt.brp`。除此之外不碰任何东西。

## 原理

关机时固件按这个顺序找图（`b01firm8` 文件 0x35132E，逐指令复核）：

```
B:/dvf_TDSt.brp   ->   C:/dvf_TDSt.brp   ->   A:/img/dvf_TDSt.brp（固件内置）
```

本机实测 B:/ 和 C: 根目录**都没有**这个文件，所以现在显示的是内置那份 RICOH。
往 B:/ 放一份，就会盖掉它。删掉，就完全回到现在这样。
安装时会先删除旧的 `B:/dvf_TDSt.brp`，再复制新文件，重复安装也能稳定替换。

## 用法

```
# 安装/替换（第一次正常关机时写入，第二次正常关机显示新图）
& .\\安装LOGO.ps1 -Drive E: -Stage install-logo -ConfirmGRCard -IUnderstandInternalWrite

# 还原（下一次正常关机时删除）
& .\\安装LOGO.ps1 -Drive E: -Stage restore-logo -ConfirmGRCard -IUnderstandInternalWrite
```

写入/删除发生在**关机脚本执行时**，也就是你正常关机的那一次。
新图不一定在写入的同一刻就显示：推荐部署 -> 第一次正常关机（写入）-> 开机 -> 第二次正常关机（看到新图）。

## 判读

| 卡上出现的文件 | 含义 |
| --- | --- |
| `GR1START.TXT` | 关机脚本跑到了写 NAND 那一步 |
| `GR1WROTE.TXT` | 写完后 `filesearch "B:/dvf_TDSt.brp"` 命中 -> 文件在 B: 上 |
| `GR1REST.TXT` | 还原脚本执行完成 |

`GR1WROTE.TXT` 出现后，再跑一次 `sd-probe` 的 `recon-shutdown`，卡上会多出
`dvf_TDSt.brp`（那是 `copyfiles B:/ H:/` 把 B: 倒出来的），用电脑比对字节即可确认写对了。

## 风险（如实说明）

- 这是内部 NAND 写入。相机自己在正常使用中也会写 B:（`P_COMMON.bin` 的修改时间
  就是探针运行的那一刻），但"关机脚本里写完紧接着断电"这个时序没有先例。
- 如果 B: 文件系统因此损坏、且相机无法开机，SD 卡救不回来 —— 没有独立于 APP 的救砖链。
- 安装只会删除并替换 `B:/dvf_TDSt.brp` 这一个目标文件；还原就是删掉它，不覆盖其他文件。
- 最坏情况下的第一手止损依然是：**拔卡**（必要时再取电池），回到"没有 SD 脚本"的状态。

## 卡上不能有别的入口

安装器会拒绝任何残留的 `Startup.ttl` / `Shutdown.ttl` / `update.ttl` / `gr1logo` /
探针输出文件。请先把卡清干净（保留 `DCIM`、`MISC`）。
"""

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    install_dir = PACKAGE / "install-logo"
    restore_dir = PACKAGE / "restore-logo"
    for d in (install_dir, restore_dir):
        d.mkdir(parents=True, exist_ok=True)
        for stale in d.glob("*.ttl"):
            raise ValueError("Active TTL left in package: " + str(stale))

    logo = install_dir / "gr1logo" / "dvf_TDSt.brp"
    size = build_logo(logo)
    (install_dir / "GR1PIN.TXT").write_bytes(MARKER)
    (restore_dir / "GR1PIN.TXT").write_bytes(MARKER)
    (install_dir / "Shutdown.ttl.disabled").write_bytes(validate(INSTALL, INSTALL_SHELL))
    (restore_dir / "Shutdown.ttl.disabled").write_bytes(validate(RESTORE, RESTORE_SHELL))

    manifest = {
        "package": "gr1-shutdown-logo",
        "writes_internal_nand": True,
        "internal_write_stages": ["install-logo", "restore-logo"],
        "target_file": "B:/dvf_TDSt.brp",
        "logo_source_text": TEXT,
        "logo_font": os.path.basename(FONT),
        "logo_font_size": size,
        "stages": {
            "install-logo": {
                "Shutdown.ttl.disabled": {"type": "file", "size": (install_dir / "Shutdown.ttl.disabled").stat().st_size, "sha256": sha(install_dir / "Shutdown.ttl.disabled")},
                "GR1PIN.TXT": {"type": "file", "size": len(MARKER), "sha256": sha(install_dir / "GR1PIN.TXT")},
                "gr1logo": {"type": "directory", "inner": {"name": "dvf_TDSt.brp", "size": logo.stat().st_size, "sha256": sha(logo)}},
            },
            "restore-logo": {
                "Shutdown.ttl.disabled": {"type": "file", "size": (restore_dir / "Shutdown.ttl.disabled").stat().st_size, "sha256": sha(restore_dir / "Shutdown.ttl.disabled")},
                "GR1PIN.TXT": {"type": "file", "size": len(MARKER), "sha256": sha(restore_dir / "GR1PIN.TXT")},
            },
        },
    }
    (PACKAGE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    installer = (ROOT / "logo_installer.ps1").read_text(encoding="utf-8-sig")
    (PACKAGE / "安装LOGO.ps1").write_bytes(b"\xef\xbb\xbf" + installer.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    (PACKAGE / "说明.md").write_text(README, encoding="utf-8")
    print("logo font size:", size, "payload bytes:", logo.stat().st_size)
    print("package written to", PACKAGE)

if __name__ == "__main__":
    main()
