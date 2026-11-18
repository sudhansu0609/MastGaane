# MastGaane's environment, (re)built in place. Run by "MastGaane Setup.exe", or by hand:
#   powershell -ExecutionPolicy Bypass -File installer\setup_env.ps1
# Every step skips what is already in place, so re-running it repairs a half-finished or
# cleaned-up install (e.g. after a disk cleaner removed .venv).
. (Join-Path $PSScriptRoot "setup_common.ps1")
$root = $script:AppRoot
Set-Steps 2

Step "Python 3.13 environment (uv sync: Playwright, FastAPI, Whisper, TTS)"
$uv = Require-Command "uv" "Install uv (https://astral.sh/uv), then run Setup again."
Invoke-Native "uv sync" $uv @("sync") $root

Step "Checking the result"
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Fail "uv sync did not create .venv\Scripts\python.exe" }
& $py -c "import mastgaane, fastapi, typer; print('MastGaane: deps import OK')"
if ($LASTEXITCODE -ne 0) { Fail "the environment does not import its core packages" }
Done "MastGaane's environment is ready. Run 'uv run mastgaane setup login' once to sign in to Instagram."
