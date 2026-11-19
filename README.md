# GTA VI Live Newsroom

A live tracker for GTA VI launch news, plus the first-12-hours analysis page.

## Start it

Double-click **start.command**. On first run, macOS may block it: right-click it, choose Open, then Open.

You can also start it from Terminal:

```
cd ~/Desktop/gta6-live
python3 tracker.py
```

Your browser opens the live dashboard at http://localhost:8765/live.html. Keep the Terminal window open while you use it. To stop the tracker, press Ctrl+C in that window.

## What it does

- Checks 14 sources every 60 seconds: Google News (3 searches), IGN, VGC, Eurogamer, GamesRadar+, Dexerto, Insider Gaming, Push Square, Pure Xbox, Dot Esports, The Verge and r/GTA6.
- Keeps every GTA VI story it finds, without duplicates, in `data/feed.json`. That file survives restarts.
- Flags headlines that mention a record, a peak, a first or an all-time milestone. These appear in the **Record wire** column.
- Sends a macOS notification with a sound for each new record headline.
- Refreshes the dashboard every 20 seconds. New stories flash, and the tab title shows how many new records arrived.

## Settings

Set these before the command, for example `POLL_SECONDS=30 python3 tracker.py`:

| Setting | Default | What it changes |
|---|---|---|
| `POLL_SECONDS` | 60 | How often the sources are checked |
| `PORT` | 8765 | The local web port |
| `NOTIFY` | 1 | Set to 0 to turn off notifications |

To add a source, add a line to `SOURCES` in `tracker.py`.

## Files

- `live.html`: the live dashboard
- `index.html`: the first-12-hours analysis
- `tracker.py`: the poller and local server. It uses only the Python standard library, so there is nothing to install.
- `data/feed.json`: the stored stories. It starts with the 11 sources from this morning's research.

## Hosted version (GitHub Pages)

The repo also deploys itself to GitHub Pages:

- `.github/workflows/live.yml` runs every 5 minutes. GitHub's scheduler can start runs a few minutes late.
- Each run polls the same sources with `python3 tracker.py --once`, then commits any new stories to `data/feed.json`.
- It then publishes `index.html`, `live.html` and the updated feed to Pages.
- You can trigger a run by hand: open the Actions tab, choose Poll news and deploy, then Run workflow.

The hosted dashboard is at `https://<your-username>.github.io/gta6-live/live.html`. It reads the committed feed and refreshes itself every 20 seconds.
