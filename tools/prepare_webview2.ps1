$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Source = (Get-Content -Raw -Encoding UTF8 (Join-Path $Root 'vendor-sources.json') | ConvertFrom-Json).webview2
$Archive = Join-Path $Root 'build/webview2.cab'
$Expanded = Join-Path $Root 'build/webview2-expanded'
$Destination = Join-Path $Root 'vendor/webview2'
New-Item -ItemType Directory -Force (Split-Path $Archive -Parent) | Out-Null
if (-not (Test-Path $Archive)) {
    Invoke-WebRequest -Uri $Source.url -OutFile $Archive -TimeoutSec 300
}
if ((Get-Item $Archive).Length -ne $Source.bytes -or (Get-FileHash $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Source.sha256) {
    throw 'Microsoft fixed WebView2 archive does not match its pinned size/SHA256'
}
if (Test-Path $Expanded) { Remove-Item -Recurse -Force $Expanded }
New-Item -ItemType Directory -Force $Expanded | Out-Null
& expand.exe $Archive '-F:*' $Expanded | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Fixed runtime extraction failed' }
$Executables = @(Get-ChildItem -Recurse -File $Expanded -Filter 'msedgewebview2.exe')
if ($Executables.Count -ne 1) { throw 'Expected exactly one fixed WebView2 runtime' }
$Signature = Get-AuthenticodeSignature $Executables[0].FullName
if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
    throw 'Microsoft WebView2 executable signature is invalid'
}
if (Test-Path $Destination) { Remove-Item -Recurse -Force $Destination }
New-Item -ItemType Directory -Force (Split-Path $Destination -Parent) | Out-Null
Move-Item $Executables[0].Directory.FullName $Destination
$Identity = [ordered]@{schema_version=1; version=$Source.version; architecture=$Source.architecture; archive_sha256=$Source.sha256; files=@{}}
Get-ChildItem -Recurse -File $Destination | ForEach-Object {
    $Relative = [IO.Path]::GetRelativePath($Destination, $_.FullName).Replace('\','/')
    $Identity.files[$Relative] = [ordered]@{bytes=$_.Length; sha256=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$Identity | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $Root 'vendor/runtime-assets.json')
Write-Output ('Prepared fixed WebView2 ' + $Source.version + ' ' + $Source.architecture + ' with ' + $Identity.files.Count + ' sealed files')
