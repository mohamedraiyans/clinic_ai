#!/usr/bin/env bash
# Creates deploy/.env ON THE SERVER, once. It fills in the site address, generates new random secrets,
# and asks for the keys that belong to you. Secrets are typed hidden: they are not shown on screen, and
# not saved in the shell history.
#
#     bash deploy/setup-env.sh <dns-name> <allowed-email>
#     bash deploy/setup-env.sh clinic-ai-raiyan.swedencentral.cloudapp.azure.com you@example.com
#
# Press Enter at the Azure OpenAI or Groq prompts to skip a provider you do not use (you need at least one).
set -euo pipefail

usage() { echo "usage: bash deploy/setup-env.sh <dns-name> <allowed-email>"; exit 2; }

main() {
  cd "$(dirname "$0")/.."
  local site="${1:-}" email="${2:-}"
  if [ -z "$site" ] || [ -z "$email" ]; then usage; fi
  [[ "$site" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Not a valid host name: $site"; exit 2; }
  [[ "$email" =~ ^[^@,[:space:]]+@[^@,[:space:]]+$ ]] || { echo "Not a valid email address: $email"; exit 2; }
  if [ -e deploy/.env ]; then
    echo "deploy/.env already exists. Move or delete it first if you really want to start again."
    exit 1
  fi

  rand() { openssl rand -hex "$1" 2>/dev/null || head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
  # make a value safe to use on the right-hand side of a sed "s|..|..|" command
  esc() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }
  set_value() { sed -i "s|^$1=.*|$1=$(esc "$2")|" deploy/.env; }
  # ask_secret NAME: hidden input; accepts the value alone or the whole NAME=value line; trims spaces and line ends
  ask_secret() {
    local v
    read -r -s -p "$1 (typing is hidden; paste, then Enter; empty = skip): " v || true
    echo >&2   # stderr: stdout is captured as the value
    v="${v#"$1="}"
    v="${v//[$'\r\n ']/}"
    printf '%s' "$v"
  }
  ask_plain() {
    local v
    read -r -p "$1 (Enter = skip): " v || true
    v="${v//[$'\r\n ']/}"
    printf '%s' "$v"
  }

  # tr removes any Windows line ends, which Docker's env-file parser rejects
  tr -d '\r' < deploy/.env.production.example > deploy/.env
  chmod 600 deploy/.env
  SETUP_OK=0
  trap '[ "$SETUP_OK" = 1 ] || { rm -f deploy/.env; echo "Stopped: deploy/.env was removed. Run the script again." >&2; }' EXIT

  local dbpw
  dbpw="$(rand 24)"
  set_value SITE_ADDRESS "$site"
  set_value PUBLIC_BASE_URL "https://$site"
  set_value FRONTEND_URL "https://$site"
  set_value GOOGLE_REDIRECT_URI "https://$site/api/auth/google/callback"
  set_value POSTGRES_PASSWORD "$dbpw"
  sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://clinic_user:$dbpw@db:5432/clinic_ai|" deploy/.env
  set_value JWT_SECRET "$(rand 32)"
  set_value SIGNING_SECRET "$(rand 32)"
  set_value ALLOWED_DOCTOR_EMAILS "$email"
  set_value DEV_LOGIN 0

  echo
  echo "Google sign-in (required)"
  local gid gsecret
  gid="$(ask_secret GOOGLE_CLIENT_ID)"
  gsecret="$(ask_secret GOOGLE_CLIENT_SECRET)"
  if [ -z "$gid" ] || [ -z "$gsecret" ]; then
    echo "Google client ID and secret are required for sign-in."
    exit 1
  fi
  set_value GOOGLE_CLIENT_ID "$gid"
  set_value GOOGLE_CLIENT_SECRET "$gsecret"

  echo
  echo "AI providers (at least one)"
  local az_endpoint az_key az_deploy groq
  az_endpoint="$(ask_plain 'AZURE_OPENAI_ENDPOINT, e.g. https://NAME.openai.azure.com/')"
  if [ -n "$az_endpoint" ]; then
    az_key="$(ask_secret AZURE_OPENAI_API_KEY)"
    az_deploy="$(ask_plain 'AZURE_OPENAI_DEPLOYMENT_NAME, the name of your model deployment')"
    if [ -z "$az_key" ] || [ -z "$az_deploy" ]; then
      echo "Azure OpenAI needs the key and the deployment name too."
      exit 1
    fi
    set_value AZURE_OPENAI_ENDPOINT "$az_endpoint"
    set_value AZURE_OPENAI_API_KEY "$az_key"
    set_value AZURE_OPENAI_DEPLOYMENT_NAME "$az_deploy"
  fi
  groq="$(ask_secret GROQ_API_KEY)"
  if [ -n "$groq" ]; then set_value GROQ_API_KEY "$groq"; fi
  if [ -z "$az_endpoint" ] && [ -z "$groq" ]; then
    echo "You need at least one AI provider (Azure OpenAI or Groq)."
    exit 1
  fi

  SETUP_OK=1
  echo
  echo "deploy/.env created (readable only by you). Checks that do not show any secret:"
  echo "  empty settings left : $(grep -cE '^[A-Z_]+=$' deploy/.env)  (empty AI-provider lines you skipped are normal)"
  echo "  placeholders left   : $(grep -c 'CHANGE_ME\|your-name\|you@example.com' deploy/.env)  (must be 0)"
  echo "  DEV_LOGIN           : $(grep '^DEV_LOGIN=' deploy/.env | cut -d= -f2)  (must be 0)"
  echo
  echo "Next: bash deploy/deploy.sh \"\$(git rev-parse HEAD)\""
}

main "$@"
exit $?
