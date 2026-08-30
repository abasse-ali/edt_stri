# Fait tourner le bot sur ce PC Windows : demarrage a l'ouverture de session,
# relance automatique apres un plantage, sans fenetre noire a l'ecran.
#
#   powershell -ExecutionPolicy Bypass -File deploiement\installer_tache_windows.ps1
#
# ATTENTION : ce n'est pas du 24/7. PC eteint ou en veille = bot arrete, et les
# demandes attendront ton retour. Pour du vrai 24/7, voir docs/DEPLOIEMENT.md.

$racine = Split-Path -Parent $PSScriptRoot
$python = Join-Path $racine "venv\Scripts\pythonw.exe"   # pythonw : sans console
$script = Join-Path $racine "src\bot_discord.py"

if (-not (Test-Path $python)) {
    Write-Output "pythonw.exe introuvable dans le venv : $python"
    Write-Output "Cree le venv d'abord, ou corrige le chemin."
    exit 1
}

$action = New-ScheduledTaskAction -Execute $python -Argument "-u `"$script`"" `
                                  -WorkingDirectory $racine
$declencheur = New-ScheduledTaskTrigger -AtLogOn
$reglages = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName "Bot EDT STRI" -Force `
    -Action $action -Trigger $declencheur -Settings $reglages `
    -Description "Formulaire d'inscription aux agendas STRI (src/bot_discord.py)"

Write-Output "Tache creee. Elle demarrera a ta prochaine ouverture de session."
Write-Output "Demarrer maintenant :  Start-ScheduledTask -TaskName 'Bot EDT STRI'"
Write-Output "Arreter             :  Stop-ScheduledTask  -TaskName 'Bot EDT STRI'"
Write-Output "Supprimer           :  Unregister-ScheduledTask -TaskName 'Bot EDT STRI'"
Write-Output ""
Write-Output "Pense a empecher la mise en veille, sinon le bot s'arretera avec l'ecran :"
Write-Output "  powercfg /change standby-timeout-ac 0"
