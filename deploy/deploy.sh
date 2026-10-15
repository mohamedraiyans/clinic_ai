#!/usr/bin/env bash
# Runs ON THE SERVER, from the repository folder. Started by the GitHub Actions deploy job, or by hand:
#
#     bash deploy/deploy.sh <full-git-sha>
#
# It moves the server to exactly that commit, rebuilds, waits until the API is healthy, and goes back to the
# previous commit if the new version does not start. Your data lives in Docker volumes, so it is not touched.
set -euo pipefail

main() {
  cd "$(dirname "$0")/.."
  local sha="${1:?usage: bash deploy/deploy.sh <full-git-sha>}"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "Not a full commit SHA: $sha"; exit 2; }
  [ -f deploy/.env ] || { echo "deploy/.env is missing on the server"; exit 1; }

  local compose=(docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env)
  local previous
  previous="$(git rev-parse HEAD)"

  git fetch --quiet origin
  git checkout --quiet --detach "$sha"
  echo "Deploying $sha (previous: $previous)"

  healthy() {
    local _
    for _ in $(seq 1 30); do
      if "${compose[@]}" exec -T api python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/auth/config', timeout=5)" \
        >/dev/null 2>&1; then
        return 0
      fi
      sleep 4
    done
    return 1
  }

  if "${compose[@]}" up -d --build --remove-orphans && healthy; then
    echo "Deploy OK: $sha"
    docker image prune -f >/dev/null || true
  else
    echo "Deploy FAILED. Last API logs:"
    "${compose[@]}" logs --tail=60 api || true
    echo "Rolling back to $previous"
    git checkout --quiet --detach "$previous"
    "${compose[@]}" up -d --build --remove-orphans
    exit 1
  fi
}

# The whole script is inside main() and ends with exit, so changing this file during a deploy is safe.
main "$@"
exit $?
