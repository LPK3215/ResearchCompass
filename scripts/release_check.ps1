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
$originalLiteMode = [Environment]::GetEnvironmentVariable('LITE_MODE', 'Process')
Push-Location $repositoryRoot

try {
    $env:LITE_MODE = 'false'

    if (-not (Test-Path '.env.prod.template')) {
        throw '.env.prod.template is missing'
    }

    Invoke-ReleaseStep 'Development Compose config' 'docker' @('compose', 'config', '--quiet')
    Invoke-ReleaseStep 'Development Compose build and startup' 'docker' @('compose', 'up', '-d', '--build')

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
    Invoke-ReleaseStep 'Production service images' 'docker' @(
        'compose', '--env-file', '.env.prod.template', '-f', 'docker-compose.prod.yml',
        'build', 'api', 'worker', 'sandbox-provisioner'
    )
    $env:LITE_MODE = 'true'
    Invoke-ReleaseStep 'Production Lite web image' 'docker' @(
        'compose', '--env-file', '.env.prod.template', '-f', 'docker-compose.prod.yml', 'build', 'web'
    )
    $env:LITE_MODE = 'false'
    Invoke-ReleaseStep 'Production full web image' 'docker' @(
        'compose', '--env-file', '.env.prod.template', '-f', 'docker-compose.prod.yml', 'build', 'web'
    )

    Invoke-ReleaseStep 'Backend workspace lockfile' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'lock', '--check'
    )
    Invoke-ReleaseStep 'Backend package lockfile' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'lock', '--project', 'package', '--check'
    )
    Invoke-ReleaseStep 'Research Copilot Ruff format' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'run', 'ruff', 'format', '--check',
        'package/yuxi/agents/toolkits/research',
        'package/yuxi/services/research_copilot_service.py',
        'test/e2e/test_research_copilot_e2e.py',
        'test/unit/repositories/test_agent_repository_research_copilot.py',
        'test/unit/routers/test_agent_router_research_copilot.py',
        'test/unit/routers/test_research_copilot_router.py',
        'test/unit/server/test_lifespan_startup.py',
        'test/unit/services/test_research_copilot_service.py',
        'test/unit/toolkits/test_research_tools.py'
    )
    Invoke-ReleaseStep 'Backend Ruff' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'run', 'ruff', 'check', 'package', 'server', 'test'
    )
    Invoke-ReleaseStep 'Backend unit tests' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'run', '--group', 'test', 'pytest',
        'test/unit', '-m', 'not slow', '-q'
    )
    Invoke-ReleaseStep 'Backend integration tests' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'run', '--group', 'test', 'pytest',
        'test/integration', '-q'
    )
    Invoke-ReleaseStep 'Backend E2E tests' 'docker' @(
        'compose', 'exec', '-T', 'api', 'uv', 'run', '--group', 'test', 'pytest',
        'test/e2e', '-m', 'e2e', '-q'
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
    Invoke-ReleaseStep 'Documentation production build' 'docker' @(
        'run', '--rm',
        '--mount', "type=bind,source=$repositoryRoot,target=/workspace",
        '--mount', 'type=volume,target=/workspace/docs/node_modules',
        '--workdir', '/workspace/docs',
        'node:22-bookworm', 'sh', '-lc',
        'corepack pnpm install --frozen-lockfile && corepack pnpm run build'
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
    if ($null -eq $originalLiteMode) {
        Remove-Item Env:LITE_MODE -ErrorAction SilentlyContinue
    }
    else {
        $env:LITE_MODE = $originalLiteMode
    }
}
