param(
    [switch]$Cli,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPythonw = Join-Path $VenvDir "Scripts\pythonw.exe"
$LogPath = Join-Path $ProjectDir "launcher.log"

function Write-LauncherLog {
    param([string]$Message)
    try {
        $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    }
    catch {}
}

function Show-LauncherError {
    param([string]$Message)
    Write-LauncherLog "ERROR: $Message"
    $details = "$Message`n`nSee launcher.log for full details."
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($details, "JavDB launcher error", "OK", "Error") | Out-Null
    }
    catch {
        Write-Host $details
    }
}

function Invoke-NativeLogged {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [switch]$Quiet
    )
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) {
            & $FilePath @Arguments 2>&1 | Out-Null
        }
        else {
            & $FilePath @Arguments 2>&1 | ForEach-Object {
                $text = $_.ToString()
                Write-Host $text
                Write-LauncherLog $text
            }
        }
        return [int]$LASTEXITCODE
    }
    catch {
        Write-LauncherLog "Failed to run $FilePath : $($_.Exception.Message)"
        return -1
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Test-PythonExecutable {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $exitCode = Invoke-NativeLogged -FilePath $Path -Arguments @(
        "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    ) -Quiet
    return $exitCode -eq 0
}

function Test-Dependencies {
    if (-not (Test-PythonExecutable $VenvPython)) { return $false }
    $exitCode = Invoke-NativeLogged -FilePath $VenvPython -Arguments @(
        "-c", "import nodriver, bs4"
    ) -Quiet
    return $exitCode -eq 0
}

function Find-Python {
    $projectPython = Join-Path $ProjectDir ".python\python.exe"
    if (Test-PythonExecutable $projectPython) { return $projectPython }

    $localCandidates = @(
        Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Programs\Python\Python*\python.exe") -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -ExpandProperty FullName
    )
    foreach ($candidate in $localCandidates) {
        if (Test-PythonExecutable $candidate) { return $candidate }
    }

    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $resolved = (& $pyLauncher.Source -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and (Test-PythonExecutable $resolved)) { return $resolved }
        }
        catch {}
        finally { $ErrorActionPreference = $previousPreference }
    }

    foreach ($name in @("python.exe", "python3.exe")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and (Test-PythonExecutable $command.Source)) { return $command.Source }
    }
    return $null
}

function Remove-BrokenVenv {
    if (-not (Test-Path -LiteralPath $VenvDir)) { return }
    $resolvedProject = [System.IO.Path]::GetFullPath($ProjectDir).TrimEnd('\')
    $resolvedVenv = [System.IO.Path]::GetFullPath($VenvDir).TrimEnd('\')
    if (-not $resolvedVenv.StartsWith($resolvedProject + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove an unexpected virtual environment path: $resolvedVenv"
    }
    Write-LauncherLog "Removing invalid virtual environment: $resolvedVenv"
    Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
}

function New-ProjectVenv {
    param([string]$Python)
    Write-LauncherLog "Creating virtual environment with $Python"
    $exitCode = Invoke-NativeLogged -FilePath $Python -Arguments @("-m", "venv", $VenvDir)
    return $exitCode -eq 0 -and (Test-PythonExecutable $VenvPython)
}

function Install-PythonFromOfficialSite {
    $version = "3.13.14"
    $installerName = "python-$version-amd64.exe"
    $installerUrl = "https://www.python.org/ftp/python/$version/$installerName"
    $installerPath = Join-Path ([System.IO.Path]::GetTempPath()) $installerName
    Write-LauncherLog "Downloading $installerUrl"

    $downloaded = $false
    if (Test-Path -LiteralPath $installerPath) {
        $cachedSignature = Get-AuthenticodeSignature -LiteralPath $installerPath
        $cachedSigner = if ($cachedSignature.SignerCertificate) { $cachedSignature.SignerCertificate.Subject } else { "" }
        if ($cachedSignature.Status -eq "Valid" -and $cachedSigner -like "*Python Software Foundation*") {
            Write-LauncherLog "Using cached signed Python installer"
            $downloaded = $true
        }
        else {
            Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
        }
    }

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $downloaded -and $curl) {
        $exitCode = Invoke-NativeLogged -FilePath $curl.Source -Arguments @(
            "--fail", "--location", "--retry", "3", "--output", $installerPath, $installerUrl
        )
        if ($exitCode -eq 0 -and (Test-Path -LiteralPath $installerPath)) {
            $downloaded = $true
        }
        else {
            Write-LauncherLog "curl failed; trying BITS"
            Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not $downloaded -and (Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue)) {
        try {
            Start-BitsTransfer -Source $installerUrl -Destination $installerPath -ErrorAction Stop
            $downloaded = Test-Path -LiteralPath $installerPath
        }
        catch {
            Write-LauncherLog "BITS failed: $($_.Exception.Message)"
            Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not $downloaded) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $installerUrl -OutFile $installerPath -ErrorAction Stop
            $downloaded = Test-Path -LiteralPath $installerPath
        }
        catch {
            Write-LauncherLog "Invoke-WebRequest failed: $($_.Exception.Message)"
        }
    }
    if (-not $downloaded) {
        throw "Python download failed. Check the network or proxy."
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
    $signer = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { "" }
    if ($signature.Status -ne "Valid" -or $signer -notlike "*Python Software Foundation*") {
        throw "The Python installer signature was not valid."
    }

    $targetDir = Join-Path $ProjectDir ".python"
    Write-LauncherLog "Installing signed Python $version into $targetDir"
    $installArgs = "/quiet InstallAllUsers=0 TargetDir=`"$targetDir`" PrependPath=0 AssociateFiles=0 Include_test=0 Include_launcher=0 InstallLauncherAllUsers=0 Include_tcltk=1 Shortcuts=0"
    $process = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Python installer failed with exit code $($process.ExitCode)."
    }
    Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
}

function Install-PythonRuntime {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Write-LauncherLog "Installing Python 3.13 with winget"
        $exitCode = Invoke-NativeLogged -FilePath $winget.Source -Arguments @(
            "install", "--id", "Python.Python.3.13", "--exact", "--scope", "user", "--silent",
            "--disable-interactivity", "--accept-package-agreements", "--accept-source-agreements"
        )
        if ($exitCode -eq 0 -and (Find-Python)) { return }
        Write-LauncherLog "winget did not provide a working Python; using the official installer"
    }
    Install-PythonFromOfficialSite
}

if ($SelfTest) {
    if (Test-PythonExecutable $VenvPython) {
        Write-Host "Launcher self-test: existing virtual environment is valid"
    }
    else {
        Write-Host "Launcher self-test: invalid virtual environment detected without terminating"
    }
    $testExitCode = Invoke-NativeLogged -FilePath $env:ComSpec -Arguments @(
        "/c", "echo simulated traceback 1>&2 & exit /b 7"
    ) -Quiet
    if ($testExitCode -ne 7) {
        throw "Launcher self-test failed: expected exit code 7, got $testExitCode"
    }
    Write-Host "Launcher self-test: native stderr handling OK"
    exit 0
}

try {
    Set-Location -LiteralPath $ProjectDir
    Write-LauncherLog "Launcher started"

    if (-not (Test-PythonExecutable $VenvPython)) {
        Remove-BrokenVenv
        $python = Find-Python

        if ($python -and -not (New-ProjectVenv $python)) {
            Write-LauncherLog "Existing Python could not create a working virtual environment"
            Remove-BrokenVenv
            $python = $null
        }

        if (-not $python) {
            Install-PythonRuntime
            $python = Find-Python
            if (-not $python) {
                throw "Python was installed but python.exe could not be found. Sign in to Windows again and retry."
            }
            if (-not (New-ProjectVenv $python)) {
                throw "The official Python runtime could not create a working virtual environment."
            }
        }
    }

    if (-not (Test-Dependencies)) {
        Write-LauncherLog "Installing locked Python dependencies"
        $pypiIndex = if ($env:JAVDB_PYPI_INDEX) { $env:JAVDB_PYPI_INDEX } else { "https://pypi.org/simple" }
        $exitCode = Invoke-NativeLogged -FilePath $VenvPython -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--progress-bar", "off",
            "--timeout", "30", "--retries", "2", "--index-url", $pypiIndex,
            "-r", (Join-Path $ProjectDir "requirements.lock")
        )
        if ($exitCode -ne 0 -or -not (Test-Dependencies)) {
            throw "Dependency installation failed. Check the network or proxy."
        }
    }

    if ($Cli) {
        Write-LauncherLog "Starting CLI"
        $exitCode = Invoke-NativeLogged -FilePath $VenvPython -Arguments @((Join-Path $ProjectDir "javdb_rating.py"))
        exit $exitCode
    }

    Write-LauncherLog "Starting GUI"
    $guiScript = Join-Path $ProjectDir "javdb_gui.py"
    Start-Process -FilePath $VenvPythonw -ArgumentList ('"{0}"' -f $guiScript) -WorkingDirectory $ProjectDir
}
catch {
    Show-LauncherError $_.Exception.Message
    exit 1
}
