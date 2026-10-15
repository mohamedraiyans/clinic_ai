#!/usr/bin/env bash
# Tests deploy/setup-env.sh on a throw-away copy of the repository. It never touches your real deploy/.env.
#
#   in CI (runs automatically):  REPO="$GITHUB_WORKSPACE" bash deploy/test-setup-env.sh
#   on Windows with Docker:      docker run --rm -v "${PWD}:/repo:ro" ubuntu:24.04 bash /repo/deploy/test-setup-env.sh
set -u
REPO="${REPO:-/repo}"
T="$(mktemp -d)"
cp -r "$REPO/." "$T/work" && cd "$T/work" || exit 1
rm -f deploy/.env

pass=0; fail=0
check() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "  ok    $1"; else fail=$((fail+1)); echo "  FAIL  $1  (expected [$3] got [$2])"; fi; }
val() { grep "^$1=" deploy/.env | head -1 | cut -d= -f2-; }
exists() { if [ -e deploy/.env ]; then echo exists; else echo gone; fi; }
HOST=clinic-test.example.cloudapp.azure.com
MAIL=doc@example.com

echo "== 1. full run: Google + Azure + Groq, with tricky characters in the values"
GID='GOOGLE_CLIENT_ID=fake-client-id-123.example'      # pasted WITH the name
GSEC='FAKE-secret-a&b|c\d-e'                                         # & | \ must survive
printf '%s\n' "$GID" "$GSEC" 'https://res.openai.azure.com/' 'AZkey&|\x' 'my-deploy' 'groq-test|key' \
  | bash deploy/setup-env.sh "$HOST" "$MAIL" > "$T/out1.txt" 2> "$T/err1.txt"
check "exit code 0" "$?" "0"
check "client id (name prefix stripped)" "$(val GOOGLE_CLIENT_ID)" "fake-client-id-123.example"
check "client secret with & | \\ intact" "$(val GOOGLE_CLIENT_SECRET)" 'FAKE-secret-a&b|c\d-e'
check "azure endpoint" "$(val AZURE_OPENAI_ENDPOINT)" "https://res.openai.azure.com/"
check "azure key with & | \\ intact" "$(val AZURE_OPENAI_API_KEY)" 'AZkey&|\x'
check "azure deployment" "$(val AZURE_OPENAI_DEPLOYMENT_NAME)" "my-deploy"
check "groq key" "$(val GROQ_API_KEY)" "groq-test|key"
check "site address" "$(val SITE_ADDRESS)" "$HOST"
check "public base url" "$(val PUBLIC_BASE_URL)" "https://$HOST"
check "frontend url" "$(val FRONTEND_URL)" "https://$HOST"
check "google redirect" "$(val GOOGLE_REDIRECT_URI)" "https://$HOST/api/auth/google/callback"
check "allowed email" "$(val ALLOWED_DOCTOR_EMAILS)" "$MAIL"
check "DEV_LOGIN is 0" "$(val DEV_LOGIN)" "0"
check "DISABLE_API_DOCS stays 1" "$(val DISABLE_API_DOCS)" "1"
check "no placeholders left" "$(grep -c 'CHANGE_ME\|your-name\|you@example.com' deploy/.env)" "0"
check "db password length (48 hex)" "$(val POSTGRES_PASSWORD | tr -d '\n' | wc -c)" "48"
check "db password same inside DATABASE_URL" "$(val DATABASE_URL)" "postgresql://clinic_user:$(val POSTGRES_PASSWORD)@db:5432/clinic_ai"
check "JWT secret length (64 hex)" "$(val JWT_SECRET | tr -d '\n' | wc -c)" "64"
check "SIGNING secret length (64 hex)" "$(val SIGNING_SECRET | tr -d '\n' | wc -c)" "64"
check "JWT and SIGNING differ" "$([ "$(val JWT_SECRET)" != "$(val SIGNING_SECRET)" ] && echo yes)" "yes"
check "file permissions 600" "$(stat -c %a deploy/.env)" "600"
check "no secret printed on screen" "$(cat "$T/out1.txt" "$T/err1.txt" | grep -c 'FAKE-secret\|AZkey\|groq-test\|fake-client-id')" "0"
check "line count unchanged (no stray newlines)" "$(wc -l < deploy/.env)" "$(wc -l < deploy/.env.production.example)"

echo "== 2. running again must refuse and must NOT delete the existing file"
cp deploy/.env "$T/before.env"
bash deploy/setup-env.sh "$HOST" "$MAIL" </dev/null >"$T/out2.txt" 2>&1; rc=$?
check "exit code 1" "$rc" "1"
check "existing .env untouched" "$(cmp -s deploy/.env "$T/before.env" && echo same)" "same"

echo "== 3. Groq only (Azure skipped)"
rm -f deploy/.env
printf '%s\n' 'cid' 'csecret' '' 'groq-only' | bash deploy/setup-env.sh "$HOST" "$MAIL" >/dev/null 2>&1
check "exit code 0" "$?" "0"
check "groq set" "$(val GROQ_API_KEY)" "groq-only"
check "azure endpoint left empty" "$(val AZURE_OPENAI_ENDPOINT)" ""

echo "== 4. no AI provider at all -> fails and removes the half-made file"
rm -f deploy/.env
printf '%s\n' 'cid' 'csecret' '' '' | bash deploy/setup-env.sh "$HOST" "$MAIL" >/dev/null 2>&1; rc=$?
check "exit code 1" "$rc" "1"
check "deploy/.env removed" "$(exists)" "gone"

echo "== 5. Google missing -> fails and removes the file"
rm -f deploy/.env
printf '%s\n' '' '' | bash deploy/setup-env.sh "$HOST" "$MAIL" >/dev/null 2>&1; rc=$?
check "exit code 1" "$rc" "1"
check "deploy/.env removed" "$(exists)" "gone"

echo "== 6. Azure endpoint given but key or deployment missing -> fails"
rm -f deploy/.env
printf '%s\n' 'cid' 'csecret' 'https://x.openai.azure.com/' '' '' | bash deploy/setup-env.sh "$HOST" "$MAIL" >/dev/null 2>&1; rc=$?
check "exit code 1" "$rc" "1"
check "deploy/.env removed" "$(exists)" "gone"

echo "== 7. bad arguments"
bash deploy/setup-env.sh >/dev/null 2>&1;                          check "no arguments -> exit 2" "$?" "2"
bash deploy/setup-env.sh 'bad host!' "$MAIL" >/dev/null 2>&1;      check "bad host name -> exit 2" "$?" "2"
bash deploy/setup-env.sh "$HOST" 'not-an-email' >/dev/null 2>&1;   check "bad email -> exit 2" "$?" "2"
bash deploy/setup-env.sh "$HOST" 'a@b.c,d@e.f' >/dev/null 2>&1;   check "comma in email -> exit 2" "$?" "2"
check "no file left after bad arguments" "$(exists)" "gone"

echo "== 8. the generated file is in the format docker compose reads (KEY=value, LF only)"
rm -f deploy/.env
printf '%s\n' 'cid' 'csecret' '' 'groq-x' | bash deploy/setup-env.sh "$HOST" "$MAIL" >/dev/null 2>&1
check "no carriage returns" "$(grep -c "$(printf '\r')" deploy/.env)" "0"
check "every non-comment line is KEY=value" "$(grep -vcE '^(#.*|[A-Z_]+=.*|)$' deploy/.env)" "0"

echo
echo "RESULT: $pass passed, $fail failed"
rm -rf "$T"
[ "$fail" = 0 ]
