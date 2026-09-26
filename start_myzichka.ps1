$python = 'F:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$botFile = Join-Path $PSScriptRoot 'myzichka.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python не найден: $python"
}
if (-not (Test-Path -LiteralPath $botFile)) {
    throw "Файл бота не найден: $botFile"
}

$secureToken = Read-Host 'Вставьте новый токен бота' -AsSecureString
$env:DISCORD_BOT_TOKEN = [System.Net.NetworkCredential]::new('', $secureToken).Password
$secureToken.Dispose()

try {
    & $python $botFile
}
finally {
    Remove-Item Env:DISCORD_BOT_TOKEN -ErrorAction SilentlyContinue
}