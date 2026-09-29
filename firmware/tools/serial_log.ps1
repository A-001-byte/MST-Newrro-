# serial_log.ps1: read the board's serial log (115200) for N seconds; optional reset first. Prints and appends to a log file.
# Close the Arduino Serial Monitor first (only one program can open the COM port).
#   powershell -ExecutionPolicy Bypass -File tools\serial_log.ps1 -Port COM6 -Seconds 30 -Reset
param([string]$Port = "COM6", [int]$Seconds = 30, [switch]$Reset, [string]$LogFile = "")
$sp = New-Object System.IO.Ports.SerialPort $Port, 115200, "None", 8, "One"
$sp.ReadTimeout = 200
$sp.DtrEnable = $false
$sp.RtsEnable = $false
$sp.Open()
if ($Reset) {
  # ESP32-S3 USB-Serial/JTAG: pulse RTS (EN) to reboot, like esptool's hard reset
  $sp.RtsEnable = $true; Start-Sleep -Milliseconds 150; $sp.RtsEnable = $false
  $sp.Close(); Start-Sleep -Milliseconds 1500
  for ($i = 0; $i -lt 20; $i++) { try { $sp.Open(); break } catch { Start-Sleep -Milliseconds 250 } }
}
$end = (Get-Date).AddSeconds($Seconds)
$buf = ""
while ((Get-Date) -lt $end) {
  try { $chunk = $sp.ReadExisting() } catch { $chunk = "" }
  if ($chunk) {
    $buf += $chunk
    while ($buf.Contains("`n")) {
      $i = $buf.IndexOf("`n"); $line = $buf.Substring(0, $i).TrimEnd("`r"); $buf = $buf.Substring($i + 1)
      $stamped = "{0} {1}" -f (Get-Date -Format "HH:mm:ss.fff"), $line
      Write-Output $stamped
      if ($LogFile) { Add-Content -Path $LogFile -Value $stamped -Encoding utf8 }
    }
  } else { Start-Sleep -Milliseconds 50 }
}
if ($buf) { Write-Output $buf }
$sp.Close()
