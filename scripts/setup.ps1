# ======================================================================
# KalshiDesk —— 一键初始化（创建虚拟环境 + 安装依赖）
# ======================================================================
# 使用方法：powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
# ======================================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════╗"
Write-Host "║     KalshiDesk —— 环境初始化                            ║"
Write-Host "╚══════════════════════════════════════════════════════╝"
Write-Host ""

# 1) 创建虚拟环境
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "[1/3] 创建虚拟环境 .venv ..." -ForegroundColor Cyan
    python -m venv .venv
} else {
    Write-Host "[1/3] 虚拟环境已存在，跳过" -ForegroundColor DarkGray
}

# 2) 安装依赖
Write-Host "[2/3] 安装项目依赖 ..." -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 依赖安装失败" -ForegroundColor Red
    exit 1
}

# 3) 验证
Write-Host "[3/3] 验证安装 ..." -ForegroundColor Cyan
$ok = & ".\.venv\Scripts\python.exe" -c "import flet, duckdb, cryptography, requests; print('flet={}, duckdb={}'.format(flet.__version__, duckdb.__version__))"
if ($LASTEXITCODE -eq 0) {
    Write-Host "  $ok"
    Write-Host ""
    Write-Host "✓ 环境就绪！" -ForegroundColor Green
    Write-Host "  桌面运行：  .\scripts\run_dev.ps1"
    Write-Host "  Web 运行：  .\scripts\run_web.ps1"
    Write-Host "  Web 发布：  .\scripts\publish_web.ps1"
    Write-Host "  EXE 打包：  .\scripts\build_desktop.ps1"
    Write-Host ""
} else {
    Write-Host "[ERROR] 验证失败" -ForegroundColor Red
    exit 1
}
