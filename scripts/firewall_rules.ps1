# PRINTS the Windows Firewall commands SwarmShield needs. It NEVER runs them.
# Copy the commands into an ADMINISTRATOR PowerShell window yourself, on laptop A (and B/C for UDP replies if blocked).
#   ./scripts/firewall_rules.ps1

Write-Host "1) The Wi-Fi/hotspot network must be marked Private (not Public):"
Write-Host "     Get-NetConnectionProfile"
Write-Host "     Set-NetConnectionProfile -InterfaceAlias 'Wi-Fi' -NetworkCategory Private"
Write-Host ""
Write-Host "2) Allow the SwarmShield ports on Private networks (run as Administrator):"
Write-Host "     netsh advfirewall firewall add rule name=""SwarmShield hub UDP 47000"" dir=in action=allow protocol=UDP localport=47000 profile=private"
Write-Host "     netsh advfirewall firewall add rule name=""SwarmShield observer UDP 47002"" dir=in action=allow protocol=UDP localport=47002 profile=private"
Write-Host "     netsh advfirewall firewall add rule name=""SwarmShield hub control TCP 47001"" dir=in action=allow protocol=TCP localport=47001 profile=private"
Write-Host "     netsh advfirewall firewall add rule name=""SwarmShield dashboard TCP 8080"" dir=in action=allow protocol=TCP localport=8080 profile=private"
Write-Host ""
Write-Host "   Alternative: when Windows shows the 'Allow Python' popup, tick PRIVATE networks and click Allow."
Write-Host ""
Write-Host "3) To remove the rules later:"
Write-Host "     netsh advfirewall firewall delete rule name=""SwarmShield hub UDP 47000"""
Write-Host "     netsh advfirewall firewall delete rule name=""SwarmShield observer UDP 47002"""
Write-Host "     netsh advfirewall firewall delete rule name=""SwarmShield hub control TCP 47001"""
Write-Host "     netsh advfirewall firewall delete rule name=""SwarmShield dashboard TCP 8080"""
Write-Host ""
Write-Host "Nothing above was executed."
