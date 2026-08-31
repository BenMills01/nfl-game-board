# Deploying the NFL Game Board

This folder is a **self-contained** Streamlit app (9 MB). It reads only the CSVs
bundled here — no modelling pipeline, no venvs, nothing outside this folder.

Structure (do not rename / move — the app finds data by relative path):

    deploy/game_dashboard.py     ← the app (entrypoint)
    processed/*.csv              ← projections + team/gamescript data
    external/games.csv           ← schedule + lines
    requirements.txt             ← deps
    .streamlit/config.toml       ← dark theme + headless

## Recommended host: Streamlit Community Cloud (free)

Vercel / Netlify / Cloudflare Pages will NOT work — they only run static sites and
short-lived serverless functions. Streamlit needs an always-on server holding a
WebSocket. Community Cloud is built for exactly this.

1. Create a GitHub repo — **make it Private** (this is your model / edge).
   From inside this folder:

       git init
       git add .
       git commit -m "NFL Game Board dashboard"
       git branch -M main
       git remote add origin https://github.com/<you>/<repo>.git
       git push -u origin main

2. Go to https://share.streamlit.io → "Create app" → "Deploy a public app from
   GitHub" → pick your repo, branch `main`, main file path
   `deploy/game_dashboard.py` → Deploy.

3. First build takes ~2 min. You get a URL like `https://<repo>.streamlit.app`.

### Keep it private (important — this is your betting edge)
On the free tier the app is reachable by anyone with the URL. In the app's
Settings → Sharing, set it to **"Only specific people can view"** and add your
own email(s). Combined with a Private GitHub repo, nobody else can see the app
or the data behind it.

## Refreshing each week
When you rebuild projections locally, just copy the 5 data files back into this
folder and `git add . && git commit -m "week N" && git push`. Community Cloud
auto-redeploys on push.

## Alternatives (if you want a custom domain / more control)
- **Render** / **Railway** / **Fly.io** — persistent container hosts.
  Start command: `streamlit run deploy/game_dashboard.py --server.port $PORT --server.address 0.0.0.0`

## Note on the NFL shield
The sidebar mark is a stylised shield drawn in code. The official NFL shield is a
registered trademark — fine for a private, personal tool, but if you ever make
the app public-facing, swap it for your own mark.
