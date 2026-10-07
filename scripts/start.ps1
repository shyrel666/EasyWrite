$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$launchArgs = @($args)
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.runtime'
$uvDir = Join-Path $runtimeDir 'uv'
$uvPath = Join-Path $uvDir 'uv.exe'

try {
    # All tools and caches stay inside the project; no global PATH or registry changes.
    $env:UV_CACHE_DIR = Join-Path $runtimeDir 'cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtimeDir 'python'
    $env:UV_PYTHON_INSTALL_BIN = '0'
    $env:UV_PYTHON_INSTALL_REGISTRY = '0'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONUNBUFFERED = '1'
    if (-not $env:UV_DEFAULT_INDEX -and -not $env:UV_INDEX_URL) {
        $env:UV_DEFAULT_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    }
    if (-not $env:UV_PYTHON_INSTALL_MIRROR) {
        $env:UV_PYTHON_INSTALL_MIRROR = 'https://registry.npmmirror.com/-/binary/python-build-standalone'
    }

    if (-not (Test-Path -LiteralPath $uvPath -PathType Leaf)) {
        New-Item -ItemType Directory -Path $uvDir -Force | Out-Null
        switch ($env:PROCESSOR_ARCHITECTURE) {
            'AMD64' { $platform = 'windows-x86_64' }
            'ARM64' { $platform = 'windows-aarch64' }
            default { throw 'EasyWrite startup supports 64-bit Windows (x64 / ARM64).' }
        }
        $download = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'uv-downloads.txt') |
            Where-Object { $_.StartsWith("$platform|") } | Select-Object -First 1
        if (-not $download) { throw "No startup tool is available for $platform." }
        $artifact = $download.Split('|')
        $uvMirror = $env:EASYWRITE_UV_MIRROR
        if (-not $uvMirror) { $uvMirror = 'https://pypi.tuna.tsinghua.edu.cn' }
        $wheel = Join-Path $runtimeDir 'uv.whl'
        Write-Host '[INFO] Downloading the project-local startup tool from the package mirror...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $wheelUrl = $uvMirror.TrimEnd('/') + '/' + $artifact[1]
        Invoke-WebRequest -UseBasicParsing -Uri $wheelUrl -OutFile $wheel -TimeoutSec 120
        $hasher = [Security.Cryptography.SHA256]::Create()
        try {
            $wheelStream = [IO.File]::OpenRead($wheel)
            try {
                $actualSha = [BitConverter]::ToString($hasher.ComputeHash($wheelStream)).Replace('-', '').ToLowerInvariant()
            }
            finally { $wheelStream.Dispose() }
        }
        finally { $hasher.Dispose() }
        if ($actualSha -ne $artifact[2]) {
            throw 'The startup tool download is incomplete or has an invalid SHA256. Please retry.'
        }
        # Extract only the executable, avoiding any archive-controlled filesystem paths.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($wheel)
        try {
            $entry = $archive.Entries | Where-Object { $_.FullName -match '\.data/scripts/uv\.exe$' } |
                Select-Object -First 1
            if (-not $entry) { throw 'The startup tool archive has no uv.exe.' }
            $inputStream = $entry.Open()
            try {
                $outputStream = [IO.File]::Create("$uvPath.part")
                try { $inputStream.CopyTo($outputStream) }
                finally { $outputStream.Dispose() }
            }
            finally { $inputStream.Dispose() }
        }
        finally { $archive.Dispose() }
        Move-Item -LiteralPath "$uvPath.part" -Destination $uvPath -Force
    }

    Write-Host '[INFO] Preparing Python 3.12 and dependencies. The first launch may take a few minutes.'
    & $uvPath run --no-config --no-project --isolated --managed-python --python 3.12 `
        --with-requirements (Join-Path $projectRoot 'backend\requirements.txt') `
        -- python -u (Join-Path $PSScriptRoot 'start.py') @launchArgs
    exit $LASTEXITCODE
}
catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
