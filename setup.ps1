param(
    [string]$Python = 'python',
    [string]$PipIndexUrl = 'https://mirrors.aliyun.com/pypi/simple',
    [string]$TorchIndexUrl = '',
    [string]$TorchWheel = '',
    [string]$TorchVersion = '',
    [string]$QwenVersion = '0.0.6'
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path 'config.json')) { Copy-Item 'config.example.json' 'config.json' }
if (-not (Test-Path 'workbuddy\desktop-profile.json')) {
    Copy-Item 'workbuddy\desktop-profile.example.json' 'workbuddy\desktop-profile.json'
}
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Exe (exit $LASTEXITCODE)" }
}
Invoke-Checked $Python @('-c', 'import sys; assert (3,11) <= sys.version_info[:2] <= (3,12), "Use Python 3.11 or 3.12"')
if (-not (Test-Path '.venv\Scripts\python.exe')) { Invoke-Checked $Python @('-m','venv','.venv') }
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $venvPython -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec(''torch'') else 1)'
$hasTorch = $LASTEXITCODE -eq 0
if (-not $hasTorch) {
    if ($TorchWheel) {
        Invoke-Checked $venvPython @('-m','pip','install',$TorchWheel,'-i',$PipIndexUrl)
    } elseif ($TorchIndexUrl) {
        $torchSpec = if ($TorchVersion) { "torch==$TorchVersion" } else { 'torch' }
        Invoke-Checked $venvPython @('-m','pip','install',$torchSpec,'--index-url',$TorchIndexUrl)
    } else {
        if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { & nvidia-smi }
        throw 'Select a compatible PyTorch wheel at https://pytorch.org/get-started/locally/ then rerun with -TorchIndexUrl URL or -TorchWheel PATH. CUDA versions are not fixed by this application.'
    }
}
Invoke-Checked $venvPython @('-m','pip','install','-r','requirements-runtime.txt','-i',$PipIndexUrl)
Invoke-Checked $venvPython @('-m','pip','install','-r','requirements-workbuddy.txt','-i',$PipIndexUrl)
# ASR-only runtime: skip SDK web demos and its incompatible legacy nagisa pin on Windows.
Invoke-Checked $venvPython @('-m','pip','install',"qwen-asr==$QwenVersion",'--no-deps','-i',$PipIndexUrl)
Invoke-Checked $venvPython @('-c','import torch; from qwen_asr import Qwen3ASRModel; from opencc import OpenCC')
Invoke-Checked $venvPython @('scripts\doctor.py')
Write-Host 'Setup complete. Download the model, then double-click start.cmd.'
