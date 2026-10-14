# Linux server setup — Telegram Message Change Notifier

Use this guide for a **new installation on an always-on Linux server**. For a Mac, laptop, or Windows PC, use the [local setup guide](README.md).

## Before you start

- Use a persistent server with **systemd**, Python **3.10+** (3.12 recommended), internet access, and disk space for saved data. The package commands below target Ubuntu 24.04 / Debian 12 or newer.
- Connect over SSH as a **normal non-root user with sudo access**. Run all steps as that same user. Use sudo only where shown.
- Complete [README step 1: Telegram credentials](README.md#1-get-your-telegram-credentials) first.
- No domain or inbound application port is needed; allow outbound Telegram/HTTPS connections. Do not close your SSH access.
- GitHub, sleeping web hosts, and temporary disks do not provide suitable 24/7 hosting. Disk usage grows; there is no automatic retention cleanup.

Run commands **one line at a time**. If one fails, fix it before continuing. Keep the paths below unchanged for the easiest setup.

## 1. Install requirements

```sh
sudo apt update
sudo apt install -y python3 python3-venv git
python3 --version
systemctl --user status --no-pager
```

Python must be 3.10 or newer. If systemctl says it cannot connect to the user bus, ask the server administrator to enable a systemd user session before continuing. Do not replace `systemctl --user` with `sudo systemctl`.

## 2. Download and install

```sh
mkdir -p "$HOME/apps"
cd "$HOME/apps"
git clone https://github.com/mrdv71/telegram-message-change-notifier.git
cd telegram-message-change-notifier
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

If the repository already exists here, enter it instead of cloning again. For updates, use the separate update section below.

## 3. Sign in once, then test in the foreground

```sh
.venv/bin/python notifier.py --state-dir "$HOME/.local/share/telegram-message-change-notifier" setup
```

Complete the interactive prompts in SSH. Then start:

```sh
.venv/bin/python notifier.py --state-dir "$HOME/.local/share/telegram-message-change-notifier" run
```

Wait for `Watching incoming private chats`. From a second human account, send a new private text message to the signed-in account, wait a few seconds, edit it, wait for the edit alert, then delete it. Your bot should send edit and deletion alerts. Make sure you pressed Start in that bot from the signed-in account.

**Press Ctrl+C before continuing.** Do not run the foreground program and service at the same time. If the live test fails, fix it before enabling the service; see [troubleshooting](README.md#if-something-fails).

## 4. Enable automatic startup

The [included service](deploy/telegram-message-change-notifier.service) uses the exact app and data paths above. If you change paths, update the service copy too. The unit contains no credentials.

```sh
cd "$HOME/apps/telegram-message-change-notifier"
mkdir -p "$HOME/.config/systemd/user"
cp deploy/telegram-message-change-notifier.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
sudo loginctl enable-linger "$USER"
systemctl --user enable --now telegram-message-change-notifier.service
systemctl --user status telegram-message-change-notifier.service --no-pager
```

Expected status: **active (running)**. Lingering lets the service run after SSH logout and start after a server reboot. The service restarts after process exit, but cannot fix invalid credentials, a full disk, or unavailable Telegram content.

## 5. Verify background operation

Repeat the live test from step 3, then inspect recent logs:

```sh
journalctl --user -u telegram-message-change-notifier.service -n 50 --no-pager
```

Disconnect SSH, reconnect, and repeat the status check and live test. After your next planned server reboot, repeat them again. `active (running)` alone does not prove media capture or bot delivery. Logs can include private paths or message identifiers; do not publish them unchanged.

## Daily commands

These commands can be run from any folder. Choose the one you need; do not run this entire block as a setup step.

```sh
# Check status
systemctl --user status telegram-message-change-notifier.service --no-pager
# Stop
systemctl --user stop telegram-message-change-notifier.service
# Start
systemctl --user start telegram-message-change-notifier.service
# Restart
systemctl --user restart telegram-message-change-notifier.service
# Disable automatic startup and stop now (keeps private data)
systemctl --user disable --now telegram-message-change-notifier.service
```

## Update an existing server installation

Review upstream changes first. Stop on any error; do not continue to later commands after a failed pull or install. If you customized the installed service, merge service changes manually instead of overwriting it.

```sh
cd "$HOME/apps/telegram-message-change-notifier"
systemctl --user stop telegram-message-change-notifier.service
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
```

For the unchanged default service, refresh its copy and start again:

```sh
cp deploy/telegram-message-change-notifier.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user start telegram-message-change-notifier.service
systemctl --user status telegram-message-change-notifier.service --no-pager
```

Repeat the live test. Updating does not require fresh Telegram setup.

## Data and moving to another machine

- Server login and private state: `~/.local/share/telegram-message-change-notifier/`.
- Cached messages, media, and queued notifications are also inside that state directory.
- Local README setup uses `state/` inside the repository instead.
- Always use the **same `--state-dir` for setup and running**. Changing it does not migrate your login or data. The setup success message shows a generic run command; on this server use the full command in step 3, including `--state-dir`.
- Stop the process before backing up private state. Protect backups; never upload sessions, credentials, or cached messages to GitHub.
- On a new machine, use fresh login and stop the old collector before switching. Never run copies of one session simultaneously on different hosts.

## Remove the service (optional)

First run the disable command above. Remove only `~/.config/systemd/user/telegram-message-change-notifier.service`, then run `systemctl --user daemon-reload`. This does not delete private data. Do not disable lingering if your other services need it.

## What is verified

Offline tests and CLI checks do not sign in to Telegram. Real capture/delivery, Windows behavior, and Linux startup/reboot must be checked on the chosen host. This guide does not guarantee uptime or recovery of expired media or unobserved edits/deletions. See the [README limits](README.md) for details.
