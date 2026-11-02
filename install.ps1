# J.A.R.V.I.S. installer. Started by install.bat / install_xtts.bat, which are plain ASCII on purpose:
# cmd.exe misreads UTF-8 Russian text in .bat files (with chcp 65001 it loses its place in the file
# and runs fragments of lines as commands), so all logic and messages live here.
param([switch]$Xtts)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8 # read Python's UTF-8 output (paths may be Cyrillic)
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $PSScriptRoot
try { $Host.UI.RawUI.WindowTitle = if ($Xtts) { 'J.A.R.V.I.S. - голос XTTS' } else { 'J.A.R.V.I.S. - установка' } } catch {}
# The "downloaded from the internet" mark, the speech models and the shortcuts are handled by jarvis/prefetch.py:
# antivirus heuristics took this script for a downloader when it did those things itself (Kaspersky, 2026-11-02).

function Say($text, $color = 'Cyan') { Write-Host $text -ForegroundColor $color }
function Fail($text) {
    Write-Host ''
    Write-Host "[!] $text" -ForegroundColor Red
    Read-Host 'Нажмите Enter, чтобы закрыть'
    exit 1
}
function Step($text) { Write-Host ''; Write-Host ">> $text" -ForegroundColor Yellow }

function Get-PyExe {
    # 64-bit Python 3.10-3.13: every package (torch, onnxruntime, webrtcvad) has ready wheels for them
    $cands = @()
    foreach ($v in '3.12', '3.13', '3.11', '3.10') { $cands += , @('py', "-$v") }
    $cands += , @('python')
    $dirs = @("$env:LOCALAPPDATA\Programs\Python\Python31[0-3]\python.exe", "$env:ProgramFiles\Python31[0-3]\python.exe")
    foreach ($f in (Get-ChildItem $dirs -ErrorAction SilentlyContinue)) { $cands += , @($f.FullName) }
    # no double quotes inside: Windows PowerShell 5.1 mangles them in native command arguments
    $check = 'import sys; v = sys.version_info[:2]; print(sys.executable if (3, 10) <= v <= (3, 13) and sys.maxsize > 2**32 else str())'
    foreach ($c in $cands) {
        $exe = $c[0]
        $rest = @($c | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try { $out = & $exe @rest -c $check 2>$null } catch { continue }
        $out = "$out".Trim()
        # Microsoft Store Python is a packaged app: Windows gives it silence instead of the microphone
        if ($out -match 'WindowsApps') { $script:storePy = $true; continue }
        if ($out -and (Test-Path -LiteralPath $out)) { return $out }
    }
    return $null
}

function Install-Python {
    if ($script:storePy) {
        Step 'Найден только Python из Microsoft Store: ему Windows не даёт микрофон. Устанавливаю обычный Python 3.12 (около 25 МБ)...'
    } else {
        Step 'Python не найден — устанавливаю Python 3.12 (около 25 МБ)...'
    }
    # through winget only: a script that downloads an .exe into TEMP and runs it silently looks like a dropper to
    # antivirus heuristics. Without winget the python.org page is opened for a manual install.
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Host
        return Get-PyExe
    }
    return $null
}

function Pip($py, [string[]]$pkgs) {
    # Out-Host: inside a function native output would otherwise become part of the return value
    & $py -m pip install --disable-pip-version-check @pkgs | Out-Host
    return $LASTEXITCODE -eq 0
}

Say '========================================='
Say ('   J.A.R.V.I.S.  -  ' + $(if ($Xtts) { 'установка голоса XTTS' } else { 'установка' }))
Say '========================================='

$py = Get-PyExe
if (-not $py) { $py = Install-Python }
if (-not $py) {
    Start-Process 'https://www.python.org/downloads/release/python-31210/'
    Fail 'Не удалось установить Python автоматически. Установите Python 3.12 вручную (галочка "Add Python to PATH") и запустите install.bat снова.'
}
Say "Python: $py" 'Green'
$hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)

if ($Xtts) {
    Say 'Нейросетевой голос XTTS v2: нужно около 6 ГБ места. С видеокартой NVIDIA говорит быстро, без неё медленно.'
    Say 'Лицензия модели XTTS: Coqui Public Model License (только некоммерческое использование).'
    $xp = Join-Path $PSScriptRoot '.xtts-venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $xp) {
        & $xp -c 'import TTS' 2>$null
        if ($LASTEXITCODE -eq 0) { Say 'XTTS уже установлен.' 'Green'; Read-Host 'Нажмите Enter, чтобы закрыть'; exit 0 }
        Say 'Пересоздаю повреждённое окружение...'
        Remove-Item -LiteralPath (Join-Path $PSScriptRoot '.xtts-venv') -Recurse -Force
    }
    Step 'Создаю окружение .xtts-venv'
    & $py -m venv .xtts-venv
    if ($LASTEXITCODE -ne 0) { Fail 'Не удалось создать окружение .xtts-venv' }
    & $xp -m pip install --upgrade pip --disable-pip-version-check
    $index = if ($hasNvidia) { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }
    Step 'Ставлю PyTorch'
    if (-not (Pip $xp @('torch', 'torchaudio', '--index-url', $index))) { Fail 'Ошибка установки PyTorch' }
    Step 'Ставлю Coqui TTS'
    if (-not (Pip $xp @('coqui-tts', 'transformers>=4.57,<5', 'numpy'))) { Fail 'Ошибка установки Coqui TTS' }
    Step 'Скачиваю модель XTTS v2 (около 1.8 ГБ)...'
    $env:COQUI_TOS_AGREED = '1'
    & $xp -c "from TTS.api import TTS; TTS('tts_models/multilingual/multi-dataset/xtts_v2')"
    Say ''
    Say 'Готово. В Джарвисе: Настройки - Голос - Движок: XTTS.' 'Green'
    Read-Host 'Нажмите Enter, чтобы закрыть'
    exit 0
}

$vp = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$cfgFile = Join-Path $PSScriptRoot '.venv\pyvenv.cfg'
if ((Test-Path -LiteralPath $cfgFile) -and ((Get-Content -LiteralPath $cfgFile -Raw) -match 'WindowsApps')) {
    Step 'Старое окружение сделано на Python из Microsoft Store (без доступа к микрофону) — пересоздаю'
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot '.venv') -Recurse -Force
}
if (-not (Test-Path -LiteralPath $vp)) {
    Step 'Создаю окружение .venv'
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail 'Не удалось создать окружение .venv' }
}
& $vp -m pip install --upgrade pip --disable-pip-version-check

Step 'Ставлю пакеты Джарвиса (несколько минут)...'
if (-not (Pip $vp @('-r', 'requirements.txt'))) {
    Fail 'Ошибка установки пакетов. Проверьте интернет и запустите install.bat ещё раз.'
}

& $vp -c 'import torch' 2>$null
if ($LASTEXITCODE -ne 0) {
    Step 'Ставлю движок голоса Silero (PyTorch для процессора, около 200 МБ)...'
    if (-not (Pip $vp @('torch', '--index-url', 'https://download.pytorch.org/whl/cpu'))) { Fail 'Ошибка установки PyTorch' }
}

# GigaAM (a weak laptop gets the light int8 model), Silero, the shortcuts: jarvis/prefetch.py
Step 'Скачиваю модели распознавания и голоса (0,4–1 ГБ, на слабом ПК меньше) и создаю ярлык...'
& $vp -m jarvis.prefetch

if ($hasNvidia) {
    Step 'Найдена видеокарта NVIDIA: ставлю CUDA-библиотеки для распознавания Whisper...'
    Pip $vp @('nvidia-cublas-cu12', 'nvidia-cudnn-cu12>=9,<10') | Out-Null
}

Say ''
Say '  Готово! Запуск: ярлык J.A.R.V.I.S. на рабочем столе или Jarvis.bat' 'Green'
Say '  Нейросетевой голос XTTS (нужна NVIDIA): install_xtts.bat' 'Green'
Say ''
$a = Read-Host 'Запустить Джарвиса сейчас? (Enter - да, n - нет)'
if ($a -notmatch '^[nNнН]') { Start-Process -FilePath (Join-Path $PSScriptRoot 'Jarvis.bat') -WorkingDirectory $PSScriptRoot }
exit 0
