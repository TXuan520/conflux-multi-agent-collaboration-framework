param(
    [Parameter(Mandatory = $true)]
    [string]$DestinationRoot
)
$ErrorActionPreference = 'Stop'
$releaseScripts = Join-Path ([System.IO.Path]::GetFullPath($DestinationRoot)) 'scripts'
if (-not (Test-Path -LiteralPath $releaseScripts -PathType Container)) {
    throw 'DestinationRoot must contain an existing scripts directory.'
}
function Get-Sha256($p) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $fs = [System.IO.File]::OpenRead($p)
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($fs)) -replace '-', '').ToUpper()
    } finally {
        $fs.Close()
        $sha.Dispose()
    }
}
# Only the bootstrap/thread change set; no credentials or runtime data.
$names = @('routing_config.py', 'agent_loop.py', 'memory_summary.py',
           'codex_appserver.py', 'hub.py', 'ag_reply_guard.py', 'test-ag-reply-guard.py', 'test_bootstrap_rules.py', 'test-broadcast-mode.py', 'broadcast_policy.py', 'test-broadcast-policy.py', 'test-recipient-batching.py', 'test-broadcast-delivery-contract.py', 'provider-dispatch-test.py', 'client-probe-test.py', 'sync-release.ps1', 'workbuddy_bridge.py', 'provider-registration-test.py', 'launch-conflux.ps1', 'release-gate.py')
foreach ($name in $names) {
    $sourceFile = Join-Path $PSScriptRoot $name
    $releaseFile = Join-Path $releaseScripts $name
    Copy-Item -LiteralPath $sourceFile -Destination $releaseFile -Force
    if ((Get-Sha256 $sourceFile) -ne (Get-Sha256 $releaseFile)) {
        throw "Copy verification failed: $name"
    }
    Write-Output "SYNC OK $name"
}
