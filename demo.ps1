# demo.ps1 - end-to-end walkthrough of Provenance Guard for the video.
#
# Runs natively in PowerShell (Windows), where your Flask server actually lives.
# Use this instead of demo.sh if `bash` on your machine is WSL (a separate
# network namespace that can't see the Windows server on 127.0.0.1).
#
# STEP 1: in a SEPARATE terminal, start the server and leave it running:
#     python app.py
# STEP 2: in THIS PowerShell window, run:
#     .\demo.ps1
# (If you get an execution-policy error, run once:
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass )

$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:5000"

# Force direct connection (ignore any system proxy) so localhost always resolves.
[System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy

function Show($obj) { $obj | ConvertTo-Json -Depth 10 }

# --- Make sure the server is up before we start ------------------------------
try {
    $h = Invoke-RestMethod "$base/health"
    "Server is up: $($h.status)"
} catch {
    Write-Host "ERROR: server not reachable at $base" -ForegroundColor Red
    Write-Host "Start it first in another terminal:  python app.py"
    exit 1
}

Write-Host "`n########################################################"
Write-Host "# 1. Submit a casual, human-sounding piece"
Write-Host "########################################################"
$humanBody = @{
    text       = "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. probably wont go back unless someone drags me there"
    creator_id = "demo-human"
} | ConvertTo-Json
$resp = Invoke-RestMethod "$base/submit" -Method Post -ContentType "application/json" -Body $humanBody
Show $resp
$cid = $resp.content_id
Write-Host "`n>>> captured content_id = $cid" -ForegroundColor Cyan

Write-Host "`n########################################################"
Write-Host "# 2. Submit a formal, AI-sounding piece (label changes)"
Write-Host "########################################################"
$aiBody = @{
    text       = "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment."
    creator_id = "demo-ai"
} | ConvertTo-Json
Show (Invoke-RestMethod "$base/submit" -Method Post -ContentType "application/json" -Body $aiBody)

Write-Host "`n########################################################"
Write-Host "# 3. Appeal the first submission (status -> under_review)"
Write-Host "########################################################"
$appealBody = @{
    content_id        = $cid
    creator_reasoning = "I wrote this myself from personal experience. English is my second language so my writing can read as formal."
} | ConvertTo-Json
Show (Invoke-RestMethod "$base/appeal" -Method Post -ContentType "application/json" -Body $appealBody)

Write-Host "`n########################################################"
Write-Host "# 4. Show the structured audit log"
Write-Host "########################################################"
Show (Invoke-RestMethod "$base/log?limit=5")

Write-Host "`n########################################################"
Write-Host "# 5. Rate limiting: 12 rapid requests (limit is 10/min)"
Write-Host "########################################################"
$rlBody = @{ text = "This is a test submission for rate limit testing purposes only."; creator_id = "ratelimit-test" } | ConvertTo-Json
for ($i = 1; $i -le 12; $i++) {
    try {
        $r = Invoke-WebRequest "$base/submit" -Method Post -ContentType "application/json" -Body $rlBody -UseBasicParsing
        Write-Host $r.StatusCode
    } catch {
        Write-Host $_.Exception.Response.StatusCode.value__
    }
}
Write-Host "`n>>> done"
