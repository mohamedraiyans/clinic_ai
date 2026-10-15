# Deploying Clinic AI to a server (Azure runbook)

This folder is the **server** version of the project. The **local** version (`docker-compose.yml` in the repo root,
`.env`, `localhost:5173`) is not changed by anything here, so you can run both at the same time.

```
Local (your PC)                          Server (Azure VM, or any Ubuntu server)
docker-compose.yml                       deploy/docker-compose.prod.yml
Vite dev server, hot reload              built React app served by nginx
http://localhost:5173                    https://<your-name>.<region>.cloudapp.azure.com
.env  (DEV_LOGIN=1)                      deploy/.env  (DEV_LOGIN=0)  - lives only on the server
```

**Time needed:** about 45 minutes the first time, 20 to 30 minutes once you know the steps.
**Cost:** a small VM, roughly 15 to 35 USD a month on pay-as-you-go. The portal shows the price before you create it.

## Read this first: which account to use

Use an Azure account that **you own for the long term** (a personal Microsoft account). Do not build on a university or
employer account that can be closed.

> **What happened the first time.** The first deployment worked on an "Azure for Students" subscription tied to a
> university account. Hours later the university account was closed (sign-in error `AADSTS51004: the user account does
> not exist in the directory`), the portal became unreachable, and two firewall rules that were still missing could not
> be added. The server itself kept running. Nothing in this repository was lost, and the same steps work on any new
> account or provider.

Options (check each provider's page for current terms and prices):
- **A new Azure free account** on a personal Microsoft account. At the time of writing it offers a USD 200 credit for
  30 days and 12 months of free small VMs for new customers, and needs a card and a phone number for identity checks.
  See https://azure.microsoft.com/free/ .
- **Any other Ubuntu 24.04 server** (a small VPS from any provider). Skip Part 1 and open ports 22, 80 and 443 in that
  provider's firewall. You need a DNS name that points to the server's IP address (see "Moving to another provider").

## How a change reaches the server

```
feature branch -> pull request -> tests pass -> merge to main -> "tests" workflow passes on main
              -> "deploy" workflow -> SSH to the VM -> deploy/deploy.sh <exact commit>
              -> rebuild -> health check -> (if it fails: automatic rollback to the previous commit)
              -> check the live site from outside
```

- Work on a **branch**. `main` only gets code whose tests are green.
- The deploy workflow is **off** until you create the repository variable `DEPLOY_ENABLED = true`.
- Scheduled test runs, pull requests and other branches **never** deploy. If tests fail on `main`, nothing is deployed.

## What runs on the server

| Container | What it is | Reachable from the internet? |
|---|---|---|
| `caddy` | HTTPS front door. Gets and renews a free certificate for your DNS name | **Yes** (ports 80, 443) |
| `web` | The React app built once, served by nginx | No (only through Caddy) |
| `api` | FastAPI backend, no `--reload`, no source mounted | No (only through Caddy: `/api/*`, `/verify/*`) |
| `db` | PostgreSQL 16, data in a Docker volume | No |

## Files in this folder

| File | Purpose |
|---|---|
| `docker-compose.prod.yml` | The four containers above (own project name `clinic-ai-prod`) |
| `Caddyfile`, `nginx.conf`, `web.Dockerfile` | HTTPS routing and the production web image |
| `.env.production.example` | Template for the server's settings |
| `setup-env.sh` | Creates `deploy/.env` for you: generates secrets, asks for your keys (hidden input) |
| `deploy.sh` | Updates the server to one exact commit, checks health, rolls back on failure |
| `test-setup-env.sh` | Tests `setup-env.sh` on a throw-away copy (also runs in CI) |

---

## Part 0: What you need before you start

- [ ] An Azure account you own (see above), and about 45 minutes
- [ ] A Windows, Mac or Linux computer with a terminal and `ssh` (Windows 10/11 has it in PowerShell)
- [ ] Your GitHub repository (public repositories can be cloned without any key)
- [ ] A Google OAuth client (Cloud Console, Credentials, Web application) for sign-in
- [ ] An AI key: Azure OpenAI (endpoint, key, deployment name) and/or a Groq key
- [ ] The email address(es) of the doctors who may sign in

## Part 1: Create the server in the Azure portal

1. **Resource group:** create `clinic-ai-rg` in a region your subscription allows.
2. **Virtual machine** (Create a resource, Virtual machine). Fill in the Basics tab:

   | Field | Value |
   |---|---|
   | Name | `clinic-ai-vm` |
   | Region | same as the resource group |
   | Image | **Ubuntu Server 24.04 LTS - x64 Gen2** |
   | Size | any size with **2 vCPU and 4 GB or more** (for example B2s). Building the app needs the memory. Compare the monthly price shown; a D-series costs noticeably more than a B-series |
   | Authentication | **SSH public key**, username `azureuser`, "Generate new key pair" |
   | Public inbound ports | **Allow selected ports: SSH (22), HTTP (80), HTTPS (443)** |

   Disks: Standard SSD. Networking: create a **Standard, Static** public IP and tick "Delete public IP and NIC when VM is deleted".
   Click **Review + create**, then **Create**, and choose **Download private key and create resource**.
   **The `.pem` file can be downloaded only once.** Move it to a private folder such as `C:\Users\<you>\.ssh\`,
   and never put it in the repository.

3. **DNS name:** VM, Overview, click the "DNS name" link, set a **DNS name label** (for example `clinic-ai-yourname`), Save.
   Your address is now `clinic-ai-yourname.<region>.cloudapp.azure.com`. Write it down.

4. **Open ports 80 and 443 and check them.** VM, **Networking** (Network settings). The inbound rules must list
   **SSH 22, HTTP 80 and HTTPS 443**, all Allow. **This is the step that was missed the first time**: without 80 and 443 the
   site times out and no HTTPS certificate can be issued. If a rule is missing, click **Create port rule, Inbound port rule**,
   choose Service **HTTP** (priority 310) and add it, then again for **HTTPS** (priority 320).

5. **Budget alert** (Cost Management, Budgets) and, to save credit, **Auto-shutdown** (VM, Auto-shutdown). When you are not
   demoing, press **Stop** so the VM shows "Stopped (deallocated)".

**If the portal page hangs on a blank screen:** first check whether the VM was created (bell icon, or the resource group
list) before you click Create again. Try an Incognito window, or switch off browser extensions such as ad blockers.
As a fallback, create it from **Cloud Shell** (the `>_` icon in the portal), after making a key on your own computer with
`ssh-keygen -t ed25519 -f ~/.ssh/clinic-ai-vm_key -N ""` and pasting the **public** key (`.pub`) below:

```bash
az vm create --resource-group clinic-ai-rg --name clinic-ai-vm --image Ubuntu2404 --size Standard_B2s \
  --admin-username azureuser --ssh-key-values "PASTE_THE_PUBLIC_KEY_LINE" \
  --public-ip-sku Standard --public-ip-address-allocation static \
  --os-disk-delete-option Delete --nic-delete-option Delete
az vm open-port --resource-group clinic-ai-rg --name clinic-ai-vm --port 80  --priority 900
az vm open-port --resource-group clinic-ai-rg --name clinic-ai-vm --port 443 --priority 901
```

## Part 2: Connect from your computer

**Know which window you are in.** Commands in this guide are for one of two places:
**your own PC** (a prompt like `PS C:\Users\you>`), or **the server** (a prompt like `azureuser@clinic-ai-vm:~$`).
PowerShell commands do nothing useful on the server, and Linux commands do not work in PowerShell.

On Windows, make the key file private first (Windows refuses a key other users can read). **Run each line separately:**

```powershell
$key = "C:\Users\<you>\.ssh\clinic-ai-vm_key.pem"
icacls $key /inheritance:r
icacls $key /grant:r "$($env:USERNAME):(R)"
icacls $key
```

The last command should list only your user with `(R)`. Then connect (replace the address with yours, no `<` or `>`):

```powershell
ssh -i $key azureuser@clinic-ai-yourname.<region>.cloudapp.azure.com
```

Type `yes` the first time. When the prompt becomes `azureuser@clinic-ai-vm:~$`, you are on the server.

## Part 3: Install Docker and get the code (on the server)

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit
```

Connect again (the group change needs a new login), then:

```bash
docker run --rm hello-world          # must print "Hello from Docker!"
git clone https://github.com/<your-user>/clinic_ai.git ~/clinic-ai
cd ~/clinic-ai && git log --oneline -3
```

If the repository is **private**, make a read-only deploy key first: `ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N ""`,
add `~/.ssh/github_deploy.pub` under GitHub, Settings, Deploy keys (leave "write access" off), put
`Host github.com` / `IdentityFile ~/.ssh/github_deploy` into `~/.ssh/config`, and clone with the `git@github.com:...` address.

Make sure you are on the branch that has the `deploy/` folder (normally `main`): `git branch --show-current`, then
`ls deploy`. If `deploy` is missing you are on an old commit: `git checkout main && git pull`.

## Part 4: Create the server's settings (on the server)

```bash
cd ~/clinic-ai
bash deploy/setup-env.sh clinic-ai-yourname.<region>.cloudapp.azure.com you@example.com
```

The script fills in the addresses, generates a new database password, `JWT_SECRET` and `SIGNING_SECRET`, sets
`DEV_LOGIN=0`, and then asks for your keys. **Typing is hidden, so paste and press Enter and nothing will appear.**
It accepts the value alone or the whole `NAME=value` line. For the Azure OpenAI and Groq prompts, press Enter to skip one
you do not use (you need at least one). At the end it prints three checks that show no secret. They must say
**placeholders left: 0** and **DEV_LOGIN: 0**.

**Do not** type `NAME=value` lines straight at the shell prompt. That only sets temporary variables, does not write the
file, and leaves the secret in the shell history. If you did, run `history -c; history -w`.

Manual alternative: `cp deploy/.env.production.example deploy/.env`, edit it with `nano deploy/.env`, and use new random
values from `openssl rand -hex 32`. `deploy/.env` is git-ignored and readable only by you (`chmod 600`).

**Never paste or screenshot `deploy/.env` into a chat or a ticket.** Keep a copy of the settings in a password manager.

## Part 5: Google sign-in for the server

In Google Cloud Console, Credentials, your OAuth client, **Authorized redirect URIs**, add:

```
https://clinic-ai-yourname.<region>.cloudapp.azure.com/api/auth/google/callback
```

Keep the `http://localhost:8000/...` one, so Google login works locally and on the server. If the consent screen is in
"Testing" mode, add the doctors' emails under Test users.

## Part 6: First deploy by hand (on the server)

```bash
cd ~/clinic-ai
bash deploy/deploy.sh "$(git rev-parse HEAD)"
```

The first build takes a few minutes. It must end with `Deploy OK: <commit>`. Then check:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"     # caddy, api (healthy), db (healthy), web: all Up
docker exec clinic-ai-prod-api-1 python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/api/auth/config').read().decode())"
```

The second command must print `{"google":true,"dev_login":false}`.

**Check from your own PC** (PowerShell). Ports 80 and 443 must say OPEN:

```powershell
$h = "clinic-ai-yourname.<region>.cloudapp.azure.com"
foreach ($p in 22, 80, 443) { $c = New-Object Net.Sockets.TcpClient; $ok = $c.BeginConnect($h, $p, $null, $null).AsyncWaitHandle.WaitOne(6000) -and $c.Connected; "port $p " + $(if ($ok) { "OPEN" } else { "blocked" }); $c.Close() }
```

Then open `https://<your address>`. The first visit can take a minute while Caddy gets the certificate. If you opened the
ports after the first deploy, restart Caddy so it asks again at once:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env restart caddy
docker logs clinic-ai-prod-caddy-1 --tail 5
```

Sign in with Google and test: select a patient, ask the assistant a question, sign a prescription and open the PDF.

## Part 7: Switch on continuous deployment

1. **A dedicated deploy key** (on your PC; never reuse your personal key):
   `ssh-keygen -t ed25519 -f clinic_deploy_key -N '""'` (PowerShell) and append `clinic_deploy_key.pub` to
   `~/.ssh/authorized_keys` on the server.
2. **The server's fingerprint:** `ssh-keyscan -t ed25519 <your-address>` and copy the line. Compare it with
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server.
3. GitHub, repository, **Settings, Secrets and variables, Actions**:

   | Type | Name | Value |
   |---|---|---|
   | Secret | `VM_HOST` | your DNS name |
   | Secret | `VM_USER` | `azureuser` |
   | Secret | `VM_SSH_KEY` | the whole content of `clinic_deploy_key` (the private one) |
   | Secret | `VM_KNOWN_HOSTS` | the `ssh-keyscan` line |
   | Variable | `SITE_URL` | `https://<your-address>` |
   | Variable | `DEPLOY_ENABLED` | `true` (set this **last**) |

4. Optional: **Settings, Environments, production, Required reviewers** adds a manual approval before each deploy.

## Part 8: Test the pipeline (both directions)

1. **The good path.** On a branch, change a visible word (for example the title on the login page), open a pull request,
   wait for green tests, merge. Watch **Actions**: `tests` then `deploy`. Refresh the site.
2. **The bad path.** On a branch, break a test on purpose and open a pull request: the checks go **red**, which is your
   warning not to merge. (If a red commit ever reaches `main`, `tests` is red there and **`deploy` does not run**. Revert it.)
   Do not leave a deliberately failing test on `main`.
3. **The automatic rollback** (only if you want to see it): deploy a commit that crashes the API at startup. The script
   prints the logs and returns the server to the previous commit.

---

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

The patients, prescriptions and audit log live in the `pgdata_prod` Docker volume on the server.

```bash
mkdir -p ~/backups
C="docker compose -f ~/clinic-ai/deploy/docker-compose.prod.yml --env-file ~/clinic-ai/deploy/.env"
$C exec -T db pg_dump -U clinic_user clinic_ai | gzip > ~/backups/clinic-$(date +%F).sql.gz
```

Run it daily with `crontab -e` (`0 3 * * * ...`), and copy a backup off the server now and then. Restore:
`gunzip -c backup.sql.gz | $C exec -T db psql -U clinic_user clinic_ai`.

## Moving to another provider (or a new Azure account)

Everything except Part 1 is the same:
1. Create an Ubuntu 24.04 server with 2 vCPU and 4 GB or more, and open ports **22, 80, 443** in the provider's firewall.
2. Point a DNS name at its IP address. Caddy needs a **name** (not a bare IP) to get a certificate. Without a domain, a
   free name such as `203-0-113-10.sslip.io` (the IP with dashes) can work, but shared services can hit certificate
   rate limits.
3. Do Parts 2 to 8 with the new address. Update the Google redirect URI and the GitHub secrets and variables.

## Security checklist

- [ ] `DEV_LOGIN=0` on the server (`curl https://<site>/api/auth/config` must show `"dev_login":false`)
- [ ] New `JWT_SECRET`, `SIGNING_SECRET` and database password, different from local
- [ ] Any API key that was ever pasted into a chat, ticket or screenshot is rotated
- [ ] `ALLOWED_DOCTOR_EMAILS` contains only people who should sign in
- [ ] Only ports 22, 80 and 443 are open (the database and API are not published)
- [ ] Password login for SSH is off (the default when you choose an SSH key)
- [ ] Only the **seeded demo data** is on the server. **No real patient data** on a demo or student server
- [ ] A budget alert is set, and the VM is stopped when you are not using it

## Troubleshooting (all of these happened)

| Symptom | Cause and fix |
|---|---|
| Browser: "site can't be reached", `ERR_CONNECTION_TIMED_OUT` | Ports **80/443 are not open** in Azure's Networking rules (Part 1, step 4). Check with the PowerShell port test in Part 6 |
| Caddy log: `Timeout during connect (likely firewall problem)` | Same cause. Open the ports, then restart Caddy |
| Browser shows a certificate warning right after opening the ports | Caddy is still getting the certificate. Wait a minute, restart Caddy, refresh |
| `cp: cannot stat 'deploy/.env.production.example'` or `ls: cannot access 'deploy'` | The server is on an old commit. `git checkout main && git pull` |
| `.env` opens in nano as an empty file | The `cp` failed (see the line above). Exit nano with Ctrl+X, fix the branch, copy again |
| After pasting `KEY=value` lines the file is still empty | Typed at the shell prompt, not into the script. Use `setup-env.sh`, and run `history -c; history -w` |
| `Invalid parameter "ssh"` from `icacls` | Two commands were pasted as one line. Run each line separately |
| `Identity file ... not accessible` / `=: command not found` | PowerShell commands were typed in the **server** window. Open a normal PC PowerShell |
| `Permissions ... are too open` for the key | Re-run the `icacls` lines in Part 2 |
| `unexpected character "@" in variable name` when starting Compose | The env file has Windows line endings or a broken line. Create it with `setup-env.sh` on the server |
| Deploy job: `Host key verification failed` | `VM_KNOWN_HOSTS` is missing or wrong |
| Deploy job skipped | `DEPLOY_ENABLED` is not `true`, or the commit is not on `main`, or tests failed |
| `deploy.sh` prints "Rolling back" | Read the logs it printed. The API did not become healthy in about two minutes |
| Google: `redirect_uri_mismatch` | The redirect URI in Google Cloud must match `GOOGLE_REDIRECT_URI` exactly, with `https` |
| Login works but you are signed out at once | `PUBLIC_BASE_URL` is not `https`, or you opened the site by IP instead of the DNS name |
| Azure portal sign-in: `AADSTS51004` | The account no longer exists in that directory (a closed university or employer account). Only the account's administrator can restore it. Use a personal Azure account for anything long-lived |
| Azure portal page stays blank and loading | Check whether the resource was created first. Try Incognito, disable extensions, or use Cloud Shell (Part 1) |
