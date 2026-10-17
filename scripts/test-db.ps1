# Corre localmente lo mismo que el job "database" de la CI.
# Requisitos: Docker Desktop encendido. La Supabase CLI es opcional: si no está instalada,
# se usa `npx supabase@<versión de la CI>` (requiere Node.js).
# Uso (desde la raíz del repo):  .\scripts\test-db.ps1
# "Continue": la CLI escribe avisos en stderr (p. ej. "new version available") y en PowerShell 5.1
# eso cuenta como error con "Stop". El resultado de cada paso se mide con $LASTEXITCODE.
$ErrorActionPreference = "Continue"

# Misma versión que SUPABASE_CLI_VERSION en .github/workflows/ci.yml
$cliVersion = (Select-String -Path ".github/workflows/ci.yml" -Pattern 'SUPABASE_CLI_VERSION:\s*(\S+)').Matches[0].Groups[1].Value
function Invoke-Supabase {
    if (Get-Command supabase -ErrorAction SilentlyContinue) { supabase @args }
    else { npx -y "supabase@$cliVersion" @args }
}

# La CLI 2.120.0 en Windows falla en `db start` al crear su carpeta temporal en
# %LOCALAPPDATA%\Temp (FileSystem.makeTempDirectoryScoped NotFound). Con una carpeta propia funciona.
$tmp = Join-Path $env:LOCALAPPDATA "scz-b2b-core-tmp"
New-Item -ItemType Directory -Force $tmp | Out-Null
$env:TEMP = $tmp
$env:TMP = $tmp

Invoke-Supabase db start
if ($LASTEXITCODE -ne 0) { throw "Falló al levantar Postgres o al aplicar las migraciones" }

Invoke-Supabase test db
$tests = $LASTEXITCODE

Invoke-Supabase db lint --level error --fail-on error
$lint = $LASTEXITCODE

python scripts/ci/check_repo.py
$repo = $LASTEXITCODE

Invoke-Supabase stop --no-backup
if ($tests -ne 0 -or $lint -ne 0 -or $repo -ne 0) { exit 1 }
Write-Host "Todo en verde" -ForegroundColor Green
