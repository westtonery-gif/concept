#!/bin/zsh
# One pass of the unattended post queue, as a schedule runs it (ADR-0092). Loads .env (nothing else
# does, CLIPPING_RUNBOOK.md), puts Homebrew on PATH (launchd has no login shell) and appends to
# .omemo/post-queue.log. With OMEMO_UPLOAD_POST_MAX_NEW_PER_RUN=1 every pass collects the clip in
# flight and, once none is, posts the next one.
set -eu
cd "${0:A:h}/.."
export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"
set -a; source ./.env; set +a
mkdir -p .omemo
{ echo "=== $(date '+%Y-%m-%d %H:%M:%S')"; .venv/bin/python demo_post_queue.py "${1:-post-queue}"; } >> .omemo/post-queue.log 2>&1
