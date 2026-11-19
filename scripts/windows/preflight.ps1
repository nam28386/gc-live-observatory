param(
    [ValidateRange(1,65535)][int]$GatewayPort = 9001,
    [ValidateRange(1,65535)][int]$Port = 8000,
    [switch]$Running,
    [switch]$Synthetic
)
$ErrorActionPreference = 'Stop'
function Get-Listeners([int]$ListenerPort) {
    [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object Port -eq $ListenerPort
}
function Confirm-Loopback([int]$ListenerPort, [string]$Service) {
    $listeners = @(Get-Listeners $ListenerPort)
    if (!$listeners) { throw "$Service is absent on port $ListenerPort. Check that it is running and verify the port." }
    if ($listeners | Where-Object { $_.Address.ToString() -notin @('127.0.0.1', '::1') }) {
        throw "$Service port $ListenerPort has a non-loopback listener. Bind it to 127.0.0.1."
    }
    if (!($listeners | Where-Object { $_.Address.ToString() -eq '127.0.0.1' })) {
        throw "$Service port $ListenerPort has no IPv4 loopback listener."
    }
}

if (!$Synthetic) {
    if ($GatewayPort -eq $Port) { throw 'Gateway and dashboard ports must differ.' }
    Confirm-Loopback $GatewayPort 'Fxaurum gateway'
}
if ($Running) { Confirm-Loopback $Port 'Dashboard' }
elseif (Get-Listeners $Port) { throw "Dashboard port $Port is already in use." }
