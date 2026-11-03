# Security Policy

This policy applies to **Telegram Message Change Notifier** and its published source code.

## Supported versions

Security fixes target the latest code on the default `main` branch. Update to the latest commit before checking whether an issue still occurs. Older commits, forks and locally modified copies have no guaranteed security backports. This project does not claim support for numbered release series that it has not published.

## Reporting a vulnerability

Do **not** post vulnerability details, exploit code, credentials, session files, private messages or media in public issues, pull requests or discussions.

If GitHub private vulnerability reporting is enabled, use [Report a vulnerability](https://github.com/mrdv71/telegram-message-change-notifier/security/advisories/new) under this repository's **Security** tab. If that option is unavailable, open a public issue titled **Private security contact requested**, with no technical details or sensitive information, and wait for the maintainer to provide a private reporting channel.

In the private report, include:

- The affected commit, Python version and operating system.
- A concise description of the impact and affected component.
- Minimal reproduction steps using test accounts and synthetic data you control.
- Redacted logs and a suggested fix, if available.

Never include real API hashes, bot tokens, login codes, two-step verification passwords, session databases or other people's content. Do not test against accounts or systems without permission.

Reports are reviewed on a best-effort basis; no response deadline, fix deadline or bounty is promised. The maintainer may request more information, assess impact and coordinate a fix and disclosure with the reporter. Please agree on public disclosure before publishing technical details.

## Credentials and private data

Telegram session files grant account access. Treat them like passwords, including copies and backups. A `.gitignore` rule is not encryption and does not protect files already tracked by Git.

- Default private state is `state/` beside `notifier.py`, or your `--state-dir`: Telegram session, API credentials, bot token, SQLite message cache, queued alerts and downloaded attachments.
- Cached text and media are retained until you remove them; there is no automatic retention cleanup. Deleting an original Telegram message does not delete its cached copy or an alert already delivered.
- Alerts send cached message content and available attachments through Telegram's Bot API to the configured account. These copies leave the local computer; bot chats are not Secret Chats or end-to-end encrypted.
- Protect both your personal account and notification bot. Do not configure shared destinations or share the bot token.

## Safe deployment

- Use only accounts and conversations you are authorized to access and archive. Permission to receive disappearing content does not automatically include permission to retain it.
- Run as a dedicated, unprivileged user on a trusted computer or server. Do not expose state, media, logs or backups through public web servers, shared folders, public repositories or container images.
- Restrict state and output directories to the operating-system user running the tool. POSIX permission handling does not encrypt files or protect against administrators or a compromised host. On Windows, configure and check folder ACLs explicitly.
- Protect backups and disks with appropriate encryption. Stop the process before backing up, moving or deleting live state. Keep a deliberate retention policy for downloaded content and cached data.
- Keep Python and dependencies updated. Review dependency changes before deploying; offline tests are not a security audit or proof of live Telegram behavior.

## If credentials or data are exposed

1. Stop the affected process and restrict access to exposed files or hosting locations.
2. In Telegram **Settings → Devices**, terminate the affected session (or other unrecognized sessions). Deleting a local session file alone does not revoke a stolen copy.
3. If a bot token is exposed, revoke/regenerate it through the verified **@BotFather**, update the local configuration and restart only after checking the deployment.
4. Review Telegram account security and enable two-step verification if needed. It does not replace terminating compromised sessions.
5. Remove exposed files from public locations and repository history where applicable, while assuming existing clones or copies remain. Redact reports and logs; never repost exposed secrets as evidence.

For vulnerabilities in Telegram or a dependency such as Telethon, use the upstream project's security reporting process. Report any project-specific integration impact privately here as well.
