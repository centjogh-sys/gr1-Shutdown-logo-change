# 重新构建 GR1关机LOGO工具.exe（单文件、无控制台窗口）。
# 需要 Python 3 + Pillow + PyInstaller。
param(
    [string]$Version = 'v3'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

python -m pip install --disable-pip-version-check --quiet pyinstaller

$ps1 = (Resolve-Path -LiteralPath .\logo_installer.ps1).Path
$appName = "GR1关机LOGO工具-$Version"
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name $appName `
    --add-data "$ps1;." `
    --distpath "gr1-logo-exe" --workpath "gr1-logo-exe\_build-$Version" --specpath "gr1-logo-exe" `
    gr1_logo_app.py

Copy-Item -LiteralPath "gr1-logo-exe\$appName.exe" `
    -Destination "GR1关机LOGO工具\$appName.exe" -Force
Write-Host "完成：GR1关机LOGO工具\$appName.exe"
