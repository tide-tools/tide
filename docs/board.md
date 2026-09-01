# The board

*Русская версия: [board.ru.md](board.ru.md)*

`tide board` raises the board out of the package: a small server on localhost
that rebuilds the projection of your control-home on every request — the home's
own stream, a stream for each project in the roster, and the work cards (when
the `work` plugin is on). The page reloads itself every 30 seconds; no database
and no cache — what's in `.tide/` is what's on the screen.

```bash
tide board                # http://127.0.0.1:8765
tide board --open         # the same, plus open the browser right away
tide board --port 9000    # the port isn't baked in — any free one
```

The server listens on localhost only (a decision, not a limitation): the board
goes outside through tailscale, not through an open port.

## From your phone

The server stays on localhost; [tailscale](https://tailscale.com) carries the
board to your phone (the free plan is enough):

1. Install Tailscale on the Mac and on the phone, sign into the same tailnet.
2. On the Mac, with `tide board` running:

   ```bash
   tailscale serve --bg 8765
   tailscale serve status       # prints https://<mac-name>.<tailnet>.ts.net
   ```

3. Open that address in the phone's browser. HTTPS and access limited to your
   own devices come from tailscale itself.

To take it down: `tailscale serve reset`.

## As a service (macOS, so the board is always up)

`tide board` is an ordinary process: close the terminal and the board goes dark.
To have it come up on login, install a launchd agent. The file
`~/Library/LaunchAgents/com.tide.board.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tide.board</string>
  <key>ProgramArguments</key><array>
    <string>/Users/YOU/.local/bin/tide</string>
    <string>board</string>
    <string>--port</string><string>8765</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>TIDE_HOME</key><string>/Users/YOU/tide-home</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/tide-board.log</string>
  <key>StandardErrorPath</key><string>/tmp/tide-board.log</string>
</dict></plist>
```

Substitute your own two paths: `which tide` gives the first, your control-home
is the second. Then:

```bash
launchctl load ~/Library/LaunchAgents/com.tide.board.plist    # install
launchctl unload ~/Library/LaunchAgents/com.tide.board.plist  # remove
```

## Where the live board joins

This board has an older sister — the live interactive board of the yard
(`tide-stack/board`: tabs, work buttons, jumping back into sessions, ~12 thousand
lines). It does NOT travel in the package: today it's nailed to one machine by
absolute paths (candidate 187) and carries surfaces a fresh install doesn't have.
The joint is sketched like this: once it's unpinned from home paths, the `tide
board` verb will learn to launch it instead of the built-in page — same address,
same command, richer page. Until then the built-in projection is an honest
minimum: you can see everything, there's just nothing to press yet.
