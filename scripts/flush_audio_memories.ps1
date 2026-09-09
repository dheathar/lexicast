# Moves captured audio from the local pending folder to the network dump.
# Runs as the scheduled task "Lexicast audio-memories flush" (every 15 min).
# Design rule: archival must NEVER block transcription. If the share is
# unreachable, robocopy fails and the files simply stay pending until a
# later run -- eventual delivery, not guaranteed-instant delivery.
# The UNC path is deliberate: a non-interactive scheduled task does not see
# the user's mapped S: drive letter, but UNC works.
$src = "F:\InHouse-Apps\lexicast\archive_pending"
$dst = "\\100.118.147.81\dump\audio-memories"
New-Item -ItemType Directory -Force $src | Out-Null
if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Force $dst | Out-Null }
robocopy $src $dst /MOV /NP /NFL /NDL /NJH /NJS | Out-Null
exit 0
