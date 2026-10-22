# Scheduling daily snapshots

`social snapshot` is idempotent. Running it more than once a day updates rather than
duplicates, and a failed fetch never overwrites good data. It exits with code **2** if any
platform failed, so schedulers can alert you.

Find the full path of the command with `which social` (macOS/Linux) or `where social` (Windows).

## macOS / Linux: cron

```cron
# Every day at 07:15 local time
15 7 * * * /home/you/.local/bin/social snapshot >> ~/.socialledger/snapshot.log 2>&1
```

Linux cron jobs often can't reach the desktop keychain (Secret Service). If `social status`
works in your terminal but cron fails with a keychain error, use the encrypted-file store:
set `SOCIALLEDGER_SECRET_STORE=file` and `SOCIALLEDGER_TOKEN_PASSPHRASE` in the crontab
environment, then run `social connect` once with the same variables.

## macOS: launchd

Save as `~/Library/LaunchAgents/io.socialledger.snapshot.plist`, then run
`launchctl load ~/Library/LaunchAgents/io.socialledger.snapshot.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.socialledger.snapshot</string>
  <key>ProgramArguments</key><array>
    <string>/Users/you/.local/bin/social</string><string>snapshot</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>/tmp/socialledger-snapshot.log</string>
  <key>StandardErrorPath</key><string>/tmp/socialledger-snapshot.log</string>
</dict></plist>
```

launchd agents run in your login session, so they can use the macOS Keychain.

## Windows: Task Scheduler

```powershell
$action  = New-ScheduledTaskAction -Execute "$env:USERPROFILE\.local\bin\social.exe" -Argument "snapshot"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:15am
Register-ScheduledTask -TaskName "socialledger snapshot" -Action $action -Trigger $trigger
```

Run the task as your own user ("Run only when user is logged on"), so it can use Windows
Credential Manager.

## GitHub Actions

Only do this in a **private** repository. Bear in mind that the snapshot database then lives
in GitHub (as a cache or artifact), not on your machine.

1. Store the tokens as encrypted repository secrets.
2. Use the encrypted-file store: `SOCIALLEDGER_SECRET_STORE=file`, with
   `SOCIALLEDGER_TOKEN_PASSPHRASE` as a secret.
3. Restore and save `SOCIALLEDGER_HOME` with `actions/cache` or an artifact between runs.

A complete example workflow will be added before the 0.1.0 release.
