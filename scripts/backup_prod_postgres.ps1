param(
    [string]$Kubeconfig = "C:\gtm-prototype\tmp\beacon-test-kubeconfig.yaml",
    [string]$Namespace = "gtm-prod",
    [string]$Pod = "gtm-postgresql-0",
    [string]$Database = "beacon",
    [string]$User = "beacon",
    [string]$OutDir = "C:\gtm-prototype\tmp\prod-db-backups",
    [int]$KeepLatest = 14,
    [string]$BlobContainerSasUrl = $env:PROD_BACKUP_BLOB_CONTAINER_SAS_URL
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force $OutDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = Join-Path $OutDir "$Database-prod-$timestamp.dump"
$metaPath = Join-Path $OutDir "$Database-prod-$timestamp.txt"

$remoteCommand = @"
export PGPASSWORD=`$(cat /opt/bitnami/postgresql/secrets/password)
/opt/bitnami/postgresql/bin/pg_dump -U $User -d $Database -Fc --no-owner --no-acl
"@

kubectl --kubeconfig $Kubeconfig -n $Namespace exec "pod/$Pod" -- bash -lc $remoteCommand > $backupPath

$sizeBytes = (Get-Item $backupPath).Length
@(
    "created_at=$((Get-Date).ToString("o"))"
    "namespace=$Namespace"
    "pod=$Pod"
    "database=$Database"
    "format=pg_dump custom (-Fc)"
    "size_bytes=$sizeBytes"
    "restore_example=pg_restore --clean --if-exists --no-owner --no-acl -U $User -d $Database `"$backupPath`""
) | Set-Content -Path $metaPath

Get-ChildItem $OutDir -Filter "$Database-prod-*.dump" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $KeepLatest |
    ForEach-Object {
        $oldMeta = [System.IO.Path]::ChangeExtension($_.FullName, ".txt")
        Remove-Item -LiteralPath $_.FullName -Force
        if (Test-Path -LiteralPath $oldMeta) {
            Remove-Item -LiteralPath $oldMeta -Force
        }
    }

[pscustomobject]@{
    BackupPath = $backupPath
    MetadataPath = $metaPath
    SizeBytes = $sizeBytes
    SizeMB = [math]::Round($sizeBytes / 1MB, 2)
    Kept = (Get-ChildItem $OutDir -Filter "$Database-prod-*.dump").Count
}

if ($BlobContainerSasUrl) {
    & "$PSScriptRoot\upload_backup_to_blob.ps1" `
        -ContainerSasUrl $BlobContainerSasUrl `
        -Files @($backupPath, $metaPath)
}
