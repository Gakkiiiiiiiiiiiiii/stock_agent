param(
    [switch]$WithEmbedding,
    [switch]$Build
)

$ErrorActionPreference = "Stop"

$argsList = @("compose")
if ($WithEmbedding) {
    $argsList += @("--profile", "embedding")
}
$argsList += "up"
if ($Build) {
    $argsList += "--build"
}
$argsList += "-d"

Write-Host "Starting stock_agent Docker stack..."
if ($WithEmbedding) {
    Write-Host "Embedding profile enabled. Ensure EMBEDDING_PROVIDER=openai_compatible in .env."
} else {
    Write-Host "Embedding profile disabled. API will use EMBEDDING_PROVIDER default/local_ngram unless .env overrides it."
}

docker @argsList

Write-Host ""
Write-Host "API:       http://127.0.0.1:8000"
Write-Host "Qdrant:    http://127.0.0.1:6333"
Write-Host "Reranker:  http://127.0.0.1:8010"
Write-Host "Postgres:  127.0.0.1:5433"
Write-Host "Redis:     127.0.0.1:6379"
if ($WithEmbedding) {
    Write-Host "Embedding: http://127.0.0.1:8001/v1"
}
