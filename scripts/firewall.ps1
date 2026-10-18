# Run in an elevated PowerShell on the target computer after checking config.json.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content -LiteralPath (Join-Path $root 'config.json') -Raw | ConvertFrom-Json
$nodeName = $cfg.runtime.node
if ($nodeName.Contains('/') -or $nodeName.Contains('\')) { $nodeName = Join-Path $root $nodeName }
$node = (Get-Command $nodeName -ErrorAction Stop).Source
$name = 'AIPrinter-HT802-UDP'
if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
    throw "Rule $name already exists. Inspect it before changing network settings."
}
New-NetFirewallRule -Name $name -DisplayName 'AI Printer HT802 SIP and RTP' -Direction Inbound -Action Allow -Protocol UDP -LocalPort @($cfg.phone.sip_port,$cfg.phone.rtp_port) -LocalAddress $cfg.phone.local_address -RemoteAddress $cfg.phone.device_address -Program $node -Profile Any
