$ErrorActionPreference = 'Stop'

function Invoke-ReleaseStep {
    param(
        [Parameter(Mandatory = $true)] [string] $Name,
        [Parameter(Mandatory = $true)] [string] $Command,
        [Parameter(Mandatory = $true)] [string[]] $Arguments
    )

    Write-Host "`n==> $Name" -ForegroundColor Cyan
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repositoryRoot

try {
    if (-not (Test-Path '.env.prod.template')) {
        throw '.env.prod.template is missing'
    }

    Invoke-ReleaseStep 'Development Compose config' 'docker' @('compose', 'config', '--quiet')

    $env:YUXI_ENV_FILE = '.env.prod.template'
    $env:JWT_SECRET_KEY = 'release-check-jwt-secret-00000000000000000000000000000000'
    $env:YUXI_INSTANCE_ID = 'release-check-instance'
    $env:SANDBOX_PROVISIONER_TOKEN = 'release-check-sandbox-token-0000000000000000000000000000'
    $env:POSTGRES_PASSWORD = 'release-check-postgres-password'
    $env:NEO4J_PASSWORD = 'release-check-neo4j-password'
    $env:MINIO_ACCESS_KEY = 'release-check-minio-access'
    $env:MINIO_SECRET_KEY = 'release-check-minio-secret'
    Invoke-ReleaseStep 'Production Compose config' 'docker' @(
        'compose', '--env-file', '.env.prod.template', '-f', 'docker-compose.prod.yml', 'config', '--quiet'
    )

    Invoke-ReleaseStep 'Backend Ruff' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'run', 'ruff', 'check', 'package', 'server', 'test'
    )
    Invoke-ReleaseStep 'Backend unit tests' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'run', '--group', 'test', 'pytest',
        'test/unit', '-m', 'not slow', '-q'
    )
    Invoke-ReleaseStep 'ResearchCompass integration tests' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'run', '--group', 'test', 'pytest',
        'test/integration/api/test_research_compass_core.py',
        'test/integration/services/test_research_user_study_repository.py',
        'test/integration/services/test_task_repository.py', '-q'
    )
    Invoke-ReleaseStep 'Frontend API contract tests' 'docker' @(
        'compose', 'exec', '-T', 'web', 'pnpm', 'run', 'test:unit'
    )
    Invoke-ReleaseStep 'Frontend ESLint' 'docker' @(
        'compose', 'exec', '-T', 'web', 'pnpm', 'run', 'lint:check'
    )
    Invoke-ReleaseStep 'Frontend production build' 'docker' @(
        'compose', 'exec', '-T', 'web', 'pnpm', 'run', 'build'
    )

    Write-Host "`nRelease checks passed." -ForegroundColor Green
}
finally {
    Pop-Location
    Remove-Item Env:YUXI_ENV_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:JWT_SECRET_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:YUXI_INSTANCE_ID -ErrorAction SilentlyContinue
    Remove-Item Env:SANDBOX_PROVISIONER_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:POSTGRES_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:NEO4J_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:MINIO_ACCESS_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:MINIO_SECRET_KEY -ErrorAction SilentlyContinue
}

