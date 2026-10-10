$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Fixtures = [IO.Path]::GetFullPath((Join-Path $Root '../android-source/android/app/src/androidTest/assets'))
$Output = Join-Path $Root 'artifacts/windows-runtime'
New-Item -ItemType Directory -Force $Output | Out-Null
$Exe = Join-Path $Root 'dist/Shikuang/Shikuang.exe'
$Adapters = @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' })
if ($Adapters.Count -eq 0) { throw 'No initially active network adapter; cannot prove its disable operation' }
try {
    $Adapters | Disable-NetAdapter -Confirm:$false
    Start-Sleep -Seconds 2
    $Routes = @(Get-NetRoute -ErrorAction SilentlyContinue | Where-Object { $_.DestinationPrefix -in @('0.0.0.0/0','::/0') })
    $Up = @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' })
    if ($Routes.Count -ne 0 -or $Up.Count -ne 0) { throw 'External route or active network adapter remains; offline gate failed' }
    [ordered]@{ disabled_adapter_names=@($Adapters.Name); active_adapter_count=$Up.Count; default_route_count=$Routes.Count; physical_network_disabled=$true; platform=[Environment]::OSVersion.VersionString } | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $Output 'network-proof.json')
    foreach ($Phase in @('write','restart')) {
        $Report = Join-Path $Output ($Phase + '.json')
        $Arguments = @('--smoke-report', ('"'+$Report+'"'), '--smoke-fixtures', ('"'+$Fixtures+'"'), '--smoke-phase', $Phase)
        $Process = Start-Process -FilePath $Exe -ArgumentList $Arguments -PassThru
        if (-not $Process.WaitForExit(330000)) {
            $Process.Kill()
            throw ('Packaged desktop ' + $Phase + ' timed out')
        }
        if ($Process.ExitCode -ne 0 -or -not (Test-Path $Report)) { throw ('Packaged desktop ' + $Phase + ' did not finish successfully') }
        $Result = Get-Content -Raw -Encoding UTF8 $Report | ConvertFrom-Json
        if ($Result.passed -ne $true) { throw ('Packaged desktop ' + $Phase + ' failed: ' + $Result.failure) }
        # Recheck the real host network after models/reference reads, not only before.
        if (@(Get-NetAdapter | Where-Object {$_.Status -eq 'Up'}).Count -ne 0 -or @(Get-NetRoute -ErrorAction SilentlyContinue | Where-Object {$_.DestinationPrefix -in @('0.0.0.0/0','::/0')}).Count -ne 0) { throw 'Network returned during packaged smoke' }
        $Result.physicalNetworkDisabled = $true
        $Result | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 $Report
        # The window must close and its owned listening socket must disappear.
        if (@(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue).Count -ne 0) { throw 'Desktop left its port listening after close' }
    }
} finally {
    $Adapters | Enable-NetAdapter -Confirm:$false
}

