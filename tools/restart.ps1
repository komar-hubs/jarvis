# Restart Jarvis after code changes: ask the running copy to quit (same as tray -> Exit), wait, start Jarvis.bat.
# Started through WMI so the new Jarvis runs outside the Claude Desktop package (real AppData, real registry).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content (Join-Path $root "data\config.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$port = if ($cfg.port) { $cfg.port } else { 8770 }

function Get-JarvisPids {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -match 'run\.py' -and ($_.ExecutablePath -like "$root*" -or $_.CommandLine -like "*$root*" -or $_.CommandLine -match '^"?\.venv') } |
        Select-Object -ExpandProperty ProcessId
}

$before = @(Get-JarvisPids)
if ($before.Count) {
    try {
        $body = '{"method":"tray_action","params":{"action":"quit"}}'
        Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/rpc" -Method Post -Body $body -ContentType "application/json" `
            -Headers @{ "X-Jarvis-Token" = $cfg.token } -TimeoutSec 5 | Out-Null
        Write-Output "quit requested (pids $($before -join ', '))"
    } catch {
        Write-Output "quit request failed: $($_.Exception.Message)"
    }
    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline -and @(Get-Process -Id $before -ErrorAction SilentlyContinue).Count) { Start-Sleep -Milliseconds 500 }
    $left = @(Get-Process -Id $before -ErrorAction SilentlyContinue)
    if ($left.Count) { Write-Output "still running after 25 s: $($left.Id -join ', ') - not killed"; exit 1 }
    Write-Output "old Jarvis exited"
}

$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = "cmd.exe /c `"$(Join-Path $root 'Jarvis.bat')`""; CurrentDirectory = $root }
Write-Output "started Jarvis.bat (ReturnValue $($r.ReturnValue))"
