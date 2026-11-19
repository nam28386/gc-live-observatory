param(
    [ValidateRange(1,65535)][int]$Port = 8000,
    [ValidateRange(2,3600)][int]$Seconds = 30
)
$ErrorActionPreference = 'Stop'
$listener = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen)
if ($listener.Count -ne 1) { throw 'Expected exactly one IPv4 loopback observatory listener.' }
$processId = $listener[0].OwningProcess
$url = "http://127.0.0.1:$Port/api/status"
$status = Invoke-RestMethod $url -TimeoutSec 3
if ($status.mode -ne 'LIVE' -or !$status.recorder.enabled -or !$status.recorder.session_id -or
    $status.software_revision -notmatch '^[0-9a-f]{40}$') {
    throw 'Resource measurement requires a running LIVE recorder with a known software SHA.'
}
$runtime = Join-Path $env:LOCALAPPDATA 'GCLiveObservatory'
$drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($runtime))
function Get-CaptureBytes {
    $total = 0L
    foreach ($name in @('live.sqlite', 'live.sqlite-wal', 'live.sqlite-shm')) {
        $file = Get-Item -LiteralPath (Join-Path $runtime $name) -ErrorAction SilentlyContinue
        if ($file) { $total += $file.Length }
    }
    return $total
}

$start = [DateTimeOffset]::UtcNow
$timer = [System.Diagnostics.Stopwatch]::StartNew()
$first = Get-Process -Id $processId
$initialCpu = $first.TotalProcessorTime.TotalSeconds
$initialBytes = Get-CaptureBytes
$initialFree = $drive.AvailableFreeSpace
$peak = $first.WorkingSet64
$samples = 1
do {
    Start-Sleep -Milliseconds 500
    $current = Get-Process -Id $processId
    if ($current.StartTime -ne $first.StartTime) { throw 'Publisher process changed during sampling.' }
    $peak = [Math]::Max($peak, $current.WorkingSet64)
    $samples++
} while ($timer.Elapsed.TotalSeconds -lt $Seconds)
$final = Invoke-RestMethod $url -TimeoutSec 3
$end = [DateTimeOffset]::UtcNow
$duration = $timer.Elapsed.TotalSeconds
$endBytes = Get-CaptureBytes
$endFree = $drive.AvailableFreeSpace
if ($final.recorder.session_id -ne $status.recorder.session_id -or $final.software_revision -ne $status.software_revision -or
    $final.recorder_error) { throw 'Recorder failed or changed session during sampling.' }
[ordered]@{
    schema_version = 1
    source_sha = $status.software_revision
    session_id = $status.recorder.session_id
    started_utc = $start.ToString('o')
    ended_utc = $end.ToString('o')
    elapsed_seconds = $duration
    cpu_seconds = $current.TotalProcessorTime.TotalSeconds - $initialCpu
    peak_sampled_working_set_bytes = $peak
    capture_bytes_start = $initialBytes
    capture_bytes_end = $endBytes
    volume_free_bytes_start = $initialFree
    volume_free_bytes_end = $endFree
    received_frames_delta = $final.receiver.socket_received_frames - $status.receiver.socket_received_frames
    durable_market_records_delta = $final.receiver.durable_market_records - $status.receiver.durable_market_records
    uncertain_receipts_delta = $final.receiver.uncertain_received_frames - $status.receiver.uncertain_received_frames
    samples = $samples
} | ConvertTo-Json
