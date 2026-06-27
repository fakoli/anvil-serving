# Bring the local SGLang serve up on the 96GB card (on-demand).
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
docker compose -f "$dir\docker-compose.yml" up -d
Write-Host "SGLang starting on GPU 1 (96GB). Watch: docker logs -f sglang"
Write-Host "Health: curl http://localhost:30000/health   Models: curl http://localhost:30000/v1/models"
