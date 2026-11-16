# ======================================================================
# KalshiDesk —— 打包 Windows 桌面 EXE（单文件便携版）
# ======================================================================
# 使用方法：powershell -ExecutionPolicy Bypass -File .\scripts\build_desktop.ps1
# 产出目录：.\dist\KalshiDesk.exe
# 特点：单文件、拷走 exe 即用，首次运行自动在同级生成 data\ 目录
# ======================================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$VenvPython = ".\.venv\Scripts\python.exe"
$VenvFlet = ".\.venv\Scripts\flet.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERROR] 未找到虚拟环境 .venv" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════╗"
Write-Host "║     KalshiDesk —— 打包 Windows 单文件 EXE              ║"
Write-Host "╚══════════════════════════════════════════════════════╝"
Write-Host ""

# 安装 PyInstaller（如果未安装）
& $VenvPython -m pip install --quiet "pyinstaller>=6.6"

# 清理旧产物
if (Test-Path ".\dist\desktop") {
    Remove-Item ".\dist\desktop" -Recurse -Force
}
if (Test-Path ".\build") {
    Remove-Item ".\build" -Recurse -Force
}

# flet pack：封装 PyInstaller，自动处理 Flet client + 资源文件
# -n KalshiDesk.exe  指定输出文件名
# --distpath 指定输出目录到 dist\desktop
# --hidden-import  防止 PyInstaller 扫描不到 duckdb / cryptography 的动态导入
& $VenvFlet pack main.py `
    -n "KalshiDesk.exe" `
    --distpath ".\dist\desktop" `
    --hidden-import duckdb `
    --hidden-import cryptography `
    --hidden-import cryptography.hazmat `
    --hidden-import cryptography.hazmat.primitives `
    --hidden-import cryptography.hazmat.primitives.asymmetric `
    --hidden-import cryptography.hazmat.primitives.asymmetric.padding `
    --hidden-import cryptography.hazmat.primitives.asymmetric.rsa `
    --hidden-import cryptography.hazmat.primitives import hashes `
    --hidden-import cryptography.hazmat.primitives import serialization `
    --hidden-import socks

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] flet pack 失败" -ForegroundColor Red
    exit 1
}

# 找到产出的 exe（flet pack 可能把输出放到不同位置，这里兜底搜索）
$exePath = Get-ChildItem ".\dist\desktop\KalshiDesk.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $exePath) {
    $exePath = Get-ChildItem ".\dist\**\KalshiDesk.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
}

if ($exePath) {
    $size = "{0:N1} MB" -f ($exePath.Length / 1MB)
    Write-Host ""
    Write-Host "✓ 打包完成！" -ForegroundColor Green
    Write-Host "  产物：$($exePath.FullName)"
    Write-Host "  大小：$size"
    Write-Host ""
    Write-Host "首次运行 exe 会在同级生成 data\ 目录，保存 PEM 私钥、登录会话与 DuckDB 数据库。"
    Write-Host "拷走 exe + data\ 即可迁移到另一台机器。"
    Write-Host ""
} else {
    Write-Host "[WARN] 未找到 KalshiDesk.exe，请检查 dist\ 目录" -ForegroundColor Yellow
}

# 清理 build 中间目录
if (Test-Path ".\build") {
    Remove-Item ".\build" -Recurse -Force
}
