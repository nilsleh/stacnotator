<#
.SYNOPSIS
    Run the local building-annotation stack (upstream develop) and back it up.

.DESCRIPTION
    Wraps `docker compose --env-file .env.develop -f docker-compose.dev.yml`.

      start          Start every service, apply migrations, wait until the app answers.
      stop | status | logs
      seed           Seed the curated building campaigns (reuses existing ones).
      refresh-imagery
                     Re-register the campaigns' chips on the tiler. Needed after the
                     tiler database was lost or a backup was restored into a new stack.
      backup         Dump the app database to data\backups\*.dump (or -BackupPath).
                     The tiler database is not backed up: pgstac dumps do not restore
                     cleanly, and refresh-imagery rebuilds it (search ids are deterministic).
      verify-backup  Restore a backup into a disposable database and count what came
                     back. Live data is never touched.

.EXAMPLE
    .\scripts\local.ps1 backup
    .\scripts\local.ps1 verify-backup -BackupPath data\backups\stacnotator-20261016-170000.dump
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'logs', 'seed', 'refresh-imagery', 'backup', 'verify-backup')]
    [string]$Action,
    [string]$BackupPath,
    [string]$EnvFile = '.env.develop'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Compose {
    & docker compose --env-file $EnvFile -f docker-compose.dev.yml @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($args -join ' ') failed ($LASTEXITCODE)" }
}

function Wait-Url([string]$Url, [hashtable]$Headers = @{}) {
    $deadline = (Get-Date).AddMinutes(4)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -Headers $Headers -TimeoutSec 10 | Out-Null
            return
        } catch { Start-Sleep -Seconds 3 }
    }
    throw "$Url did not answer within 4 minutes"
}

if (-not (Test-Path $EnvFile)) { throw "$EnvFile not found (see docs/local-labeling.md)" }

switch ($Action) {
    'start' {
        Invoke-Compose up -d
        Wait-Url 'http://localhost:8000/api/openapi.json'
        Invoke-Compose exec -T backend alembic upgrade head
        Wait-Url 'http://localhost:5173'
        Write-Host 'Ready: http://localhost:5173 (API docs http://localhost:8000/api/docs)'
    }
    'stop' { Invoke-Compose stop }
    'status' { Invoke-Compose ps }
    'logs' { Invoke-Compose logs --tail 100 }
    'seed' {
        & python scripts/seed_building_datasets.py
        if ($LASTEXITCODE -ne 0) { throw 'Seeding failed' }
    }
    'refresh-imagery' {
        & python scripts/seed_building_datasets.py --refresh-imagery
        if ($LASTEXITCODE -ne 0) { throw 'Refreshing imagery failed' }
    }
    'backup' {
        if (-not $BackupPath) {
            $BackupPath = Join-Path $root "data\backups\stacnotator-$(Get-Date -Format 'yyyyMMdd-HHmmss').dump"
        }
        $BackupPath = [System.IO.Path]::GetFullPath($BackupPath)
        if (Test-Path $BackupPath) { throw "Backup already exists: $BackupPath" }
        New-Item -ItemType Directory -Path (Split-Path $BackupPath) -Force | Out-Null
        $tmp = "/tmp/backup-$([guid]::NewGuid().ToString('N')).dump"
        try {
            Invoke-Compose exec -T db sh -c "pg_dump -U `$POSTGRES_USER -d `$POSTGRES_DB -Fc -f $tmp"
            Invoke-Compose cp "db:$tmp" $BackupPath
        } finally {
            Invoke-Compose exec -T db rm -f $tmp
        }
        Write-Host "Backup saved to $BackupPath"
        Write-Host 'Imagery (cogs\, data\imagery\) and the env file are not included. Run verify-backup before relying on it.'
    }
    'verify-backup' {
        if (-not $BackupPath -or -not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
            throw 'Pass -BackupPath pointing to an existing .dump backup.'
        }
        $BackupPath = (Resolve-Path -LiteralPath $BackupPath).Path
        $suffix = [guid]::NewGuid().ToString('N')
        $tmp = "/tmp/verify-$suffix.dump"
        $check = "restore_check_$suffix"
        $query = 'SELECT (SELECT count(*) FROM data.campaigns) AS campaigns, (SELECT count(*) FROM data.annotation_tasks) AS scenes, (SELECT count(*) FROM data.annotations) AS annotations, (SELECT count(*) FROM data.building_hand_ins) AS hand_ins'
        try {
            Invoke-Compose cp $BackupPath "db:$tmp"
            Invoke-Compose exec -T db sh -c "createdb -U `$POSTGRES_USER $check"
            Invoke-Compose exec -T db sh -c "pg_restore -U `$POSTGRES_USER -d $check --exit-on-error $tmp"
            Invoke-Compose exec -T db sh -c "psql -v ON_ERROR_STOP=1 -U `$POSTGRES_USER -d $check -c '$query'"
            Write-Host 'Backup restored into a disposable database. Live data was not changed.'
        } finally {
            Invoke-Compose exec -T db sh -c "dropdb -U `$POSTGRES_USER --if-exists $check"
            Invoke-Compose exec -T db rm -f $tmp
        }
    }
}
