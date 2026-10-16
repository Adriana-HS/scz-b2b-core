# Corre localmente lo mismo que el job "database" de la CI.
# Requisitos: Docker Desktop encendido y Supabase CLI (scoop install supabase  o  npm i -g supabase).
# Uso (desde la raíz del repo):  .\scripts\test-db.ps1
$ErrorActionPreference = "Stop"

supabase db start
if ($LASTEXITCODE -ne 0) { throw "Falló al levantar Postgres o al aplicar las migraciones" }

supabase test db
$tests = $LASTEXITCODE

supabase db lint --level error --fail-on error
$lint = $LASTEXITCODE

python scripts/ci/check_repo.py
$repo = $LASTEXITCODE

supabase stop --no-backup
if ($tests -ne 0 -or $lint -ne 0 -or $repo -ne 0) { exit 1 }
Write-Host "Todo en verde" -ForegroundColor Green
