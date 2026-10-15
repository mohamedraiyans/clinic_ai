# Deploying Clinic AI to Azure

This folder is the **server** version of the project. The **local** version (`docker-compose.yml` in the repo root,
`.env`, `localhost:5173`) is not changed by anything here, so you can run both at the same time.

```
Local (your PC)                          Server (Azure VM)
docker-compose.yml                       deploy/docker-compose.prod.yml
Vite dev server, hot reload              built React app served by nginx
http://localhost:5173                    https://<your-name>.<region>.cloudapp.azure.com
.env  (DEV_LOGIN=1)                      deploy/.env  (DEV_LOGIN=0)  - lives only on the server
```

## How a change reaches the server

```
feature branch → pull request → tests pass → merge to main → "tests" workflow passes on main
              → "deploy" workflow → SSH to the VM → deploy/deploy.sh <exact commit>
              → rebuild → health check → (if it fails: automatic rollback to the previous commit)
              → check the live site from outside
```

- Work on a **branch**. `main` only gets code whose tests are green.
- The deploy workflow is **off** until you create the repository variable `DEPLOY_ENABLED = true`.
- Scheduled test runs, pull requests and other branches **never** deploy.
- If tests fail on `main`, **nothing is deployed**.

## What runs on the server

| Container | What it is | Reachable from the internet? |
|---|---|---|
| `caddy` | HTTPS front door. Gets and renews a free certificate for your DNS name | **Yes** (ports 80, 443) |
| `web` | The React app built once, served by nginx | No (only through Caddy) |
| `api` | FastAPI backend, no `--reload`, no source mounted | No (only through Caddy: `/api/*`, `/verify/*`) |
| `db` | PostgreSQL 16, data in a Docker volume | No |

---

## Part 1: One-time setup in the Azure portal

Use your student subscription. Check how much credit is left first: **Cost Management + Billing → Credits**.

1. **Resource group.** Create `clinic-ai-rg` in a region your subscription allows.
2. **Virtual machine** (Create a resource → Virtual machine):
   - Image: **Ubuntu Server 24.04 LTS**. Size: **B2s** (2 vCPU, 4 GB) or **B1ms** if you want it cheaper. The portal shows the monthly price before you create it.
   - Authentication: **SSH public key**. Let Azure generate a key pair and **keep the downloaded `.pem` file safe**.
   - Inbound ports: **SSH (22), HTTP (80), HTTPS (443)**.
   - Disk: Standard SSD, 30 GB is enough.
3. After it is created, open the VM → **Networking → Public IP address**:
   - Set **Assignment: Static**.
   - Set a **DNS name label**, for example `clinic-ai-raiyan`. Your address becomes
     `clinic-ai-raiyan.<region>.cloudapp.azure.com`.
4. **Budget alert.** Cost Management → Budgets → create a budget (for example 20 USD) with an email alert.
5. To save credit when you are not demoing: VM → **Stop** (shows "Stopped (deallocated)"). The disk still costs a little.

## Part 2: One-time setup on the server

Connect from PowerShell: `ssh -i path\to\key.pem azureuser@<your-dns-name>`

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # then log out and back in
```

Get the code. If the repository is **private**, create a read-only deploy key first:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub       # GitHub: repo → Settings → Deploy keys → Add (leave "write access" OFF)
printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
git clone git@github.com:mohamedraiyans/clinic_ai.git ~/clinic-ai
```

(For a public repository, `git clone https://github.com/mohamedraiyans/clinic_ai.git ~/clinic-ai` is enough.)

## Part 3: Server settings (secrets stay on the server)

```bash
cd ~/clinic-ai
cp deploy/.env.production.example deploy/.env
nano deploy/.env
```

Fill in every value. Rules:
- `SITE_ADDRESS`, `PUBLIC_BASE_URL`, `FRONTEND_URL`, `GOOGLE_REDIRECT_URI`: your real DNS name, `https://` for the last three.
- `DEV_LOGIN=0`. **Never** `1` on the server.
- `JWT_SECRET` and `SIGNING_SECRET`: new random values (`openssl rand -hex 32`), **different from your local ones**.
- `POSTGRES_PASSWORD`: long and random, and the same password inside `DATABASE_URL`.
- `ALLOWED_DOCTOR_EMAILS`: only the emails that may sign in.
- Azure OpenAI and Groq keys. **Rotate the keys that were pasted into chats earlier** in the Azure portal and in Groq, and use the new ones here.

## Part 4: Google sign-in for the server

In Google Cloud Console → Credentials → your OAuth client → **Authorized redirect URIs** → add:

```
https://<your-dns-name>/api/auth/google/callback
```

Keep the `http://localhost:8000/...` one, so Google login works locally and on the server. If the consent screen is in
"Testing" mode, your email must be listed under Test users.

## Part 5: First deploy by hand

```bash
cd ~/clinic-ai
bash deploy/deploy.sh "$(git rev-parse HEAD)"
```

Then open `https://<your-dns-name>`. The first start takes a minute while Caddy gets the certificate. Check:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env ps
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env logs --tail=50 api
```

## Part 6: Switch on continuous deployment

1. **A dedicated deploy key** (on your PC, never reuse your personal key):
   ```powershell
   ssh-keygen -t ed25519 -f clinic_deploy_key -N '""'
   ```
   Append the content of `clinic_deploy_key.pub` to `~/.ssh/authorized_keys` on the VM.
2. **The server's fingerprint**, so GitHub knows it is talking to the right machine:
   `ssh-keyscan -t ed25519 <your-dns-name>` → copy the line. (Compare it with
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the VM.)
3. GitHub → repo → **Settings → Secrets and variables → Actions**:

   | Type | Name | Value |
   |---|---|---|
   | Secret | `VM_HOST` | your DNS name |
   | Secret | `VM_USER` | `azureuser` (or the user you chose) |
   | Secret | `VM_SSH_KEY` | the whole content of `clinic_deploy_key` (the private one) |
   | Secret | `VM_KNOWN_HOSTS` | the `ssh-keyscan` line |
   | Variable | `SITE_URL` | `https://<your-dns-name>` |
   | Variable | `DEPLOY_ENABLED` | `true` (set this **last**) |

4. Optional: **Settings → Environments → production → Required reviewers** adds a manual approval before each deploy.

## Part 7: Test the pipeline (both directions)

1. **The good path.** On a branch, change a visible word (for example the title on the login page), open a pull
   request, wait for green tests, merge. Watch **Actions**: `tests` → `deploy`. Refresh the site.
2. **The bad path.** On a branch, break a test on purpose, merge it (or push to `main`). The `tests` run goes red and
   **`deploy` does not run**. Revert the change.
3. **The automatic rollback.** Only if you want to see it: deploy a commit that crashes the API at startup. The script
   prints the logs and returns the server to the previous commit.

## Everyday commands (on the server)

```bash
cd ~/clinic-ai
C="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env"
$C ps                       # what is running
$C logs -f api              # follow the API logs
$C restart api              # restart one service
git log --oneline -3        # which commit is deployed
bash deploy/deploy.sh <sha> # deploy (or roll back to) a specific commit by hand
```

## Backups

The patients, prescriptions and audit log live in the `pgdata_prod` Docker volume on the VM.

```bash
mkdir -p ~/backups
C="docker compose -f ~/clinic-ai/deploy/docker-compose.prod.yml --env-file ~/clinic-ai/deploy/.env"
$C exec -T db pg_dump -U clinic_user clinic_ai | gzip > ~/backups/clinic-$(date +%F).sql.gz
```

Run it daily with `crontab -e` (`0 3 * * * ...`), and copy a backup off the VM now and then. Restore:
`gunzip -c backup.sql.gz | $C exec -T db psql -U clinic_user clinic_ai`.

## Security checklist

- [ ] `DEV_LOGIN=0` on the server (`curl https://<site>/api/auth/config` must show `"dev_login":false`)
- [ ] New `JWT_SECRET`, `SIGNING_SECRET` and database password, different from local
- [ ] Old API keys that appeared in chats are rotated
- [ ] `ALLOWED_DOCTOR_EMAILS` contains only people who should sign in
- [ ] Only ports 22, 80 and 443 are open in the Azure network rules (the database and API are not published)
- [ ] Password login for SSH is off (Azure's default when you choose an SSH key)
- [ ] Only the **seeded demo data** is on the server. **No real patient data** on a student VM
- [ ] Budget alert is set

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Browser shows a certificate error | The DNS name in `SITE_ADDRESS` is wrong, or ports 80/443 are closed in the Azure network rules |
| Google says `redirect_uri_mismatch` | The redirect URI in Google Cloud must match `GOOGLE_REDIRECT_URI` exactly, with `https` |
| Login works but you are signed out at once | `PUBLIC_BASE_URL` is not `https`, or you opened the site by IP instead of the DNS name |
| Deploy job: `Host key verification failed` | `VM_KNOWN_HOSTS` is missing or wrong |
| Deploy job skipped | `DEPLOY_ENABLED` is not `true`, or the commit was not pushed to `main`, or tests failed |
| `deploy.sh` says rolling back | Read the logs it printed. The API did not become healthy within about two minutes |
