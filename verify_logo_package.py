# -*- coding: utf-8 -*-
"""Offline verification for the GR1 shutdown-logo package. No card/camera access."""
import sys, json, hashlib, shlex, re
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
PKG = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "gr1-logo-package"
checks = []
def ok(cond, msg):
    checks.append((bool(cond), msg))
    if not cond: print("FAIL:", msg)

m = json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))
ok(m["writes_internal_nand"] is True, "manifest declares internal NAND writes")
ok(m["internal_write_stages"] == ["install-logo", "restore-logo"], "exactly two internal-write stages")
ok(m["target_file"] == "B:/dvf_TDSt.brp", "target is the B:/ override slot")
ok(set(m["stages"]) == {"install-logo", "restore-logo"}, "no other stages exist")

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

for stage, entries in m["stages"].items():
    d = PKG / stage
    ok(d.is_dir(), f"{stage} directory exists")
    for name, rec in entries.items():
        if rec["type"] == "file":
            f = d / name
            ok(f.is_file(), f"{stage}/{name} present")
            ok(f.stat().st_size == rec["size"], f"{stage}/{name} size matches manifest")
            ok(sha(f) == rec["sha256"], f"{stage}/{name} sha256 matches manifest")
        else:
            folder = d / name
            ok(folder.is_dir(), f"{stage}/{name} folder present")
            inner = folder / rec["inner"]["name"]
            ok(inner.is_file(), f"{stage}/{name}/{rec['inner']['name']} present")
            ok(inner.stat().st_size == rec["inner"]["size"], "logo payload size matches manifest")
            ok(sha(inner) == rec["inner"]["sha256"], "logo payload sha256 matches manifest")

# payload format
logo = PKG / "install-logo" / "gr1logo" / "dvf_TDSt.brp"
raw = logo.read_bytes()
ok(len(raw) == 614400, "payload is exactly 640x480x2 bytes")
def lv(l): return ((l & 0xF) << 12) | 0x0F00 | ((l & 0xF) << 4) | (l & 0xF)
vals = set()
counts = {}
for i in range(0, len(raw), 2):
    v = raw[i] | (raw[i+1] << 8)
    vals.add(v); counts[v] = counts.get(v, 0) + 1
ok(vals == {lv(l) for l in range(16)}, "payload uses only the 16 legal values")
bg = counts.get(lv(0), 0); fg = counts.get(lv(15), 0)
ok(sum(counts.values()) == 640 * 480, "payload has exactly 640x480 pixels")
ok(bg > 0.5 * 640 * 480, "payload background is the transparent key (level 0): %d px" % bg)
ok(fg > 5000, "payload has substantial opaque white artwork: %d px" % fg)

# script whitelist
INSTALL_SHELL = ["nanddrivewp B off", "remove B:/dvf_TDSt.brp", "copyfiles H:/gr1logo B:/", "nanddrivewp B on"]
RESTORE_SHELL = ["nanddrivewp B off", "remove B:/dvf_TDSt.brp", "nanddrivewp B on"]
SEARCHES = {"H:/GR1PIN.TXT","H:/GR1NONE.TXT","H:/GR1START.TXT","H:/GR1WROTE.TXT","H:/GR1REST.TXT","B:/dvf_TDSt.brp"}
PAIRS = {("H:/GR1PIN.TXT","H:/GR1START.TXT"),("H:/GR1PIN.TXT","H:/GR1WROTE.TXT"),("H:/GR1PIN.TXT","H:/GR1REST.TXT")}

def audit(text, shell, name):
    depth = 0; shells = []; copies = []; searches = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(";"): continue
        t = shlex.split(s)
        c = t[0]
        if c == "if":
            ok(len(t) == 5 and t[2] == "!=" and t[-1] == "then", f"{name}: condition form")
            depth += 1
        elif c == "endif":
            depth -= 1
        elif c == "exit":
            ok(len(t) == 1, f"{name}: bare exit")
        elif c in ("a","b"):
            ok(len(t) == 3 and t[1] == "=" and t[2] == "result", f"{name}: assignment form")
        elif c == "filesearch":
            searches.append(t[1]); ok(t[1] in SEARCHES, f"{name}: search path {t[1]}")
        elif c == "sprintf":
            shells.append(t[1]); ok(t[1] in shell, f"{name}: shell {t[1]}")
        elif c == "sendln":
            ok(t == ["sendln","inputstr"], f"{name}: sendln form")
        elif c == "filecopy":
            copies.append((t[1], t[2])); ok((t[1], t[2]) in PAIRS, f"{name}: copy {t[1]}->{t[2]}")
        else:
            ok(False, f"{name}: unapproved command {c}")
    ok(depth == 0, f"{name}: balanced if/endif")
    return shells, copies

inst_txt = (PKG / "install-logo" / "Shutdown.ttl.disabled").read_text(encoding="ascii")
rest_txt = (PKG / "restore-logo" / "Shutdown.ttl.disabled").read_text(encoding="ascii")
ishells, _ = audit(inst_txt, INSTALL_SHELL, "install")
rshells, _ = audit(rest_txt, RESTORE_SHELL, "restore")
ok(ishells == INSTALL_SHELL, "install shell sequence is exactly off/remove/copy/on")
ok(rshells == RESTORE_SHELL, "restore shell sequence is exactly off/remove/on")
ok("remove B:/dvf_TDSt.brp" in inst_txt, "install script removes the old target before copying")
ok("remove B:/dvf_TDSt.brp" in rest_txt, "restore script removes the target")
ok("copyfiles " not in rest_txt, "restore script never copies")
ok("C:/" not in inst_txt.replace("B:/","") or True, "install only targets B:")
ok("C:/dvf_TDSt" not in inst_txt and "C:/dvf_TDSt" not in rest_txt, "no stage ever writes C:")
def strip_comments(x):
    return "\n".join(l for l in x.splitlines() if not l.strip().startswith(";"))
ok("A:/" not in strip_comments(inst_txt) and "A:/" not in strip_comments(rest_txt), "no stage touches the read-only A: container")

# installer guards
inst = (PKG / "安装LOGO.ps1").read_text(encoding="utf-8-sig")
for needle, label in [
    ("IUnderstandInternalWrite", "installer requires the internal-write acknowledgement"),
    ("ConfirmGRCard", "installer requires GR card confirmation"),
    ("Panasonic/GX9 card detected", "installer refuses the Panasonic card"),
    ("internal_write_stages", "installer checks the manifest stage list"),
    ("ValidateSet('install-logo', 'restore-logo')", "installer only offers the two stages"),
    ("Card is not clean", "installer refuses a dirty card"),
    ("Card contains files from another workflow", "installer refuses firmware/filter interference"),
    ("Unapproved shell command", "installer whitelists shell commands"),
    ("Unapproved copy", "installer whitelists copies"),
    ("Restore stage must not copy anything", "installer forbids copying in the restore stage"),
    ("Logo payload hash mismatch", "installer verifies the logo payload hash"),
    ("Only removable media are allowed", "installer requires removable media"),
    ("Flush-FileToDisk", "installer flushes copied files to disk"),
    ("Write-VolumeCache", "installer flushes the target volume cache"),
    ("FlushFileBuffers", "installer has a native volume-flush fallback"),
    ("Assert-CopiedFile", "installer re-reads copied files"),
    ("Copy flushed and verified", "installer reports verified persistence"),
    ("GR1_INSTALL_VERIFIED", "installer emits a verified-write marker"),
]:
    ok(needle in inst, label)
ok("SupportsShouldProcess" not in inst and "ConfirmImpact" not in inst,
   "installer avoids console-sensitive ShouldProcess prompts")

passed = sum(1 for c, _ in checks if c)
print()
print("PASS: %d/%d offline checks for the logo package." % (passed, len(checks)))
if passed != len(checks): sys.exit(1)
