$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
Push-Location $projectRoot
try {
    $appVersion = (& $pythonPath -c 'from course_assistant import __version__; print(__version__)').Trim()
    if ($LASTEXITCODE -ne 0 -or $appVersion -notmatch '^\d+\.\d+\.\d+$') { throw '无法读取有效版本号。' }
    & $pythonPath -m PyInstaller --noconfirm --onedir --windowed --name CourseStudyAssistant --collect-all playwright --add-data 'course_assistant/static;course_assistant/static' app.py
    if ($LASTEXITCODE -ne 0) { throw '打包失败。' }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $projectRoot 'dist\CourseStudyAssistant\使用说明.md')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination (Join-Path $projectRoot 'dist\CourseStudyAssistant\LICENSE')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docs') -Destination (Join-Path $projectRoot 'dist\CourseStudyAssistant\docs') -Recurse -Force
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'release') -Force | Out-Null
    Compress-Archive -Path (Join-Path $projectRoot 'dist\CourseStudyAssistant') -DestinationPath (Join-Path $projectRoot ("release\CourseStudyAssistant-$appVersion-windows-x64.zip")) -Force
} finally { Pop-Location }
