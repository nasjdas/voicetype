# VoiceType — one-command install for Windows.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/nasjdas/voicetype/main/install.ps1 | iex"
#
# Downloads VoiceType, installs Python if you don't have it, and starts it.
# No admin rights. No git. Nothing leaves your machine except these downloads.

$ErrorActionPreference = "Stop"

$Repo    = "nasjdas/voicetype"
$Dir     = if ($env:VOICETYPE_DIR) { $env:VOICETYPE_DIR } else { "$env:LOCALAPPDATA\VoiceType" }
$PyVer   = "3.13.14"     # pinned: 3.12.11+ ship no Windows installer at all
$MinMinor = 10

function Say  ($m) { Write-Host "→ $m" -ForegroundColor Cyan }
function Good ($m) { Write-Host "✓ $m" -ForegroundColor Green }
function Die  ($m) { Write-Host "✗ $m" -ForegroundColor Red; exit 1 }

# ── find a usable Python ─────────────────────────────────────────────────────
# The trap: %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe is not Python. It's a
# 0-byte stub that opens the Microsoft Store. Running it looks like success and
# installs nothing, so check for it explicitly rather than trusting the name.
function Test-StoreStub ($path) {
  if ($path -like '*\WindowsApps\*') { return $true }
  try {
    $i = Get-Item $path -Force -ErrorAction Stop
    if ($i.Length -eq 0) { return $true }
    if ($i.Attributes -band [IO.FileAttributes]::ReparsePoint) { return $true }
  } catch { return $true }
  return $false
}

function Get-Python {
  $found = @()
  try {
    # py -0p lists every real interpreter with its full path.
    $out = & py -0p 2>$null
    foreach ($line in $out) {
      if ($line -match '([A-Za-z]:\\[^\s].*python\.exe)') { $found += $Matches[1] }
    }
  } catch {}
  foreach ($c in @("python.exe", "python3.exe")) {
    try {
      $cmd = Get-Command $c -ErrorAction SilentlyContinue
      if ($cmd) { $found += $cmd.Source }
    } catch {}
  }
  foreach ($p in ($found | Select-Object -Unique)) {
    if (-not (Test-Path $p)) { continue }
    if (Test-StoreStub $p)   { continue }
    try {
      $v = & $p -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
      if ([int]$v -ge (300 + $MinMinor)) { return $p }
    } catch {}
  }
  return $null
}

function Install-Python {
  # python.org's own installer, per-user, so it never asks for admin.
  # (winget's Python package triggers a UAC prompt even with --scope user.)
  $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
  $url  = "https://www.python.org/ftp/python/$PyVer/python-$PyVer-$arch.exe"
  $exe  = "$env:TEMP\python-$PyVer-$arch.exe"
  Say "installing Python $PyVer ($arch) — no admin needed"
  Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
  $p = Start-Process $exe -Wait -PassThru -ArgumentList @(
    "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1",
    "Include_test=0", "SimpleInstall=1")
  Remove-Item $exe -ErrorAction SilentlyContinue
  if ($p.ExitCode -ne 0) { Die "the Python installer failed (code $($p.ExitCode))." }
  # PATH changed, but not inside THIS already-running process. Re-read it.
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
              [Environment]::GetEnvironmentVariable('Path','User')
}

# ── get the code ─────────────────────────────────────────────────────────────
function Get-VoiceType {
  # A zipball, not a clone — most people don't have git, and needing it would
  # turn one command into two.
  $zip = "$env:TEMP\voicetype.zip"
  $tmp = "$env:TEMP\voicetype-extract"
  Say "downloading VoiceType"
  Invoke-WebRequest -Uri "https://codeload.github.com/$Repo/zip/refs/heads/main" `
                    -OutFile $zip -UseBasicParsing
  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  # GitHub nests everything under voicetype-main/ — hoist it out.
  $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
  if (-not $inner) { Die "the download looked wrong — no folder inside the zip." }
  if (Test-Path $Dir) { Remove-Item $Dir -Recurse -Force }
  New-Item -ItemType Directory -Path (Split-Path $Dir -Parent) -Force | Out-Null
  Move-Item $inner.FullName $Dir
  Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# ── go ───────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  VoiceType — local voice typing" -ForegroundColor White
Write-Host "  nothing leaves your machine" -ForegroundColor DarkGray
Write-Host ""

$py = Get-Python
if (-not $py) {
  Install-Python
  $py = Get-Python
  if (-not $py) { Die "Python installed but I still can't find it. Try opening a new terminal and running this again." }
}
Say "using $(& $py --version)"

Get-VoiceType
Set-Location $Dir

Say "creating a private environment"
& $py -m venv .venv
# Never touch Activate.ps1 — it's a script FILE, so the default Windows execution
# policy blocks it. Calling python.exe directly needs no activation and no policy.
$VenvPy = Join-Path $Dir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { Die "couldn't create the environment at $VenvPy" }

& $VenvPy -m pip install --upgrade pip --quiet
Say "installing (the speech library is big — a few minutes the first time)"
& $VenvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Die "installing the dependencies failed." }

Say "checking everything imported"
$check = @"
import sys
bad = []
for m in ('numpy','scipy','sounddevice','pystray','PIL','onnx_asr'):
    try: __import__(m)
    except Exception as e: bad.append('%s: %s' % (m, e))
if bad:
    print('\n'.join(bad)); sys.exit(1)
"@
$check | & $VenvPy -
if ($LASTEXITCODE -ne 0) { Die "some libraries didn't install correctly (see above)." }

# pythonw.exe = no console window hanging around for the rest of the session.
$VenvPyw = Join-Path $Dir ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $VenvPyw)) { $VenvPyw = $VenvPy }

# A shortcut, so it's launchable without ever opening a terminal again.
try {
  $lnk = "$([Environment]::GetFolderPath('StartMenu'))\Programs\VoiceType.lnk"
  $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
  $s.TargetPath = $VenvPyw
  $s.Arguments = "-m voicetype"
  $s.WorkingDirectory = $Dir
  $s.Description = "Local voice typing"
  $s.Save()
} catch {}

Write-Host ""
Good "Installed."
Write-Host ""
Write-Host "  Double-tap Right Ctrl, talk, tap once to stop." -ForegroundColor White
Write-Host "  Your words get typed wherever the cursor is."
Write-Host ""
Write-Host "  A 🎙 appears near the clock. Right-click it for the dashboard." -ForegroundColor DarkGray
Write-Host "  The first dictation downloads the speech model (~670 MB, once)." -ForegroundColor DarkGray
Write-Host "  After that it's fully offline." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Starting it now. It's also in your Start menu." -ForegroundColor DarkGray
Write-Host ""

Start-Process $VenvPyw -ArgumentList "-m voicetype" -WorkingDirectory $Dir
