# Monitoring the server with Beszel

[Beszel](https://beszel.dev) shows the server's CPU, memory, disk and network in a small dashboard with history, and sends alerts (Telegram, email and others) when a value crosses a limit or the server stops reporting. It is light enough for a 2 GB server running several apps, unlike a Grafana + Prometheus stack.

This setup keeps it private: the dashboard (the **hub**) listens on `127.0.0.1:8090` and the collector (the **agent**) on `127.0.0.1:45876`, so nothing is reachable from the internet. You open the dashboard through an SSH tunnel from your own computer. No domain, certificate or nginx block is needed, and alerts still reach you, since the server sends them out.

Replace `<user>` and `<server-ip>` with your SSH login and the server's address. Every other command runs on the server.

## 1. A user and a folder for Beszel

```bash
sudo useradd --system --home-dir /opt/beszel --shell /usr/sbin/nologin beszel
sudo install -d -o beszel -g beszel -m 700 /opt/beszel
```

## 2. Download the hub and the agent

Both come from Beszel's official GitHub releases, for the server's CPU type (x86_64 or arm64):

```bash
cd /tmp && ARCH=$(uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/') && curl -sL "https://github.com/henrygd/beszel/releases/latest/download/beszel_Linux_${ARCH}.tar.gz" | tar -xz beszel && curl -sL "https://github.com/henrygd/beszel/releases/latest/download/beszel-agent_Linux_${ARCH}.tar.gz" | tar -xz beszel-agent && ls -l beszel beszel-agent
sudo install -o beszel -g beszel -m 755 /tmp/beszel /tmp/beszel-agent /opt/beszel/ && rm /tmp/beszel /tmp/beszel-agent
```

## 3. The hub as a service, on localhost only

```bash
sudo nano /etc/systemd/system/beszel.service
```

```ini
[Unit]
Description=Beszel Hub
After=network-online.target
Wants=network-online.target

[Service]
User=beszel
Group=beszel
WorkingDirectory=/opt/beszel
Environment=APP_URL=http://localhost:8090
ExecStart=/opt/beszel/beszel serve --http 127.0.0.1:8090
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/beszel

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now beszel
systemctl status beszel --no-pager
```

It should say `active (running)`. Its data (accounts, settings, history) lives in `/opt/beszel/beszel_data`.

## 4. Open the dashboard through an SSH tunnel and create your account

On **your own computer**, run this and leave it open:

```bash
ssh -N -L 8090:127.0.0.1:8090 <user>@<server-ip>
```

Open http://localhost:8090 in your browser and create the Beszel admin account with a strong password. The first person to open the page creates the admin account; through the tunnel that can only be someone who can log in to the server over SSH. Press `Ctrl+C` in the terminal to close the tunnel when you are done. If port 8090 is taken on your computer, use another local port (`-L 18090:127.0.0.1:8090`, then http://localhost:18090).

## 5. Add the server and start the agent

In Beszel click **Add System**: name it after the server, Host `127.0.0.1`, Port `45876`. Leave the dialog open and copy the **public key** and the **token** it shows.

Keep them in a file only root can read (a unit file under /etc/systemd/system can be read by every user):

```bash
sudo install -m 600 /dev/null /etc/beszel-agent.env && sudo nano /etc/beszel-agent.env
```

```ini
LISTEN=127.0.0.1:45876
HUB_URL=http://127.0.0.1:8090
KEY=ssh-ed25519 AAAA...the public key from the dialog...
TOKEN=the-token-from-the-dialog
```

```bash
sudo nano /etc/systemd/system/beszel-agent.service
```

```ini
[Unit]
Description=Beszel Agent
After=network-online.target beszel.service
Wants=network-online.target

[Service]
User=beszel
Group=beszel
EnvironmentFile=/etc/beszel-agent.env
ExecStart=/opt/beszel/beszel-agent
Restart=on-failure
RestartSec=5
StateDirectory=beszel-agent
KeyringMode=private
LockPersonality=yes
NoNewPrivileges=yes
ProtectClock=yes
ProtectHome=read-only
ProtectHostname=yes
ProtectKernelLogs=yes
ProtectSystem=strict
RemoveIPC=yes
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now beszel-agent
```

Back in the dialog click **Add System**. Within a minute the server turns **green** and the CPU, memory, disk and network graphs start filling in.

Check that neither port is open to the internet; both lines must start with `127.0.0.1`:

```bash
sudo ss -ltnp | grep -E ':8090|:45876'
```

## 6. Telegram alerts

1. In Telegram, message **@BotFather**, send `/newbot` and note the bot **token** (a bot of its own, not one another app uses).
2. Send any message to the new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and note the number in `"chat":{"id":...}`.
3. In Beszel open **Settings**, **Notifications**, add this URL (keep `@telegram` as written) and press **Test**:

   ```
   telegram://<TOKEN>@telegram?chats=<CHAT_ID>
   ```

4. Click the **bell** on the server's row and set the alerts, for example:
   - **Status**: the agent stopped reporting;
   - **Memory** above 85%, averaged over 10 minutes;
   - **Disk** above 80%;
   - **CPU** above 90%, averaged over 10 minutes.

On a small server, memory and disk are the two to watch: running out of memory kills processes, and a full disk stops every app from saving.

## 7. An uptime check from outside

Beszel runs on the server it watches, so it cannot report that the whole server is down. Add a free external check (UptimeRobot, Better Stack or similar) that opens the sites every few minutes and emails you when one does not answer.

## Day to day

| Task | Command |
|---|---|
| Open the dashboard | `ssh -N -L 8090:127.0.0.1:8090 <user>@<server-ip>` on your computer, then http://localhost:8090 |
| Memory Beszel itself uses | `systemctl status beszel beszel-agent --no-pager` (a few tens of MB together) |
| Logs | `journalctl -u beszel -u beszel-agent -n 50 --no-pager` |
| Update both | `sudo -u beszel /opt/beszel/beszel update && sudo /opt/beszel/beszel-agent update && sudo systemctl restart beszel beszel-agent` |

## Removing it

```bash
sudo systemctl disable --now beszel-agent beszel
sudo rm /etc/systemd/system/beszel.service /etc/systemd/system/beszel-agent.service /etc/beszel-agent.env
sudo systemctl daemon-reload
sudo rm -rf /opt/beszel /var/lib/beszel-agent
sudo userdel beszel
```
