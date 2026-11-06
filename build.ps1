$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
Push-Location $projectRoot
try {
    $appVersion = (& $pythonPath -c 'from course_assistant import __version__; print(__version__)').Trim()
    if ($LASTEXITCODE -ne 0 -or $appVersion -notmatch '^\d+\.\d+\.\d+$') { throw '无法读取有效版本号。' }
    & $pythonPath -m PyInstaller --noconfirm --onedir --windowed --name CourseStudyAssistant --collect-all playwright --add-data 'course_assistant/static;course_assistant/static' app.py
    if ($LASTEXITCODE -ne 0) { throw '打包失败。' }
    $distRoot = Join-Path $projectRoot 'dist\CourseStudyAssistant'
    $internalDir = Join-Path $distRoot '_internal'
    # Windows 和 VC++ x64 Redistributable 提供这些库，只处理本次打包生成的副本。
    Get-ChildItem -LiteralPath $internalDir -File | Where-Object {
        $_.Name -like 'api-ms-win-*.dll' -or $_.Name -eq 'ucrtbase.dll' -or $_.Name -like 'VCRUNTIME140*.dll'
    } | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $projectRoot 'dist\CourseStudyAssistant\使用说明.md')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination (Join-Path $projectRoot 'dist\CourseStudyAssistant\LICENSE')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party_licenses') -Destination $distRoot -Recurse
    $docsOut = New-Item -ItemType Directory -Path (Join-Path $distRoot 'docs') -Force
    $publicDocs = @(
        'preview.png', 'preview-mobile.png', 'grading-preview.png', 'grading-rules-preview.png',
        '版本验证.md', '评分细则导入说明.md', '隐私和本地数据.md'
    )
    foreach ($doc in $publicDocs) {
        Copy-Item -LiteralPath (Join-Path $projectRoot "docs\$doc") -Destination $docsOut.FullName
    }
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'release') -Force | Out-Null
    Compress-Archive -Path (Join-Path $projectRoot 'dist\CourseStudyAssistant') -DestinationPath (Join-Path $projectRoot ("release\CourseStudyAssistant-$appVersion-windows-x64.zip")) -Force
} finally { Pop-Location }
