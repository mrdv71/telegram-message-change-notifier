# Choose where to run

You choose **your own computer** or an **always-on Linux server/VPS**.
GitHub hosts the source, not a running collector. No hosted service or shared
Telegram credentials are included. Each person signs in with their own account.
Use one running process per state directory; never share a Telegram session
between machines. Prefer fresh login on each machine and stop the old collector
before switching hosts to avoid duplicate capture/notifications.

## Option A: Mac, Linux laptop, or Windows PC

Follow the [README setup](README.md). No server, Docker, or background service
is required. Keep the terminal open and the computer awake and online.
Ctrl+C stops the process without logging out. Sleep, shutdown, or losing the
network interrupts capture. These instructions do not install anything on boot.
On macOS, optionally prefix the run command with `caffeinate -i` to prevent
idle sleep while it runs; closing the lid can still put a laptop to sleep.
Windows commands are in the README; Windows runtime remains unverified.

## Option B: Linux server with systemd (optional 24/7 service)

Use a persistent VM/VPS with outbound HTTPS/Telegram connectivity, Python 3.10+
(Python 3.12 recommended), Git, and Python venv support. No inbound port,
domain, browser, desktop, or public web endpoint is required. Sleeping/free
web-worker platforms and ephemeral disks are unsuitable. Disk use grows with
retained media/cache; monitor free space. This is not a storage-bounded service.

Use a normal, non-root account over SSH. Install Python/Git/venv with your
OS package manager first. The bundled unit assumes this exact clone location:

```sh
mkdir -p "$HOME/apps"
cd "$HOME/apps"
git clone https://github.com/mrdv71/telegram-message-change-notifier.git
cd telegram-message-change-notifier
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python notifier.py --state-dir "$HOME/.local/share/telegram-message-change-notifier" setup
.venv/bin/python notifier.py --state-dir "$HOME/.local/share/telegram-message-change-notifier" run
```

Complete interactive login in SSH, never in the background service.
Obtain your own API ID/hash and BotFather bot token; press Start in your bot from the same Telegram account before setup.
Send a suitable test message from another account you control, check the result,
then press Ctrl+C before starting the service. For the notifier, also edit/delete
a test message after it has been cached. Offline tests cannot prove live delivery.

### Enable automatic start and restart

Read `deploy/telegram-message-change-notifier.service` first. If you chose different paths,
edit a copy of the unit and keep its state path identical to your login command.
The unit contains no credentials. Do not run the foreground command and service
at the same time.

```sh
mkdir -p "$HOME/.config/systemd/user"
cp deploy/telegram-message-change-notifier.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
sudo loginctl enable-linger "$USER"
systemctl --user enable --now telegram-message-change-notifier.service
systemctl --user status telegram-message-change-notifier.service
journalctl --user -u telegram-message-change-notifier.service -n 50 --no-pager
```

Lingering allows this user's services to start at boot and survive SSH logout;
it requires server administrator access. Run `systemctl --user` as that same
normal user, not with sudo. If a user service manager is unavailable, ask your
server administrator to configure it; this unit is for Linux/systemd only.
Automatic restarts do not fix expired authorization, disk exhaustion, or invalid
bot settings. Inspect logs if it repeatedly restarts. Logs can contain private
file paths/message identifiers; do not publish them.

After reboot, reconnect and check `systemctl --user status` plus another live
test. Service-active status alone does not establish successful Telegram capture.

### Stop, restart, update, uninstall

```sh
systemctl --user stop telegram-message-change-notifier.service
# After inspecting upstream changes; keep your state and virtualenv untracked:
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
systemctl --user start telegram-message-change-notifier.service
# Later, to disable background operation (keeps all private data):
systemctl --user disable --now telegram-message-change-notifier.service
```

To remove the unit, remove only its copy from `~/.config/systemd/user/` and run
`systemctl --user daemon-reload`. Do not disable lingering if other services use it.
Manual `stop`/`disable` does not trigger automatic restart.

## Private persistent data

The server commands use `~/.local/share/telegram-message-change-notifier/` for credentials,
session and SQLite cache, media and delivery queue.
Media stays inside the selected state directory.
Local README commands keep their existing default paths. Changing `--state-dir`
does not migrate old state: use the same path for setup and every run.
Stop the service before backing up private state, protect backups, and never
commit/upload them. Revoking the session in Telegram Settings → Devices removes
its account access. No existing local installation is read or migrated.

## Verification boundary

The release is checked with offline Python tests and CLI checks. Linux service
files are supplied as deployment templates; real systemd startup/reboot, live
Telegram capture/delivery, and Windows/macOS sleep behavior must be verified by
the operator. Unavailable/expired media and edits/deletions missed while offline
remain subject to the limitations in the README. No uptime guarantee is implied.
