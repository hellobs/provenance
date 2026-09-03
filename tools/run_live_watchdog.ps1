# run_live_watchdog.ps1 — provenance 实时模拟服务看门狗(独立进程 + 自动恢复)
#
# 背景:live_fastapi.py 以后台 job 方式运行时,进程常被外部清理(无报错退出)。
# 本脚本以独立进程启动服务,并每 20 秒检查端口;若服务消失,自动 resume 重启。
# 模拟数据(checkpoint 每 2 分钟落盘)不受影响,resume 无缝续跑。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File tools\run_live_watchdog.ps1 [-Name stock-en8] [-Port 5001]
#
# 停止:关闭此脚本所在窗口,或
#   Stop-Process -Name python -Force   (谨慎:会杀所有 python)

param(
    [string]$Name = "stock-en8",
    [int]$Port = 5001,
    [int]$CheckSeconds = 20
)

$ErrorActionPreference = "Continue"
$provenance = Split-Path -Parent $PSScriptRoot   # D:\zzr\provenance
$pkg = Join-Path $provenance "provenance"
$python = Join-Path $pkg ".venv-live\Scripts\python.exe"
$script_ = Join-Path $pkg "live_fastapi.py"

function Test-Port {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $c)
}

function Start-Service {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 启动服务 (resume $Name @ $Port) ..."
    $env:PYTHONIOENCODING = "utf-8"
    # 独立隐藏进程:脱离当前会话,不随本脚本窗口/父进程被清理
    $logOut = Join-Path $provenance "logs\live_$Name.out.log"
    $logErr = Join-Path $provenance "logs\live_$Name.err.log"
    New-Item -ItemType Directory -Path (Split-Path $logOut) -Force | Out-Null
    $p = Start-Process -FilePath $python -ArgumentList @($script_, "--name", $Name, "--resume", "--step", "0", "--port", "$Port") `
        -WindowStyle Hidden -RedirectStandardOutput $logOut -RedirectStandardError $logErr -PassThru
    Write-Host "  进程 PID=$($p.Id),日志: $logOut"
    return $p
}

# 主循环
Write-Host "=== live watchdog: $Name @ $Port (每 ${CheckSeconds}s 检查) ==="
$proc = $null
while ($true) {
    if (-not (Test-Port)) {
        if ($proc -and -not $proc.HasExited) {
            # 端口没了但进程还在:进程僵死,杀之
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 端口 $Port 无监听但 PID $($proc.Id) 存活,终止后重启"
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
        $proc = Start-Service
    } elseif ($null -eq $proc -or $proc.HasExited) {
        # 端口有监听但本脚本不记得进程:更新引用
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($c) {
            try { $proc = Get-Process -Id $c.OwningProcess -ErrorAction Stop } catch { $proc = $null }
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 检测到已有服务 PID=$($c.OwningProcess)"
        }
    }
    Start-Sleep -Seconds $CheckSeconds
}
