#!/bin/sh
# Post-deploy smoke test: is the release actually serving, safely?
#
#   smoke_test.sh <api base url> <site url> [expected git sha]
#
# Retries for ~2 minutes (machines may still be starting), then fails the deploy job.
set -eu
API="${1:?api url}"
SITE="${2:?site url}"
SHA="${3:-}"

retry() { # retry <description> <command...>
  desc="$1"
  shift
  i=0
  until "$@"; do
    i=$((i + 1))
    if [ "$i" -ge 24 ]; then
      echo "FAIL: $desc" >&2
      return 1
    fi
    sleep 5
  done
  echo "ok: $desc"
}

status_is() { [ "$(curl -s -o /dev/null -w '%{http_code}' "$2")" = "$1" ]; }
header_has() { curl -s -D - -o /dev/null "$1" | tr -d '\r' | grep -qi "^$2"; }

retry "API liveness" status_is 200 "$API/healthz"
retry "API readiness (Postgres + Redis)" status_is 200 "$API/readyz"
retry "public status endpoint" status_is 200 "$API/api/v1/status"
retry "forecast carries a disclaimer" sh -c "curl -sf '$API/api/v1/assets/BTC-USD/forecast' | grep -q disclaimer"
retry "HTTP redirects to HTTPS" sh -c "curl -s -o /dev/null -w '%{http_code}' 'http://${API#https://}/healthz' | grep -qE '^30[18]$'"
retry "HSTS on the API" header_has "$API/healthz" "strict-transport-security: max-age="
retry "CSP on the API" header_has "$API/healthz" "content-security-policy: default-src 'none'"
retry "metrics are not public" status_is 404 "$API/metrics"
retry "website serves" status_is 200 "$SITE/"
retry "SPA deep link serves index.html" status_is 200 "$SITE/models"
retry "CSP on the website" header_has "$SITE/" "content-security-policy: default-src 'self'"
if [ -n "$SHA" ]; then
  # The Sentry release / GIT_SHA the API reports must be the commit we just deployed.
  retry "deployed version is $SHA" sh -c "curl -sf '$API/healthz' | grep -q '$SHA'"
fi
echo "SMOKE TEST PASSED: $API, $SITE"
