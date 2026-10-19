$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Fixtures = [IO.Path]::GetFullPath((Join-Path $Root '../android-source/android/app/src/androidTest/assets'))
$Output = Join-Path $Root 'artifacts/windows-runtime'
New-Item -ItemType Directory -Force $Output | Out-Null
$Exe = [IO.Path]::GetFullPath((Join-Path $Root 'dist/Shikuang/Shikuang.exe'))
$RunId = [guid]::NewGuid().ToString('D')
$ReceiptPath = Join-Path $Output 'windows-smoke-receipt.json'
$NetworkPath = Join-Path $Output 'network-proof.json'
$Receipt = [ordered]@{
    schema = 'shikuang-windows-smoke-receipt-v1'; run_id = $RunId
    started_at_utc = [datetimeoffset]::UtcNow.ToString('o'); passed = $false
    network_evidence_scope = 'before-and-after-phase-host-snapshots-not-continuous-monitoring'
    phases = @(); restoration = [ordered]@{ attempted = $false; passed = $false }
}
$Adapters = @()
$Process = $null
$ValidatedHost = $null
$PhaseReceipt = $null
$Failure = $null
$WriteSnapshot = $null
$WriteReportPath = Join-Path $Output 'write.json'
$LaunchIds = @()
$NetworkProof = [ordered]@{
    schema = 'shikuang-windows-offline-snapshots-v1'; run_id = $RunId; passed = $false
    physical_network_disabled = $false; disabled_adapter_names = @()
    active_adapter_count = $null; default_route_count = $null; phases = @()
    platform = [Environment]::OSVersion.VersionString; evidence_scope = $Receipt.network_evidence_scope
}

function Write-Json($Path, $Document) {
    $Temporary = $Path + '.' + $RunId + '.tmp'
    $Document | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $Temporary -Encoding utf8
    Move-Item -LiteralPath $Temporary -Destination $Path -Force
}

function Read-Json($Path) {
    $Raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    # Newer PowerShell can deserialize ISO dates automatically. Keep the exact
    # Python identity strings on both current and older CI PowerShell versions.
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
        $Raw | ConvertFrom-Json -Depth 40 -DateKind String
    } else {
        $Raw | ConvertFrom-Json -Depth 40
    }
}

function Require($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Get-NetworkSnapshot {
    # Query failures must never become empty, apparently offline arrays.
    $CurrentAdapters = @(Get-NetAdapter -ErrorAction Stop)
    $DefaultRoutes = @(Get-NetRoute -ErrorAction Stop | Where-Object { $_.DestinationPrefix -in @('0.0.0.0/0', '::/0') })
    $Active = @($CurrentAdapters | Where-Object { $_.Status -eq 'Up' })
    [ordered]@{
        sampled_at_utc = [datetimeoffset]::UtcNow.ToString('o'); query_succeeded = $true
        active_adapter_count = $Active.Count; default_route_count = $DefaultRoutes.Count
        active_adapters = @($Active | Select-Object Name, InterfaceGuid, ifIndex, Status)
        default_routes = @($DefaultRoutes | Select-Object InterfaceIndex, DestinationPrefix)
    }
}

function Require-Offline($Snapshot) {
    Require ($Snapshot.query_succeeded -eq $true -and $Snapshot.active_adapter_count -eq 0 -and $Snapshot.default_route_count -eq 0) 'External route or active network adapter remains; offline gate failed'
}

function Get-PortSnapshot {
    # Enumerate first: a filtered CIM query can throw when no matching socket exists.
    $Listeners = @(Get-NetTCPConnection -ErrorAction Stop | Where-Object { $_.State -eq 'Listen' -and $_.LocalPort -eq 8000 })
    [ordered]@{
        sampled_at_utc = [datetimeoffset]::UtcNow.ToString('o'); query_succeeded = $true
        listeners = @($Listeners | Select-Object LocalAddress, LocalPort, OwningProcess)
    }
}

function Read-Utc($Value, [string]$Label) {
    Require ($Value -is [string] -and $Value.Length -gt 0) ($Label + ' is missing')
    $Parsed = [datetimeoffset]::Parse($Value, [Globalization.CultureInfo]::InvariantCulture)
    Require ($Parsed.Offset -eq [timespan]::Zero) ($Label + ' is not UTC')
    $Parsed
}

function Require-HostIdentity($Document, [string]$Phase, [string]$Schema) {
    Require ($Document.schema -ceq $Schema -and $Document.run_id -ceq $RunId -and $Document.phase -ceq $Phase) 'Stale or mismatched host lifecycle receipt'
    Require ($Document.launch_id -is [string] -and $Document.launch_id -cmatch '^[a-f0-9]{32,128}$') 'Missing per-host launch identity'
    Require (($Document.pid -is [long] -or $Document.pid -is [int]) -and $Document.pid -gt 0 -and
        ($Document.parent_pid -is [long] -or $Document.parent_pid -is [int]) -and $Document.parent_pid -gt 0) 'Invalid real host PID metadata'
    Require ($Document.origin -ceq 'http://127.0.0.1:8000' -and $Document.executable -is [string] -and
        [string]::Equals([IO.Path]::GetFullPath($Document.executable), $Exe, [StringComparison]::OrdinalIgnoreCase)) 'Unexpected packaged host executable or origin'
}

function Require-SameIdentity($Actual, $Expected) {
    foreach ($Field in @('run_id', 'phase', 'launch_id', 'pid', 'parent_pid', 'executable', 'python_entry_utc', 'server_started_utc', 'origin')) {
        Require ($null -ne $Actual.$Field -and [string]$Actual.$Field -ceq [string]$Expected.$Field) ('Host identity changed: ' + $Field)
    }
}

function Require-RuntimeReport($Result, [string]$Phase, $Started, $Identity) {
    Require ($Result.passed -is [bool] -and $Result.passed -eq $true -and $Result.errors -is [array] -and $Result.errors.Count -eq 0) 'Runtime failed or lacks error evidence'
    Require ($Result.run_id -ceq $RunId -and $Result.phase -ceq $Phase) 'Runtime report belongs to another run or phase'
    Require-SameIdentity $Result.host $Started
    Require ($Result.app_version -ceq $Identity.app_version -and $Result.web_commit -ceq $Identity.web_commit -and
        $Result.model_manifest_sha256 -ceq $Identity.model_manifest_sha256) 'Runtime package/source identity mismatch'
    Require ($Result.online -is [bool] -and $Result.online -eq $false -and $Result.secureContext -is [bool] -and
        $Result.secureContext -eq $true -and $Result.accuracy_claim -is [bool] -and $Result.accuracy_claim -eq $false -and
        $Result.origin -ceq 'http://127.0.0.1:8000') 'Offline/origin/accuracy boundary failed'
    $Names = @()
    Require ($Result.checks -is [array] -and $Result.checks.Count -gt 0) 'Missing runtime checks'
    foreach ($Check in $Result.checks) {
        Require ($Check.name -is [string] -and $Check.name.Length -gt 0 -and $Names -cnotcontains $Check.name -and
            $Check.passed -is [bool] -and $Check.passed -eq $true) 'Missing, duplicate or failed runtime check'
        $Names += $Check.name
    }
    $Required = @('original-stable-origin', 'secure-local-context', 'manifest-bound-models', 'cancel-active-worker',
        'real-quartz-recognition', 'real-dog-rejection', 'complete-local-reference-library', 'full-117-photo-manifest',
        'quartz-three-native-gallery-decodes', 'gallery-does-not-write-specimens', 'backup-retains-model-identity',
        'existing-records-retained', 'bundled-resources-only')
    if ($Phase -ceq 'restart') { $Required += @('history-survives-restart', 'smoke-record-cleanup') }
    foreach ($Name in $Required) { Require ($Names -ccontains $Name) ('Missing real runtime gate: ' + $Name) }
    $Persistence = $Result.persistence
    $Snapshot = $Persistence.snapshot
    Require ($Snapshot.schema -ceq 'shikuang-persistence-snapshot-v1' -and $Snapshot.record_id -ceq 'desktop-runtime-smoke-record' -and
        ($Snapshot.photo_count -is [int] -or $Snapshot.photo_count -is [long]) -and $Snapshot.photo_count -eq 1 -and
        $Snapshot.search_fixture_query -is [string] -and $Snapshot.search_fixture_query.Length -gt 0) 'Missing saved record/photo/search content contract'
    foreach ($Field in @('record_sha256', 'photos_sha256', 'search_sha256', 'search_raw_sha256')) {
        Require ($Snapshot.$Field -is [string] -and $Snapshot.$Field -cmatch '^[a-f0-9]{64}$') ('Invalid persistence digest: ' + $Field)
    }
    if ($Phase -ceq 'write') {
        Require ($Persistence.mode -ceq 'write-content-snapshot') 'Missing full write persistence snapshot'
    } else {
        Require ($Persistence.mode -ceq 'full-content-sha256-before-any-put' -and $Persistence.verified_before_any_put -is [bool] -and
            $Persistence.verified_before_any_put -eq $true -and $null -ne $WriteSnapshot) 'Restart did not verify content before writing'
        foreach ($Field in @('schema', 'record_id', 'record_sha256', 'photos_sha256', 'photo_count', 'search_sha256', 'search_raw_sha256', 'search_fixture_query')) {
            Require ([string]$Snapshot.$Field -ceq [string]$WriteSnapshot.$Field) ('Restart persistence content mismatch: ' + $Field)
        }
        $Expected = $Persistence.expected_write_report
        Require ($Expected.path -is [string] -and [string]::Equals([IO.Path]::GetFullPath($Expected.path), $WriteReportPath, [StringComparison]::OrdinalIgnoreCase) -and
            ($Expected.bytes -is [int] -or $Expected.bytes -is [long]) -and $Expected.bytes -eq (Get-Item -LiteralPath $WriteReportPath).Length -and
            $Expected.sha256 -is [string] -and $Expected.sha256 -ceq (Get-FileHash -LiteralPath $WriteReportPath -Algorithm SHA256).Hash.ToLowerInvariant()) 'Restart did not read this exact write report'
    }
}

Write-Json $ReceiptPath $Receipt
try {
    Require (Test-Path -LiteralPath $Exe -PathType Leaf) 'Packaged executable is missing'
    $Receipt.executable = $Exe
    $Receipt.executable_sha256 = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
    $Identity = Read-Json (Join-Path $Root 'web-source.json')
    # A stale report cannot satisfy a fresh invocation, even if the process exits 0.
    foreach ($Phase in @('write', 'restart')) {
        foreach ($Suffix in @('.json', '.json.host-start.json', '.json.host-stop.json')) {
            $Old = Join-Path $Output ($Phase + $Suffix)
            if (Test-Path -LiteralPath $Old) { Remove-Item -LiteralPath $Old -Force }
        }
    }
    if (Test-Path -LiteralPath $NetworkPath) { Remove-Item -LiteralPath $NetworkPath -Force }
    $Adapters = @(Get-NetAdapter -ErrorAction Stop | Where-Object { $_.Status -eq 'Up' })
    Require ($Adapters.Count -gt 0) 'No initially active network adapter; cannot prove its disable operation'
    $Receipt.initial_adapters = @($Adapters | Select-Object Name, InterfaceGuid, ifIndex, Status)
    $NetworkProof.disabled_adapter_names = @($Adapters.Name)
    $Adapters | Disable-NetAdapter -Confirm:$false -ErrorAction Stop
    Start-Sleep -Seconds 2
    $NetworkProof.initial_disabled_snapshot = Get-NetworkSnapshot
    Write-Json $NetworkPath $NetworkProof
    Require-Offline $NetworkProof.initial_disabled_snapshot
    $NetworkProof.physical_network_disabled = $true
    $NetworkProof.active_adapter_count = $NetworkProof.initial_disabled_snapshot.active_adapter_count
    $NetworkProof.default_route_count = $NetworkProof.initial_disabled_snapshot.default_route_count
    foreach ($Phase in @('write', 'restart')) {
        $Process = $null
        $ValidatedHost = $null
        $PhaseReceipt = [ordered]@{ phase = $Phase; passed = $false; timed_out = $false }
        $Receipt.phases += $PhaseReceipt
        $PhaseReceipt.network_before = Get-NetworkSnapshot
        Require-Offline $PhaseReceipt.network_before
        $PhaseReceipt.port_before = Get-PortSnapshot
        Require ($PhaseReceipt.port_before.listeners.Count -eq 0) 'Port 8000 is already owned before launch'
        Require ((Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $Receipt.executable_sha256) 'Packaged executable changed between phases'
        $Report = Join-Path $Output ($Phase + '.json')
        $StartPath = $Report + '.host-start.json'
        $StopPath = $Report + '.host-stop.json'
        $Arguments = @('--smoke-report', ('"'+$Report+'"'), '--smoke-fixtures', ('"'+$Fixtures+'"'), '--smoke-phase', $Phase, '--smoke-run-id', $RunId)
        if ($Phase -ceq 'restart') { $Arguments += @('--smoke-write-report', ('"'+$WriteReportPath+'"')) }
        $LaunchBefore = [datetimeoffset]::UtcNow
        $Clock = [Diagnostics.Stopwatch]::StartNew()
        $Process = Start-Process -FilePath $Exe -ArgumentList $Arguments -PassThru -ErrorAction Stop
        # Retain the real process handle before waiting; reject absent exit evidence.
        $null = $Process.Handle
        $PhaseReceipt.launcher = [ordered]@{ pid = $Process.Id; requested_at_utc = $LaunchBefore.ToString('o'); start_at_utc = $Process.StartTime.ToUniversalTime().ToString('o') }
        Write-Json $ReceiptPath $Receipt
        while (-not (Test-Path -LiteralPath $StartPath -PathType Leaf)) {
            Require (-not $Process.HasExited) 'Launcher exited before its host-start receipt'
            if ($Clock.ElapsedMilliseconds -ge 60000) {
                $PhaseReceipt.timed_out = $true
                throw 'Host-start receipt exceeded 60 seconds'
            }
            Start-Sleep -Milliseconds 100
        }
        $Started = Read-Json $StartPath
        Require-HostIdentity $Started $Phase 'shikuang-smoke-host-start-v1'
        Require ($LaunchIds -cnotcontains $Started.launch_id) 'Restart reused the write host launch identity'
        $LaunchIds += $Started.launch_id
        $EntryUtc = Read-Utc $Started.python_entry_utc 'Python entry time'
        $ServerUtc = Read-Utc $Started.server_started_utc 'Server start time'
        Require ($EntryUtc -ge $LaunchBefore.AddSeconds(-2) -and $ServerUtc -ge $EntryUtc -and $ServerUtc -le [datetimeoffset]::UtcNow.AddSeconds(2)) 'Stale or invalid host-start timestamps'
        $HostProcess = Get-Process -Id $Started.pid -ErrorAction Stop
        $HostCim = Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = ' + $Started.pid) -ErrorAction Stop
        Require ($null -ne $HostCim -and $HostCim.ParentProcessId -eq $Started.parent_pid) 'Host parent PID does not match the live process'
        Require ($Started.pid -eq $Process.Id -or ($Started.parent_pid -eq $Process.Id -and $HostCim.ParentProcessId -eq $Process.Id)) 'Host is neither the launcher nor its direct child'
        Require ([string]::Equals([IO.Path]::GetFullPath($HostProcess.Path), $Exe, [StringComparison]::OrdinalIgnoreCase) -and
            $HostProcess.StartTime.ToUniversalTime() -ge $LaunchBefore.UtcDateTime.AddSeconds(-2)) 'Live host executable/start time mismatch'
        $ValidatedHost = $HostProcess
        $null = $ValidatedHost.Handle
        $PhaseReceipt.host_start = $Started
        $PhaseReceipt.host_relation = if ($Started.pid -eq $Process.Id) { 'launcher-is-python-host' } else { 'direct-child-python-host' }
        $PhaseReceipt.port_during = Get-PortSnapshot
        Require ($PhaseReceipt.port_during.listeners.Count -eq 1 -and $PhaseReceipt.port_during.listeners[0].LocalAddress -ceq '127.0.0.1' -and
            $PhaseReceipt.port_during.listeners[0].OwningProcess -eq $Started.pid) 'Live host does not exclusively own loopback port 8000'
        Write-Json $ReceiptPath $Receipt
        $Remaining = 330000 - $Clock.ElapsedMilliseconds
        if ($Remaining -le 0 -or -not $Process.WaitForExit([int]$Remaining)) {
            $PhaseReceipt.timed_out = $true
            throw ('Packaged desktop ' + $Phase + ' exceeded 330 seconds')
        }
        $Process.WaitForExit()
        $Process.Refresh()
        $PhaseReceipt.launcher.exit_code = $Process.ExitCode
        $PhaseReceipt.launcher.exit_observed_at_utc = [datetimeoffset]::UtcNow.ToString('o')
        Require ($null -ne $Process.ExitCode -and $Process.ExitCode -eq 0) 'Packaged launcher did not exit successfully'
        Require ($ValidatedHost.WaitForExit(5000)) 'Python host remained alive after launcher exit'
        $ValidatedHost.Refresh()
        $PhaseReceipt.host_exit_code = $ValidatedHost.ExitCode
        Require ($null -ne $ValidatedHost.ExitCode -and $ValidatedHost.ExitCode -eq 0) 'Python host did not exit successfully'
        Require ((Test-Path -LiteralPath $Report -PathType Leaf) -and (Test-Path -LiteralPath $StopPath -PathType Leaf)) 'Missing runtime or host-stop report'
        $Stopped = Read-Json $StopPath
        Require-HostIdentity $Stopped $Phase 'shikuang-smoke-host-stop-v1'
        Require-SameIdentity $Stopped $Started
        $ShutdownUtc = Read-Utc $Stopped.shutdown_at_utc 'Host shutdown time'
        Require ($ShutdownUtc -ge $ServerUtc -and $ShutdownUtc -le [datetimeoffset]::UtcNow.AddSeconds(2) -and
            $Stopped.server_stop_completed -is [bool] -and $Stopped.server_stop_completed -eq $true -and
            $Stopped.instance_lock_closed -is [bool] -and $Stopped.instance_lock_closed -eq $true -and
            ($Stopped.exit_code -is [int] -or $Stopped.exit_code -is [long]) -and $Stopped.exit_code -eq 0) 'Host cleanup or exit receipt failed'
        $PhaseReceipt.host_stop = $Stopped
        $PhaseReceipt.port_after = Get-PortSnapshot
        Require ($PhaseReceipt.port_after.listeners.Count -eq 0) 'Desktop left port 8000 listening after close'
        $PhaseReceipt.network_after = Get-NetworkSnapshot
        Require-Offline $PhaseReceipt.network_after
        $Result = Read-Json $Report
        Require-RuntimeReport $Result $Phase $Started $Identity
        if ($Phase -ceq 'write') { $WriteSnapshot = $Result.persistence.snapshot }
        $PhaseReceipt.python_report_sha256 = (Get-FileHash -LiteralPath $Report -Algorithm SHA256).Hash.ToLowerInvariant()
        # Established field, now backed by this run's actual host observations.
        $Result.physicalNetworkDisabled = $true
        Write-Json $Report $Result
        $PhaseReceipt.report_sha256 = (Get-FileHash -LiteralPath $Report -Algorithm SHA256).Hash.ToLowerInvariant()
        $PhaseReceipt.host_start_sha256 = (Get-FileHash -LiteralPath $StartPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $PhaseReceipt.host_stop_sha256 = (Get-FileHash -LiteralPath $StopPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $PhaseReceipt.persistence_mode = $Result.persistence.mode
        $PhaseReceipt.passed = $true
        $NetworkProof.phases += [ordered]@{ phase = $Phase; before = $PhaseReceipt.network_before; after = $PhaseReceipt.network_after }
        Write-Json $ReceiptPath $Receipt
        Write-Json $NetworkPath $NetworkProof
    }
} catch {
    $Failure = $_.Exception.Message
    $Receipt.failure = $Failure
    if ($null -ne $PhaseReceipt) { $PhaseReceipt.failure = $Failure }
} finally {
    # Process objects retain handles: never kill a later, unrelated reused PID.
    $Receipt.failure_cleanup = @()
    foreach ($Owned in @($Process, $ValidatedHost)) {
        if ($null -eq $Owned) { continue }
        try {
            if (-not $Owned.HasExited) {
                $Owned.Kill($true)
                Require ($Owned.WaitForExit(5000)) 'Owned process did not terminate during failure cleanup'
                $Receipt.failure_cleanup += [ordered]@{ pid = $Owned.Id; terminated = $true }
            }
        } catch {
            $Receipt.failure_cleanup += [ordered]@{ pid = $Owned.Id; terminated = $false; failure = $_.Exception.Message }
            if ($null -eq $Failure) { $Failure = 'Owned process cleanup failed: ' + $_.Exception.Message; $Receipt.failure = $Failure }
        }
    }
    try {
        $Receipt.cleanup_port = Get-PortSnapshot
        Require ($Receipt.cleanup_port.listeners.Count -eq 0) 'Port 8000 remains listening after final cleanup'
    } catch {
        $Receipt.cleanup_port_failure = $_.Exception.Message
        if ($null -eq $Failure) { $Failure = 'Port cleanup verification failed: ' + $_.Exception.Message; $Receipt.failure = $Failure }
    }
    if ($Adapters.Count -gt 0) {
        $Receipt.restoration.attempted = $true
        try {
            # Try every originally active adapter even if an earlier enable fails.
            $RestoreFailures = @()
            foreach ($Adapter in $Adapters) {
                try { $Adapter | Enable-NetAdapter -Confirm:$false -ErrorAction Stop }
                catch { $RestoreFailures += [ordered]@{ name = $Adapter.Name; failure = $_.Exception.Message } }
            }
            $Receipt.restoration.enable_failures = $RestoreFailures
            $RestoreClock = [Diagnostics.Stopwatch]::StartNew()
            do {
                $RestoredAdapters = @(Get-NetAdapter -ErrorAction Stop)
                $Missing = @($Adapters | Where-Object {
                    $OriginalGuid = $_.InterfaceGuid
                    @($RestoredAdapters | Where-Object { $_.InterfaceGuid -eq $OriginalGuid -and $_.Status -eq 'Up' }).Count -ne 1
                })
                if ($Missing.Count -eq 0) { break }
                Start-Sleep -Milliseconds 500
            } while ($RestoreClock.ElapsedMilliseconds -lt 15000)
            $Receipt.restoration.adapters = @($RestoredAdapters | Select-Object Name, InterfaceGuid, ifIndex, Status)
            Require ($Missing.Count -eq 0 -and $RestoreFailures.Count -eq 0) 'An initially active network adapter was not restored cleanly'
            $Receipt.restoration.passed = $true
        } catch {
            $Receipt.restoration.failure = $_.Exception.Message
            if ($null -eq $Failure) { $Failure = 'Network restoration failed: ' + $_.Exception.Message; $Receipt.failure = $Failure }
        }
    }
    $Receipt.restoration.finished_at_utc = [datetimeoffset]::UtcNow.ToString('o')
    $Receipt.finished_at_utc = [datetimeoffset]::UtcNow.ToString('o')
    $Receipt.passed = $null -eq $Failure -and $Receipt.phases.Count -eq 2 -and
        @($Receipt.phases | Where-Object { $_.passed -ne $true }).Count -eq 0 -and $Receipt.restoration.passed -eq $true
    $NetworkProof.passed = $Receipt.passed
    $NetworkProof.restoration = $Receipt.restoration
    Write-Json $ReceiptPath $Receipt
    Write-Json $NetworkPath $NetworkProof
}
if (-not $Receipt.passed) { throw ('Windows packaged smoke failed: ' + $Failure + '; see ' + $ReceiptPath) }

