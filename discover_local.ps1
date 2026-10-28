# Daily on this computer (Windows Task Scheduler "FIP discover tournaments"): padelfip.com's
# Cloudflare blocks the entry list requests from GitHub Actions, so new entry list PDFs are
# found here, pushed, and the refresh workflow is started to rebuild the pages.
# Works in its own clone, so your working copy and unpushed commits are never touched.
$clone = "$env:LOCALAPPDATA\fip-discover"
$env:PYTHONUTF8 = 1
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
Start-Transcript -Path "$env:TEMP\fip-discover.log" | Out-Null

if (-not (Test-Path $clone)) { git clone -q https://github.com/AysuSulucay/Premier-Padel-Tour-Women-oop-viewer.git $clone }
Set-Location $clone
git pull -q --rebase
if ($LASTEXITCODE) { git rebase --abort 2>$null; git reset -q --hard origin/main; git pull -q }

python discover_tournaments.py
git add data/tournaments.json data/pdfs
git diff --cached --quiet
if ($LASTEXITCODE) { git commit -q -m "Discover tournaments (local)" }

# Also pushes a commit left over from a run whose push lost a race with the bot
if (git log -1 origin/main..HEAD) {
    git push -q
    if (-not $LASTEXITCODE) { gh workflow run refresh.yml --ref main }
}
Stop-Transcript
