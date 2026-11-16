# ======================================================================
# KalshiDesk —— 开发运行（Web 模式，浏览器打开）
# ======================================================================
# 使用方法：powershell -ExecutionPolicy Bypass -File .\scripts\run_web.ps1
# 或直接：.\scripts\run_web.ps1
# ======================================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$VenvFlet = ".\.venv\Scripts\flet.exe"

if (-not (Test-Path $VenvFlet)) {
    Write-Host "[ERROR] 未找到 flet CLI，请先运行 setup.ps1" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════╗"
Write-Host "║     KalshiDesk —— Web 开发模式 (Flet Web)              ║"
Write-Host "║     浏览器将自动打开 http://localhost:8000            ║"
Write-Host "╚══════════════════════════════════════════════════════╝"
Write-Host ""

& $VenvFlet run --web main.py
