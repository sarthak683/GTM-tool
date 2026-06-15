param(
    [Parameter(Mandatory = $true)]
    [string]$ContainerSasUrl,

    [Parameter(Mandatory = $true)]
    [string[]]$Files,

    [string]$Prefix = "prod-postgres"
)

$ErrorActionPreference = "Stop"

function Join-BlobUrl {
    param(
        [string]$ContainerUrl,
        [string]$BlobName
    )

    $parts = $ContainerUrl -split "\?", 2
    $base = $parts[0].TrimEnd("/")
    $sas = if ($parts.Count -gt 1) { "?" + $parts[1] } else { "" }
    return "$base/$BlobName$sas"
}

foreach ($file in $Files) {
    if (-not (Test-Path -LiteralPath $file)) {
        throw "File not found: $file"
    }

    $item = Get-Item -LiteralPath $file
    $blobName = "$Prefix/$($item.Name)"
    $uri = Join-BlobUrl -ContainerUrl $ContainerSasUrl -BlobName $blobName

    $headers = @{
        "x-ms-blob-type" = "BlockBlob"
        "x-ms-version" = "2023-11-03"
    }

    Invoke-WebRequest `
        -Uri $uri `
        -Method Put `
        -Headers $headers `
        -InFile $item.FullName `
        -ContentType "application/octet-stream" `
        -UseBasicParsing | Out-Null

    [pscustomobject]@{
        Uploaded = $item.FullName
        BlobName = $blobName
        SizeBytes = $item.Length
    }
}
