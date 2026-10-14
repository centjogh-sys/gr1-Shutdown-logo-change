# GR1 关机 LOGO 替换工具

一个用于 RICOH GR 系列相机的关机 LOGO 自定义工具。它把指定文字渲染成相机可读取的
`dvf_TDSt.brp` 图像文件，并生成安装/还原 SD 卡包。

默认文字：

```text
Ciallo～(∠・ω< )⌒★
```

![默认预览](docs/logo-preview.png)

## 重要说明

- 这不是刷固件工具，不修改启动、升级或固件分区。
- 安装时会通过关机脚本写入相机内部 NAND 的一个文件：
  `B:/dvf_TDSt.brp`。
- 安装会先删除旧的 `B:/dvf_TDSt.brp`，再复制新文件；还原会删除该文件。
- 内部 NAND 写入有风险，使用即表示自行承担风险。
- 如果相机无法启动且问题来自 B: 文件系统损坏，SD 卡包没有独立救砖能力。
  出问题时第一手止损是拔掉 SD 卡；必要时再取电池。
- 本项目与 RICOH 没有关联。

## 已验证的行为

默认文字、字体和自动字号渲染出的载荷已经与实机验证过的成品逐字节一致：

```text
sha256 577bd3c2e05da73f967222138d7547009857a0ad98417e1dff890c0084f2c894
```

实机流程中，部署后的第一次正常关机负责写入，新的关机图可能在后续正常关机时才会稳定
显示。推荐流程：

1. 部署安装包到 GR 格式化的卡。
2. 把卡插入相机，正常关机一次，等待写入完成。
3. 开机，再正常关机一次查看新 LOGO。
4. 看到新图后删除卡上的 `Shutdown.ttl`，避免每次关机重复写入。

如果第一次没有看到变化，不要连续快速插拔或强制断电；等待相机完成写入后，再按正常
开关机流程测试。

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- Pillow
- PowerShell 5.1 或更高版本
- 生成 EXE 时需要 PyInstaller

安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

## 快速开始

打开图形界面：

```powershell
python gr1_logo_app.py
```

也可以直接运行主程序：

```powershell
python gr1_logo_text.py --gui
```

界面中可以输入文字、选择字体、调整字号、对齐、行距和反色，并实时预览。确认后可以：

- 保存 `.brp` 载荷
- 生成完整安装包
- 选择卡盘符后部署替换
- 部署还原包

部署时工具会复制文件、刷新文件和卷缓存，并按 SHA256 重新读取校验。提示写入完成后
再拔卡。

## 命令行

生成默认 `.brp` 和预览图：

```powershell
python gr1_logo_text.py --out dvf_TDSt.brp --preview logo.png
```

生成自定义文字的安装包：

```powershell
python gr1_logo_text.py --text "Hello GR1" --package gr1-logo-package
```

生成并直接部署到 `E:`：

```powershell
python gr1_logo_text.py --text "Hello GR1" --deploy E:
```

部署还原包：

```powershell
python gr1_logo_text.py --deploy-restore E:
```

列出系统字体：

```powershell
python gr1_logo_text.py --list-fonts
```

完整参数：

```text
--text --text-file --font --size --max-width --max-height --margin
--line-gap --align --valign --invert --out --preview --package
--installer --deploy --deploy-restore --what-if --gui --list-fonts
```

## 生成和校验安装包

生成默认安装包：

```powershell
python make_logo_package.py
```

离线校验，不访问相机或 SD 卡：

```powershell
python verify_logo_package.py
```

当前校验结果为 `PASS: 93/93`。校验内容包括载荷长度、合法像素值、清单 SHA256、
TTL 命令白名单、安装器护栏和缓存刷新逻辑。

生成的目录结构：

```text
gr1-logo-package/
├── 安装LOGO.ps1
├── manifest.json
├── 说明.md
├── logo-preview.png
├── install-logo/
│   ├── Shutdown.ttl.disabled
│   ├── GR1PIN.TXT
│   └── gr1logo/
│       └── dvf_TDSt.brp
└── restore-logo/
    ├── Shutdown.ttl.disabled
    └── GR1PIN.TXT
```

## 构建 EXE

构建默认 `v3` 单文件无控制台 EXE：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_logo_exe.ps1 -Version v3
```

输出：

```text
GR1关机LOGO工具\GR1关机LOGO工具-v3.exe
```

EXE 体积约 30 MB。建议把构建产物放到 GitHub Releases，而不是提交进仓库历史。

## 卡片要求

安装器只接受：

- 可移动介质
- FAT32
- RICOH 卡（卷标以 `RICOH` 开头，或存在编号的 `###RICOH` 照片目录）
- 至少有 16 MiB 可用空间
- 卡上没有残留的 `Startup.ttl`、`Shutdown.ttl`、`update.ttl`、探针输出或
  `gr1logo` 目录

安装器会拒绝松下/GX9 卡和其他工作流留下的固件、滤镜文件。清理卡时保留 `DCIM` 和
`MISC`。

## 图像格式

- 尺寸：`640 x 480`
- 每像素：2 字节，小端 `u16`
- 文件大小：`614400` 字节
- 合法值：`value(level) = (level << 12) | 0x0F00 | (level << 4) | level`
- `level 0` 是透明键，`level 15` 是白色
- 灰度到 level 的映射：`round(pixel / 255 * 15)`

## 仓库内容

- `gr1_logo_app.py`：GUI/命令行 EXE 入口
- `gr1_logo_text.py`：文字渲染、安装包生成和部署
- `logo_installer.ps1`：带护栏的 SD 卡安装器
- `make_logo_package.py`：生成默认安装包
- `verify_logo_package.py`：离线校验
- `build_logo_exe.ps1`：PyInstaller 构建脚本
- `docs/logo-preview.png`：默认文字预览
