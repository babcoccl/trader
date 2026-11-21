# Runbook: the Ubuntu box

Everything below runs on the home Ubuntu box unless marked "from the
Windows desktop". The stack is defined by `docker-compose.prod.yml`
alone — never combine it with the dev `docker-compose.yml`.

Set a shell variable once per session to shorten the commands:

```bash
cd /opt/trader          # wherever the repo is checked out
PROD="docker compose -f docker-compose.prod.yml"
```

```powershell
# Windows PowerShell equivalent for commands that run on the box over ssh
ssh trader-box "cd /opt/trader && docker compose -f docker-compose.prod.yml ps"
```

## Install (first time)

1. Install Docker Engine and the Compose plugin on Ubuntu
   (`docs.docker.com/engine/install/ubuntu`), then add yourself to the
   `docker` group and log back in.
2. Clone the repo, e.g. to `/opt/trader`, and `cd` into it.
3. Find the box's LAN address: `ip -4 addr show scope global`.
4. Create `.env` next to `docker-compose.prod.yml`:

   ```
   TRADER_DASHBOARD_PASSWORD=<choose a password>
   TRADER_LAN_ADDRESS=<the LAN IP from step 3>
   ```

5. Build and start:

   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```

6. Apply the schema:

   ```bash
   docker compose -f docker-compose.prod.yml exec api alembic upgrade head
   ```

7. Open `http://<LAN-IP>:3000` from the Windows desktop and log in with
   the password from step 4.
8. Install the nightly backup (see below).

## Nightly backup

`scripts/backup.sh` dumps the database (custom-format `pg_dump`) into
`$TRADER_BACKUP_DIR` — default `/var/backups/trader` — and deletes dumps
older than 14 days. Point `TRADER_BACKUP_DIR` at a directory on the
**second disk**, not the system disk:

```bash
sudo mkdir -p /mnt/data/trader-backups
sudo chown "$USER" /mnt/data/trader-backups
echo 'TRADER_BACKUP_DIR=/mnt/data/trader-backups' | sudo tee -a /etc/environment
```

```powershell
# Windows PowerShell equivalent
# -t allocates a terminal so sudo can prompt for its password
ssh -t trader-box 'sudo mkdir -p /mnt/data/trader-backups && sudo chown $USER /mnt/data/trader-backups'
```

Cron entry (`crontab -e`), nightly at 03:17. The log goes in your home
directory — `/var/log` is not writable by a non-root account:

```
17 3 * * * TRADER_BACKUP_DIR=/mnt/data/trader-backups /opt/trader/scripts/backup.sh >> $HOME/trader-backup.log 2>&1
```

## Upgrade

```bash
cd /opt/trader
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec api alembic upgrade head
docker compose -f docker-compose.prod.yml ps   # all "running"
```

```powershell
# Windows PowerShell equivalent
ssh trader-box "cd /opt/trader && git pull && docker compose -f docker-compose.prod.yml up -d --build && docker compose -f docker-compose.prod.yml exec -T api alembic upgrade head && docker compose -f docker-compose.prod.yml ps"
```

## Reboot behaviour

`restart: unless-stopped` on every service plus Docker's autostart bring
the whole stack back after a reboot or power cut with no manual steps.
Verify with `$PROD ps` and the status strip on the dashboard — all
heartbeats should go green within a couple of minutes. A service that
starts before Postgres is ready restarts itself until it is.

## Restore a backup

Verify a dump without touching live data (restores into a scratch
database named `trader_restore`):

```bash
scripts/restore.sh /mnt/data/trader-backups/trader-20261122T031700Z.dump
```

```powershell
# Windows PowerShell equivalent
ssh trader-box "cd /opt/trader && scripts/restore.sh /mnt/data/trader-backups/trader-20261122T031700Z.dump"
```

Real restore into the live `trader` database — **destroys current data**:

```bash
docker compose -f docker-compose.prod.yml stop api collector engine executor watchdog
scripts/restore.sh /mnt/data/trader-backups/trader-20261122T031700Z.dump trader
docker compose -f docker-compose.prod.yml up -d
```

```powershell
# Windows PowerShell equivalent
ssh trader-box "cd /opt/trader && docker compose -f docker-compose.prod.yml stop api collector engine executor watchdog && scripts/restore.sh /mnt/data/trader-backups/trader-20261122T031700Z.dump trader && docker compose -f docker-compose.prod.yml up -d"
```

## Halt, resume, flatten from the command line

The API is only reachable on the LAN address, so run these on the box
against `http://<LAN-IP>:3000` or from the Windows desktop.

```bash
LAN=http://192.168.1.50:3000   # your LAN IP
curl -s -c /tmp/tj -X POST $LAN/api/login \
    -H 'Content-Type: application/json' \
    -d '{"password":"<dashboard password>"}'
curl -s -b /tmp/tj -X POST $LAN/api/controls/halt \
    -H 'Content-Type: application/json' -d '{"reason":"manual halt"}'
# resume:
curl -s -b /tmp/tj -X POST $LAN/api/controls/resume
# flatten (sells everything the executor can):
curl -s -b /tmp/tj -X POST $LAN/api/controls/flatten \
    -H 'Content-Type: application/json' -d '{"confirm":"FLATTEN"}'
```

```powershell
# Windows PowerShell equivalent
$LAN = "http://192.168.1.50:3000"
Invoke-RestMethod -SessionVariable s -Method Post "$LAN/api/login" `
    -ContentType 'application/json' -Body '{"password":"<dashboard password>"}'
Invoke-RestMethod -WebSession $s -Method Post "$LAN/api/controls/halt" `
    -ContentType 'application/json' -Body '{"reason":"manual halt"}'
Invoke-RestMethod -WebSession $s -Method Post "$LAN/api/controls/resume"
Invoke-RestMethod -WebSession $s -Method Post "$LAN/api/controls/flatten" `
    -ContentType 'application/json' -Body '{"confirm":"FLATTEN"}'
```

If the API itself is down but Postgres is up, halt by writing the
setting directly:

The risk engine reads the `risk.halted` and `risk.halt_reason` settings
keys (`src/trader/risk/engine.py`); the values are JSONB, so quote them
as `'true'::jsonb` and `'"reason"'::jsonb`:

```bash
docker compose -f docker-compose.prod.yml exec db \
    psql -U trader -c \
    "INSERT INTO settings (key, value, updated_at)
     VALUES ('risk.halted', 'true'::jsonb, now()),
            ('risk.halt_reason', '\"manual halt\"'::jsonb, now())
     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();"
```

```powershell
# Windows PowerShell equivalent
ssh trader-box 'cd /opt/trader && docker compose -f docker-compose.prod.yml exec -T db psql -U trader -c "INSERT INTO settings (key, value, updated_at) VALUES (''risk.halted'', ''true''::jsonb, now()), (''risk.halt_reason'', ''\"manual halt\"''::jsonb, now()) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();"'
```

## Alarms: what each one means

Alarms come from the `watchdog` service (`src/trader/ops/watchdog.py`)
and appear in Activity and turn the status strip red. Each clears
automatically when the condition ends.

| Alarm | Meaning | First thing to check |
|---|---|---|
| `service_silent:<name>` | Named service wrote no heartbeat for `silence_alarm_minutes` (default 5). | `$PROD ps` and `$PROD logs <name>` — it crashed or is stuck. |
| `daily_decision_missing` | No daily decision rows after 00:15 UTC. | `engine` logs; the 00:05 UTC cycle did not run. |
| `intraday_stale` | No intraday decision in the last 15 minutes. | `engine` logs; the 5-minute cycle is down. |
| `candle_stale:<symbol>` | Newest 5-minute candle for the symbol is over 10 minutes old. | `collector` logs; the venue feed or network is down. |
| `balances_mismatch` | Ledger balances differ from the venue adapter's balances (or the ledger cannot be replayed). | Do not trade. Check `executor` logs, then reconcile per spec 2.4. |

The watchdog also writes a `heartbeat_summary` event every 3 hours with
the count of price checks and errors.

## Dashboard binding and scope

The dashboard binds to `TRADER_LAN_ADDRESS` only: phones and desktops on
the home network reach it at `http://<LAN-IP>:3000`; nothing is exposed
outside the LAN (remote access is a later phase). `api` and `db` publish
no host ports at all — `api` is reachable only through the `web`
container's `/api` proxy.
