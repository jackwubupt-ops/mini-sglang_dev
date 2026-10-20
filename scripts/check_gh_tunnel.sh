#!/usr/bin/env bash
# scripts/check_gh_tunnel.sh -- read-only check of the SSH RemoteForward route to GitHub.
#   ps:127.0.0.1:17897 --(SSH RemoteForward)--> Windows 127.0.0.1:7897 (Clash Verge mixed port)
# 1. something listens on 127.0.0.1:17897, and it is an sshd process owned by xwc (our own
#    Remote-SSH session, not another user's listener on the shared host);
# 2. GitHub answers an HTTPS HEAD request through it (certificate verified as usual);
# 3. git ls-remote of the fork works with the proxy given per command.
# Changes nothing, stores no proxy setting, makes one attempt per check and never falls back
# to direct GitHub access. Exit 0 only if all three pass.
set -uo pipefail
PROXY=http://127.0.0.1:17897
PROXY_PORT=17897
FORK=https://github.com/jackwubupt-ops/mini-sglang_dev.git
OWNER=xwc
fail() { echo "STOP: $*" >&2; exit 1; }

[ "$(id -un)" = "$OWNER" ] || fail "run as $OWNER, not $(id -un)"
command -v ss > /dev/null || fail "ss not found"

line=$(ss -ltnpH "sport = :$PROXY_PORT" 2>/dev/null | head -n 1)
[ -n "$line" ] || fail "nothing listens on 127.0.0.1:$PROXY_PORT: the SSH session has no RemoteForward (reconnect VS Code Remote-SSH; ExitOnForwardFailure must not have failed)"
case "$line" in *"127.0.0.1:$PROXY_PORT "*) ;; *) fail "listener is not bound to 127.0.0.1 only: $line";; esac
pid=$(sed -n 's/.*pid=\([0-9]*\).*/\1/p' <<< "$line" | head -n 1)
[ -n "$pid" ] || fail "cannot see which process owns port $PROXY_PORT, so it may not be your tunnel: $line"
owner=$(ps -o user= -p "$pid" | tr -d ' ')
cmd=$(ps -o comm= -p "$pid" | tr -d ' ')
# OpenSSH >= 9.8 names the per-session process sshd-session
if [ "$owner" != "$OWNER" ] || { [ "$cmd" != sshd ] && [ "$cmd" != sshd-session ]; }; then
  fail "port $PROXY_PORT is held by pid $pid ($cmd, user '$owner'), not by your sshd session; do not use it"
fi
echo "ok: 127.0.0.1:$PROXY_PORT is held by your sshd (pid $pid)"

code=$(curl -sS -o /dev/null -I --connect-timeout 10 --max-time 20 -x "$PROXY" -w '%{http_code}' https://github.com) \
  || fail "curl through $PROXY to https://github.com failed: is Clash Verge running on Windows (port 7897)?"
case "$code" in 2??|3??) echo "ok: https://github.com answers $code through the tunnel";;
  *) fail "https://github.com answered '$code' through the tunnel";; esac

git -c "http.proxy=$PROXY" ls-remote "$FORK" HEAD > /dev/null \
  || fail "git ls-remote $FORK through $PROXY failed"
echo "ok: git ls-remote of the fork works through the tunnel"
