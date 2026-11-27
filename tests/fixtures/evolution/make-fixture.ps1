param([string]$Output = 'outputs/evolution-fixture')
$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/../director-first/make-fixture.ps1" -Output $Output | Out-Null
$dir = (Get-Item -LiteralPath $Output).FullName
$filters = @(
    "[0:v]drawbox=x=350:y=40:w=175:h=175:color=red:t=fill:enable='between(t,39,44)'[v]"
    '[1:a]volume=0.07,adelay=10000:all=1[a]'
    '[2:a]volume=0.07,adelay=20000:all=1[b]'
    '[3:a]volume=0.07,adelay=30000:all=1[c]'
    '[a][b][c]amix=inputs=3:normalize=0,apad[out]'
)
& ffmpeg -hide_banner -v error -y -f lavfi -i 'color=c=0x18232b:s=640x360:r=24:d=46' `
    -i "$dir/fixture-0.wav" -i "$dir/fixture-1.wav" -i "$dir/fixture-2.wav" `
    -filter_complex ($filters -join ';') -map '[v]' -map '[out]' -t 46 `
    -c:v libx264 -pix_fmt yuv420p -c:a aac "$dir/quiet-clean.mp4"
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg could not create the EVOLUTION fixture.' }
Get-Item -LiteralPath "$dir/quiet-clean.mp4"