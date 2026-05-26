$path = "D:\project\OKIL\wiki"
$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $path
$watcher.IncludeSubdirectories = $true
$watcher.EnableRaisingEvents = $true

$action = {
    Start-Sleep 5
    Set-Location "D:\project\OKIL"
    git add wiki/
    $diff = git diff --cached --name-only
    if ($diff) {
        git commit -m "wiki: auto-sync $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
        git push
        Write-Host "✅ Wiki синхронизирована: $diff"
    }
}

Register-ObjectEvent $watcher Changed -Action $action
Register-ObjectEvent $watcher Created -Action $action

Write-Host "👀 Слежу за wiki\... (Ctrl+C для остановки)"
while ($true) { Start-Sleep 1 }
