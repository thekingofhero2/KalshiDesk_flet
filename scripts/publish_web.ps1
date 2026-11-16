# ======================================================================
# KalshiDesk —— 发布为静态网站（可托管到任意静态服务器 / Cloudflare Pages / Vercel）
# ======================================================================
# 使用方法：powershell -ExecutionPolicy Bypass -File .\scripts\publish_web.ps1
# 产出目录：.\dist\web\  （全部静态文件，index.html + assets + python 运行时）
# 部署：把 dist\web 整个目录丢到 Nginx / Caddy / Vercel / Cloudflare Pages 即可
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
Write-Host "║     KalshiDesk —— 发布 Web 静态站点                    ║"
Write-Host "╚══════════════════════════════════════════════════════╝"
Write-Host ""

# 清理旧产物
if (Test-Path ".\dist\web") {
    Remove-Item ".\dist\web" -Recurse -Force
}

# flet publish 输出到 dist\web
& $VenvFlet publish main.py `
    --distpath ".\dist\web" `
    --app-name "KalshiDesk" `
    --app-short-name "KalshiDesk" `
    --app-description "Kalshi 账户管理系统" `
    --base-url "/"

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] flet publish 失败" -ForegroundColor Red
    exit 1
}

$size = "{0:N1} MB" -f ((Get-ChildItem ".\dist\web" -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB)

Write-Host ""
Write-Host "✓ Web 发布完成！" -ForegroundColor Green
Write-Host "  产物目录：$PSScriptRoot\..\dist\web"
Write-Host "  总大小：   $size"
Write-Host ""
Write-Host "本地预览：  python -m http.server 8080 --directory dist\web"
Write-Host "Nginx 托管： 把 dist\web 放到 /var/www/html 即可"
Write-Host ""
