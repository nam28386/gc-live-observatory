param([int]$GatewayPort = 9001, [int]$Port = 8000, [switch]$Running)
$ErrorActionPreference = 'Stop'
function Confirm-Loopback([int]$ListenerPort) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $ListenerPort -ErrorAction SilentlyContinue)
    if (!$listeners -or ($listeners | Where-Object { $_.LocalAddress -notin @('127.0.0.1', '::1') })) {
        throw "Port $ListenerPort requires a loopback-only listener"
    }
    if (!(Test-NetConnection 127.0.0.1 -Port $ListenerPort -InformationLevel Quiet)) {
        throw "IPv4 loopback port $ListenerPort is unreachable"
    }
}
Confirm-Loopback $GatewayPort
if ($Running) { Confirm-Loopback $Port }
elseif (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Dashboard port $Port is already in use"
}
