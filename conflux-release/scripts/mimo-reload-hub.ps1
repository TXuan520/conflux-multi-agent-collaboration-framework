# mimo-reload-hub.ps1 — reload hub.py so backend changes take effect.
# ASCII-only on purpose: PS 5.1 reads BOM-less .ps1 as ANSI, Chinese breaks parsing.
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root 'data\hub\reload.log'
function Say($m) { Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0} {1}' -f ([DateTime]::Now.ToString('s')), $m) }

Say 'mimo-reload calling POST /api/admin/prepare-reload...'
$prep = $null
try {
  $prep = Invoke-RestMethod 'http://127.0.0.1:8777/api/admin/prepare-reload' -Method Post -TimeoutSec 30
  Say ('mimo-reload prepare-reload result: ok=' + $prep.ok + ' ready=' + $prep.ready_to_exit + ' active=' + $prep.active_turns)
} catch {
  Say ('mimo-reload prepare-reload request failed: ' + $_.Exception.Message)
}

if (-not $prep -or -not $prep.ready_to_exit) {
  Say 'mimo-reload ABORT: system not ready to exit (still busy or request failed), calling cancel-reload...'
  try {
    Invoke-RestMethod 'http://127.0.0.1:8777/api/admin/cancel-reload' -Method Post -TimeoutSec 5
    Say 'mimo-reload cancel-reload sent successfully, system draining reset to false'
  } catch {
    Say ('mimo-reload cancel-reload failed: ' + $_.Exception.Message)
  }
  Say 'mimo-reload SAFE ABORT: listener NOT stopped, exiting 1'
  exit 1
}

$owner = (Get-NetTCPConnection -LocalPort 8777 -State Listen | Select-Object -First 1).OwningProcess
if ($owner) {
  Stop-Process -Id $owner -Force
  Say "mimo-reload stop listener pid=$owner"
} else {
  Say 'mimo-reload no listener on 8777'
}
for ($i = 0; $i -lt 30; $i++) {
  if (-not (Get-NetTCPConnection -LocalPort 8777 -State Listen)) { break }
  Start-Sleep -Milliseconds 300
}

$py = Join-Path $root 'stt\venv\Scripts\pythonw.exe'
if (-not (Test-Path $py)) { $py = 'pythonw.exe' }
Start-Process -FilePath $py -ArgumentList (Join-Path $root 'scripts\hub.py') `
  -WorkingDirectory (Join-Path $root 'scripts') -WindowStyle Hidden

$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $r = Invoke-RestMethod 'http://127.0.0.1:8777/api/status' -TimeoutSec 3
    Say ('mimo-reload ok pid=' + $r.heartbeat.pid)
    $ok = $true
    break
  } catch {}
}
if (-not $ok) { Say 'mimo-reload FAIL: 8777 no response' }
if ($ok) { exit 0 } else { exit 1 }
