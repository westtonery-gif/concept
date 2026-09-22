#!/bin/zsh
# One invocation of the clipping department for one episode card, as a schedule runs it (task 34).
# Loads .env (nothing else does, CLIPPING_RUNBOOK.md), puts Homebrew on PATH (launchd has no login
# shell) and appends to .omemo/clips-<card>.log. With OMEMO_UPLOAD_POST_MAX_NEW_PER_RUN=1, every run
# posts the next approved clip and collects the previous one.
set -eu
cd "${0:A:h}/.."
card="$1"
export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"
set -a; source ./.env; set +a
mkdir -p .omemo
{ echo "=== $(date '+%Y-%m-%d %H:%M:%S')"; .venv/bin/python demo_clips.py "$card"; } >> ".omemo/clips-$card.log" 2>&1
