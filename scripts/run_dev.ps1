# ======================================================================
# KalshiDesk —— 开发运行（桌面模式）
# ======================================================================
# 使用方法：powershell -ExecutionPolicy Bypass -File .\scripts\run_dev.ps1
# ======================================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$VenvPython = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERROR] 未找到虚拟环境 .venv，请先运行 setup.ps1" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════╗"
Write-Host "║     KalshiDesk —— 桌面开发模式 (Flet Desktop)          ║"
Write-Host "╚══════════════════════════════════════════════════════╝"
Write-Host ""

& $VenvPython main.py
