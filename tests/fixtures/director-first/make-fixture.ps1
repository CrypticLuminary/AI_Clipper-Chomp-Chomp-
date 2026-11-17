param([string]$Output = 'outputs/director-fixture')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$dir = (New-Item -ItemType Directory -Force -Path $Output).FullName
$rows = (Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'transcript.json') | ConvertFrom-Json).segments
$ff = @('-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', 'color=c=0x18232b:s=640x360:r=24:d=160',
    '-f', 'lavfi', '-i', 'sine=frequency=850:sample_rate=16000:duration=0.4',
    '-f', 'lavfi', '-i', 'anoisesrc=color=pink:amplitude=0.035:sample_rate=16000:duration=44:seed=7')
$mix = @('[1:a]volume=6,adelay=79000:all=1[peak]')
$labels = '[peak][2:a]'
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speech.Rate = 2
    for ($i = 0; $i -lt $rows.Count; $i++) {
        $row = $rows[$i]
        $wav = Join-Path $dir "fixture-$i.wav"
        $speech.SetOutputToWaveFile($wav)
        $speech.Speak($row.text)
        $speech.SetOutputToNull()
        $ff += @('-i', $wav)
        $duration = (($row.end_ms - $row.start_ms) / 1000).ToString(
            '0.000', [System.Globalization.CultureInfo]::InvariantCulture)
        $gain = if ($row.speaker_id -eq 'speaker-a') { '0.02' } else { '0.18' }
        $mix += "[$($i + 3):a]atrim=duration=${duration},volume=${gain},adelay=$($row.start_ms):all=1[a$i]"
        $labels += "[a$i]"
    }
} finally {
    $speech.Dispose()
}
$mix += "${labels}amix=inputs=$($rows.Count + 2):normalize=0,apad[out]"
$ff += @('-filter_complex', ($mix -join ';'), '-map', '0:v', '-map', '[out]', '-t', '160',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', (Join-Path $dir 'fixture.mp4'))
& ffmpeg @ff
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg could not create the development fixture.' }
Get-Item -LiteralPath (Join-Path $dir 'fixture.mp4')
