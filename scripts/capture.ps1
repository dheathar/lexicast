# lexicast capture: record the default microphone, then auto-submit to /transcribe.
# Runs in ITS OWN console window (the agent launches it via Start-Process) --
# the user talks and presses ENTER in this window to stop.
# The job id lands in <wav>.job.txt for the driving agent to pick up.
# Requires host ffmpeg (winget Gyan.FFmpeg on cronos) -- the container has no
# mic access, which is exactly why capture is a host-side helper, not API.
param([string]$Note = "")

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$slug = "capture"
if ($Note) {
    $slug = ($Note -replace "[^A-Za-z0-9\u0370-\u03ff_-]+", "-").Trim("-").ToLower()
    if (-not $slug) { $slug = "capture" }
    if ($slug.Length -gt 40) { $slug = $slug.Substring(0, 40) }
}
$dir = "F:\InHouse-Apps\lexicast\captures"
New-Item -ItemType Directory -Force $dir | Out-Null
$out = Join-Path $dir "${stamp}_${slug}.wav"

$devs = & ffmpeg -hide_banner -list_devices true -f dshow -i dummy 2>&1 | Out-String
$mics = [regex]::Matches($devs, '"([^"]+)"\s+\(audio\)') |
        Where-Object { $_.Groups[1].Value -match "microphone|mic" }
if (-not $mics) { $mics = [regex]::Matches($devs, '"([^"]+)"\s+\(audio\)') }
if (-not $mics) { Write-Host "no audio input device found"; Read-Host "ENTER to close"; exit 1 }
$dev = $mics[0].Groups[1].Value

Write-Host "=== lexicast capture ==="
Write-Host "mic  : $dev"
Write-Host "note : $Note"
Write-Host "file : $out"
Write-Host "Talk. Press ENTER here when done."
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "ffmpeg"
$psi.Arguments = "-hide_banner -f dshow -i audio=`"$dev`" -y `"$out`""
$psi.RedirectStandardInput = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$p = [System.Diagnostics.Process]::Start($psi)
[void](Read-Host)
# 'q' on stdin lets ffmpeg finalize the wav header; a hard kill would leave
# the RIFF sizes unwritten and the file unplayable
if (-not $p.HasExited) { $p.StandardInput.WriteLine("q") }
$p.WaitForExit(15000) | Out-Null
if (-not $p.HasExited) { $p.Kill() }

Write-Host "saved. submitting to /transcribe..."
$resp = curl.exe -s -X POST http://localhost:5005/transcribe -F "file=@$out" -F "title=$Note"
try {
    $job = ($resp | ConvertFrom-Json).job_id
    Set-Content -Path "$out.job.txt" -Value $job
    Write-Host "job: $job"
    Write-Host "processing runs in the background -- transcript + notes will land in S:\audio-memories"
} catch {
    Write-Host "submit failed: $resp"
}
Read-Host "ENTER to close"
