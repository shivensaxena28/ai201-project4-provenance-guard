#!/usr/bin/env bash
#
# demo.sh — end-to-end walkthrough of Provenance Guard for the video.
#
# STEP 1: in a SEPARATE terminal, start the server and leave it running:
#     python app.py            # (or: .venv/Scripts/python.exe app.py)
# STEP 2: in this terminal (Git Bash), run:
#     bash demo.sh
#
# It submits a human-sounding piece, then an AI-sounding piece, appeals the
# first one (auto-wiring its content_id), shows the audit log, and finally
# fires 12 rapid requests to trip the rate limit.

set -u
BASE="http://127.0.0.1:5000"

# If an HTTP proxy is configured (common on Windows / corporate machines), curl
# will try to route 127.0.0.1 through it and fail with no response. Tell curl to
# go direct for localhost. Belt-and-suspenders: env var AND a per-call flag.
export no_proxy="127.0.0.1,localhost"
export NO_PROXY="127.0.0.1,localhost"
curl() { command curl --noproxy '127.0.0.1,localhost' "$@"; }

# --- Find a Python for pretty-printing JSON (optional; falls back to raw) -----
PY=""
for c in python python3 py; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ] && [ -x ".venv/Scripts/python.exe" ]; then PY=".venv/Scripts/python.exe"; fi
pp() { if [ -n "$PY" ]; then "$PY" -m json.tool; else cat; echo; fi; }

# --- Make sure the server is actually up before we start -----------------------
if ! curl -s -o /dev/null "$BASE/health"; then
  echo "ERROR: server not reachable at $BASE"
  echo "Start it first in another terminal:  python app.py"
  exit 1
fi

echo
echo "########################################################"
echo "# 1. Submit a casual, human-sounding piece"
echo "########################################################"
RESP=$(curl -s -X POST "$BASE/submit" -H "Content-Type: application/json" -d '{
  "text": "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. probably wont go back unless someone drags me there",
  "creator_id": "demo-human"
}')
echo "$RESP" | pp
# Capture the content_id (UUID) for the appeal step -- no Python needed.
CID=$(echo "$RESP" | grep -o '[0-9a-fA-F]\{8\}-[0-9a-fA-F]\{4\}-[0-9a-fA-F]\{4\}-[0-9a-fA-F]\{4\}-[0-9a-fA-F]\{12\}' | head -1)
echo
echo ">>> captured content_id = $CID"

echo
echo "########################################################"
echo "# 2. Submit a formal, AI-sounding piece (label changes)"
echo "########################################################"
curl -s -X POST "$BASE/submit" -H "Content-Type: application/json" -d '{
  "text": "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment.",
  "creator_id": "demo-ai"
}' | pp

echo
echo "########################################################"
echo "# 3. Appeal the first submission (status -> under_review)"
echo "########################################################"
curl -s -X POST "$BASE/appeal" -H "Content-Type: application/json" -d "{
  \"content_id\": \"$CID\",
  \"creator_reasoning\": \"I wrote this myself from personal experience. English is my second language so my writing can read as formal.\"
}" | pp

echo
echo "########################################################"
echo "# 4. Show the structured audit log"
echo "########################################################"
curl -s "$BASE/log?limit=5" | pp

echo
echo "########################################################"
echo "# 5. Rate limiting: 12 rapid requests (limit is 10/min)"
echo "########################################################"
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BASE/submit" \
    -H "Content-Type: application/json" \
    -d '{"text": "This is a test submission for rate limit testing purposes only.", "creator_id": "ratelimit-test"}'
done
echo
echo ">>> done"
