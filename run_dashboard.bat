@echo off
cd /d "%~dp0"
set "DASHBOARD_PORT=8501"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "[System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | Where-Object { $_.OperationalStatus -eq 'Up' -and $_.NetworkInterfaceType -ne 'Loopback' -and $_.GetIPProperties().GatewayAddresses.Count -gt 0 } | ForEach-Object { $_.GetIPProperties().UnicastAddresses } | Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' -and -not $_.Address.ToString().StartsWith('169.254.') } | Select-Object -First 1 -ExpandProperty Address | ForEach-Object { $_.ToString() }"`) do set "DASHBOARD_IP=%%I"
echo.
if defined DASHBOARD_IP (
  echo Dashboard address on your local network:
  echo   http://%DASHBOARD_IP%:%DASHBOARD_PORT%
) else (
  echo Could not determine a local-network IP address.
  echo Open http://localhost:%DASHBOARD_PORT% on this computer.
)
echo.
py -m streamlit run app.py --server.port %DASHBOARD_PORT%
if errorlevel 1 (
  echo.
  echo The dashboard could not start. Install dependencies with:
  echo py -m pip install --user -r requirements.txt
  pause
)
