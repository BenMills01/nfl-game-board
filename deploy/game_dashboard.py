#!/usr/bin/env python3
"""
deploy/game_dashboard.py — the GAME-NARRATIVE board (v1, from scratch).
Week-by-week -> games ordered by kickoff -> click a game -> a full data-driven match
report (projected result, game flow, team identities, who benefits and WHY), team
projections, line-threshold bet calls ("OVER if <= X, UNDER if >= Y, else no bet"),
an interactive injury-impact tool, and edge highlights.

Pure presentation over precomputed files (runs in .venv_dashboard, no engine):
  processed/projected_slate_calibrated.csv   (calibrated per-player projections)
  processed/team_gamescript.csv              (per-team pass-rate curve + identity)
  processed/timeinstate.csv                  (spread -> time-in-state)
  external/games.csv                         (schedule, spread, total, kickoff, roof)

    .venv_dashboard/bin/streamlit run deploy/game_dashboard.py
"""
import os
import re
import numpy as np
import pandas as pd
import streamlit as st


def md_to_html(md):
    """Render the report's light markdown as styled HTML inside .report."""
    out = []
    for block in md.split("\n\n"):
        b = block.strip()
        if not b:
            continue
        b = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", b)
        b = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", r"<em>\1</em>", b)
        if b.startswith("### "):
            out.append(f"<h3>{b[4:]}</h3>")
        elif b.startswith("## "):
            out.append(f"<h2>{b[3:]}</h2>")
        else:
            out.append(f"<p>{b}</p>")
    return '<div class="report">' + "".join(out) + "</div>"

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
PROC = os.path.join(ROOT, "processed")
EXT = os.path.join(ROOT, "external")
SEASON = 2026
STATES = ["trail14+", "trail", "close", "lead", "lead14+"]
# betting markets: label -> (stat key, quantile prefix, unit)
MARKETS = {
    "QB": [("Pass yards", "pass_yards"), ("Pass TDs", "pass_td")],
    "RB": [("Rush yards", "rush_yards"), ("Carries", "rush_att"),
           ("Rec yards", "rec_yards"), ("Receptions", "receptions")],
    "WR": [("Rec yards", "rec_yards"), ("Receptions", "receptions"), ("Targets", "targets")],
    "TE": [("Rec yards", "rec_yards"), ("Receptions", "receptions"), ("Targets", "targets")],
}
POS_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
# standard fractional-odds ladder (fraction, decimal payout incl. stake)
FRAC = [("1/5", 1.20), ("2/9", 1.22), ("1/4", 1.25), ("2/7", 1.29), ("1/3", 1.33),
        ("4/11", 1.36), ("2/5", 1.40), ("4/9", 1.44), ("1/2", 1.50), ("8/15", 1.53),
        ("4/7", 1.57), ("8/13", 1.62), ("4/6", 1.67), ("8/11", 1.73), ("4/5", 1.80),
        ("5/6", 1.83), ("10/11", 1.91), ("EVS", 2.00), ("11/10", 2.10), ("6/5", 2.20),
        ("5/4", 2.25), ("11/8", 2.38), ("6/4", 2.50), ("13/8", 2.63), ("7/4", 2.75),
        ("15/8", 2.88), ("2/1", 3.00), ("9/4", 3.25), ("5/2", 3.50), ("11/4", 3.75),
        ("3/1", 4.00), ("10/3", 4.33), ("7/2", 4.50), ("4/1", 5.00), ("9/2", 5.50),
        ("5/1", 6.00), ("11/2", 6.50), ("6/1", 7.00), ("13/2", 7.50), ("7/1", 8.00),
        ("8/1", 9.00), ("9/1", 10.0), ("10/1", 11.0), ("12/1", 13.0), ("14/1", 15.0),
        ("16/1", 17.0), ("20/1", 21.0)]


def prob_to_frac(p):
    """Model P(event) -> the FAIR fractional odds (the minimum acceptable price).
    Snap fair decimal (1/p) UP to the next standard fraction, so 'bet at this or
    longer' is a true +EV threshold."""
    if p <= 0:
        return "—", 999.0
    fair = 1.0 / p
    for frac, dec in FRAC:
        if dec >= fair - 1e-9:
            return frac, dec
    return FRAC[-1]


# team accent colors, each brightened to stay legible on the dark broadcast bg
TEAM_COLORS = {
    "ARI": "#E64868", "ATL": "#FF525B", "BAL": "#9E86FF", "BUF": "#4C8DFF",
    "CAR": "#3AC7F0", "CHI": "#F1873B", "CIN": "#FB4F14", "CLE": "#FF7A45",
    "DAL": "#7FB0E8", "DEN": "#FB6A28", "DET": "#4FA8E8", "GB": "#FFD84D",
    "HOU": "#F2405A", "IND": "#4F9BE8", "JAX": "#19D3C5", "KC": "#FF4D57",
    "LV": "#C9D2DC", "LAC": "#3AC7F0", "LAR": "#5C93F0", "MIA": "#2CD3C6",
    "MIN": "#A06BE8", "NE": "#7FA8C9", "NO": "#D9BE72", "NYG": "#5C8FF0",
    "NYJ": "#2FC98A", "PHI": "#3FB7B0", "PIT": "#FFD84D", "SF": "#E8506A",
    "SEA": "#69BE28", "TB": "#FF5A4D", "TEN": "#4FC3E8", "WAS": "#F2C15A",
}


def tcol(team):
    return TEAM_COLORS.get(str(team).upper(), "#c9d5e0")


def shield_svg(w=44):
    """Self-contained inline shield mark (navy/red/stars/football) — no asset file."""
    h = int(w * 1.28)
    return f"""<svg width="{w}" height="{h}" viewBox="0 0 100 128" xmlns="http://www.w3.org/2000/svg">
  <path d="M50 3 C62 3 78 6 92 11 C93 42 89 80 50 124 C11 80 7 42 8 11 C22 6 38 3 50 3 Z"
        fill="#013369" stroke="#ffffff" stroke-width="4"/>
  <path d="M14 64 L86 64 C81 91 68 110 50 122 C32 110 19 91 14 64 Z" fill="#ffffff"/>
  <g transform="rotate(-32 50 31)">
    <ellipse cx="50" cy="31" rx="14.5" ry="8.2" fill="#ffffff"/>
    <line x1="42" y1="31" x2="58" y2="31" stroke="#013369" stroke-width="1.5"/>
    <line x1="46" y1="28.6" x2="46" y2="33.4" stroke="#013369" stroke-width="1.5"/>
    <line x1="50" y1="28.2" x2="50" y2="33.8" stroke="#013369" stroke-width="1.5"/>
    <line x1="54" y1="28.6" x2="54" y2="33.4" stroke="#013369" stroke-width="1.5"/>
  </g>
  <g fill="#ffffff" font-family="Arial,sans-serif" font-size="10" text-anchor="middle">
    <text x="19" y="24">★</text><text x="24" y="40">★</text>
    <text x="81" y="24">★</text><text x="76" y="40">★</text>
  </g>
  <text x="50" y="99" fill="#D50A0A" font-family="'Saira Condensed',Arial,sans-serif"
        font-weight="800" font-size="33" text-anchor="middle" letter-spacing="-1.5">NFL</text>
</svg>"""


def inject_css():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600;700&family=Saira+Condensed:wght@600;700;800&family=Newsreader:opsz,ital,wght@6..72,0,400;6..72,0,500;6..72,1,400&family=IBM+Plex+Mono:wght@500;600&display=swap');
    :root {
      --accent:#ff414d; --accent-dim:#c9313b; --gold:#f4c430;
      --ink:#c9d5e0; --headline:#f3f7fb; --muted:#8a9cae; --steel:#a7b8c8;
      --panel:#131b25; --panel-2:#0e141c; --line:#243140; --line-soft:#1b2530;
    }
    html, body, [class*="css"], .stApp { font-family: 'Newsreader', Georgia, serif; }
    .stApp { background:
        radial-gradient(1100px 520px at 50% -14%, #17212c 0%, #0a0d12 60%),
        repeating-linear-gradient(90deg, rgba(255,255,255,.014) 0 1px, transparent 1px 84px) #0a0d12; }
    .block-container { max-width: 1000px; padding-top: 2rem; }
    h1,h2,h3,h4 { font-family:'Oswald', system-ui, sans-serif !important;
        text-transform:uppercase; letter-spacing:.03em; font-weight:600; }
    /* hero — the scorebug */
    .hero { position:relative; background: linear-gradient(165deg,#18222e,#0d141c); border:1px solid var(--line);
            border-radius:16px; padding:24px 30px 22px; margin-bottom:22px; overflow:hidden;
            box-shadow:0 14px 44px rgba(0,0,0,.42); }
    .hero:before { content:""; position:absolute; top:0; left:0; right:0; height:4px;
            background:linear-gradient(90deg, var(--ca,#2b3a49) 0%, transparent 32% 68%, var(--cb,#2b3a49) 100%); }
    .hero .eyebrow { font-family:'Oswald',sans-serif; font-size:11px; font-weight:600; letter-spacing:.26em;
            text-transform:uppercase; color:var(--muted); }
    .score { display:flex; align-items:center; justify-content:center; gap:30px; margin:14px 0 18px; }
    .score .tm { text-align:center; position:relative; padding:0 6px; }
    .score .abbr { font-family:'Saira Condensed',sans-serif; font-weight:800; font-size:30px; letter-spacing:.02em; line-height:1; }
    .score .pts { font-family:'Saira Condensed',sans-serif; font-size:64px; font-weight:700; line-height:.92; letter-spacing:-.01em; }
    .score .tm.fav .pts:after { content:""; display:block; height:3px; width:34px; margin:6px auto 0;
            background:var(--accent); border-radius:2px; }
    .score .at { font-family:'Oswald',sans-serif; color:#54657a; font-weight:600; font-size:16px; padding-top:16px; }
    .chips { display:flex; gap:9px; justify-content:center; flex-wrap:wrap; }
    .chip { background:var(--panel-2); border:1px solid var(--line); border-radius:6px; padding:5px 13px;
            font-family:'IBM Plex Mono',monospace; font-size:12.5px; color:#aebbc9; text-transform:uppercase; letter-spacing:.03em; }
    .chip b { color:var(--accent); font-weight:600; }
    /* article */
    .report h2 { font-size:23px; margin:28px 0 8px; color:var(--headline); letter-spacing:.02em; }
    .report h3 { font-size:15px; letter-spacing:.12em; color:var(--accent);
                 border-bottom:2px solid var(--line); padding-bottom:7px; margin:28px 0 12px; }
    .report p { font-family:'Newsreader',Georgia,serif; font-size:17.5px; line-height:1.74; color:var(--ink); margin:0 0 14px; text-transform:none; }
    .report strong { color:var(--headline); font-weight:500; }
    .report em { color:var(--steel); font-style:italic; }
    /* section labels */
    .seclabel { font-family:'Oswald',sans-serif; font-weight:600; font-size:13px; letter-spacing:.16em;
                text-transform:uppercase; color:var(--accent); margin:30px 0 10px;
                border-left:3px solid var(--accent); padding-left:10px; }
    /* dataframe polish */
    [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; }
    /* game-list buttons — scorebug rows */
    div.stButton>button { text-align:left; background:linear-gradient(100deg,#121a24,#0e141c); border:1px solid var(--line);
        border-left:3px solid var(--line); border-radius:10px; padding:15px 18px;
        font-family:'Oswald',sans-serif !important; font-weight:500; text-transform:uppercase; letter-spacing:.03em;
        color:#dbe6f0; transition:.14s; }
    div.stButton>button:hover { border-color:var(--accent); border-left-color:var(--accent);
        background:linear-gradient(100deg,#17212d,#111823); color:#fff; transform:translateX(3px); }
    .stApp .stRadio label, .stApp label { font-family:'Oswald',sans-serif; text-transform:uppercase; letter-spacing:.05em; }
    #MainMenu, footer, header [data-testid="stToolbar"] { visibility:hidden; }
    </style>
    """, unsafe_allow_html=True)


def hero_html(grow):
    home, away = grow.home_team, grow.away_team
    hs = grow.spread_line
    hpts, apts = score_proj(grow.total_line, hs)
    hfav, afav = hs > 0, hs < 0
    kickoff = f"{pd.to_datetime(grow.gameday).strftime('%a %b %-d')} · {grow.gametime}"
    fav = home if hs > 0 else away
    roof = str(grow.get("roof", "") or "").title()
    ca, cb = tcol(away), tcol(home)
    return f"""
    <div class="hero" style="--ca:{ca};--cb:{cb}">
      <div class="eyebrow" style="text-align:center">Week {int(grow.week)} · Projected Final</div>
      <div class="score">
        <div class="tm {'fav' if afav else 'dog'}"><div class="pts" style="color:{ca}">{apts:.0f}</div><div class="abbr" style="color:{ca}">{away}</div></div>
        <div class="at">AT</div>
        <div class="tm {'fav' if hfav else 'dog'}"><div class="pts" style="color:{cb}">{hpts:.0f}</div><div class="abbr" style="color:{cb}">{home}</div></div>
      </div>
      <div class="chips">
        <span class="chip">{kickoff}</span>
        <span class="chip"><b>{fav}</b> −{abs(hs):.1f}</span>
        <span class="chip">O/U <b>{grow.total_line:.1f}</b></span>
        {'<span class="chip">'+roof+'</span>' if roof else ''}
      </div>
    </div>
    """


@st.cache_data
def load():
    slate = pd.read_csv(os.path.join(PROC, "projected_slate_calibrated.csv"))
    games = pd.read_csv(os.path.join(EXT, "games.csv"))
    games = games[games.season == SEASON].copy()
    ts = pd.read_csv(os.path.join(PROC, "team_gamescript.csv")).set_index("team")
    tis = pd.read_csv(os.path.join(PROC, "timeinstate.csv")).set_index("sr")
    return slate, games, ts, tis


@st.cache_data
def load_teams():
    p = os.path.join(PROC, "team_profiles.csv")
    if not os.path.exists(p):
        return None
    d = pd.read_csv(p)
    return d.set_index("team")


def _cmp(v, lg, unit="", inv=False):
    """phrase a value vs league (inv=True when lower is 'more')."""
    diff = v - lg
    hi = "above" if diff > 0 else "below"
    return f"{v:.0f}{unit} ({abs(diff):.0f}{unit} {hi} the league's {lg:.0f}{unit})"


def _pick(seed, variants):
    return variants[hash(seed) % len(variants)]


def _nm(p, fallback="their top back"):
    return f"**{p.player_name}**" if p is not None else fallback


def team_insights(team, r, lg, qb, rb, wr, te):
    """Build ranked, specific insights; each returns (distinctiveness, text)."""
    ins = []
    proe = r["proe"] - lg["proe"]
    scr = r["scramble_pct"] - lg["scramble_pct"]
    orun = r["outside_run_pct"] - lg["outside_run_pct"]
    pace = r["plays_pg"] - lg["plays_pg"]
    adot = r["adot"] - lg["adot"]
    cc = r["carry_RB1"] - lg["carry_RB1"]
    wc = r["tgt_WR1"] - lg["tgt_WR1"]
    sr = r["success_rate"] - lg["success_rate"]

    if proe <= -3:
        ins.append((abs(proe) + 3, _pick(team, [
            f"Run identity is the through-line here: {team} call pass on **{r['proe']:+.0f}%** of snaps relative "
            f"to what the situation expects (league {lg['proe']:+.0f}%), and when they get ahead they lean even "
            f"harder on the ground — just {r['pass_lead']:.0f}% pass on early downs with a lead. This is a "
            f"ball-control operation built to shorten games.",
            f"Few offenses are as committed to the run as {team}. Their pass rate over expected sits at "
            f"**{r['proe']:+.0f}%** — well below the {lg['proe']:+.0f}% norm — and it collapses to {r['pass_lead']:.0f}% "
            f"once they're in front. The script writes itself: build a lead, then bludgeon."])))
    elif proe >= 1:
        ins.append((proe + 3, _pick(team, [
            f"{team} throw by design. They pass **{r['proe']:+.0f}%** over expectation (league {lg['proe']:+.0f}%) "
            f"and keep firing even with a lead ({r['pass_lead']:.0f}% early-down pass when ahead vs ~41%) — the kind "
            f"of pass-first identity that keeps receivers live in every script.",
            f"This is an aggressive, pass-leaning attack: **{r['proe']:+.0f}%** pass rate over expected, top of the "
            f"league's range, and no interest in sitting on a lead ({r['pass_lead']:.0f}% pass when ahead)."])))

    if cc >= 7:
        ins.append((cc, f"The backfield runs through one man — {_nm(rb)} absorbs **{r['carry_RB1']:.0f}% of the "
                    f"carries** (league ~{lg['carry_RB1']:.0f}%), a true bell-cow share that makes his rushing "
                    f"volume among the safest floors in the sport when the script cooperates."))
    elif cc <= -7:
        ins.append((abs(cc), f"There's no bell-cow to bank on — the lead back sees just **{r['carry_RB1']:.0f}%** "
                    f"of carries (league ~{lg['carry_RB1']:.0f}%), a committee that caps any one back's ceiling and "
                    f"makes their rushing props matchup- and script-dependent."))
    if wc >= 4:
        ins.append((wc, f"In the passing game the ball funnels to {_nm(wr,'their WR1')} — **{r['tgt_WR1']:.0f}% of "
                    f"targets** (league ~{lg['tgt_WR1']:.0f}%), a genuine alpha share that concentrates the "
                    f"receiving value at the top."))
    elif wc <= -4:
        ins.append((abs(wc), f"They spread the ball as much as anyone — the WR1 commands only **{r['tgt_WR1']:.0f}%** "
                    f"of targets (league ~{lg['tgt_WR1']:.0f}%). Volume is democratic here, which suppresses any "
                    f"single receiver's ceiling but props up the TE and secondary options."))
    if orun >= 5:
        ins.append((orun - 2, f"Their run game attacks the perimeter — **{r['outside_run_pct']:.0f}% of carries hit "
                    f"the edge** (league ~{lg['outside_run_pct']:.0f}%), an outside-zone lean that rewards speed backs "
                    f"and can boom for chunk gains."))
    elif orun <= -4:
        ins.append((abs(orun) - 2, f"They run downhill — mostly inside the tackles ({r['outside_run_pct']:.0f}% to the "
                    f"edge vs ~{lg['outside_run_pct']:.0f}%), a gap/power scheme that trades big plays for a steady, "
                    f"grinding floor."))
    if scr >= 1.5:
        ins.append((scr, f"{_nm(qb,'Their quarterback')} is a scramble threat — he takes off on **{r['scramble_pct']:.1f}%** "
                    f"of dropbacks (nearly double the {lg['scramble_pct']:.1f}% norm), rushing production that both "
                    f"lifts his own yardage and siphons a few carries from the backfield."))
    if pace >= 2.5:
        ins.append((pace / 2, f"They play fast — **{r['plays_pg']:.0f} snaps a game** (league ~{lg['plays_pg']:.0f}), "
                    f"and the extra volume is a rising tide that lifts everyone's counting stats."))
    elif pace <= -2.5:
        ins.append((abs(pace) / 2, f"This is a deliberate, low-volume offense — just **{r['plays_pg']:.0f} plays a "
                    f"game** (league ~{lg['plays_pg']:.0f}), which quietly caps the ceiling on everybody's targets "
                    f"and carries."))
    if adot >= 0.8:
        ins.append((adot * 2, f"They push it downfield — average depth of target **{r['adot']:.1f} yards** with "
                    f"{r['deep_pct']:.0f}% of throws traveling 15+, a vertical passing game that trades completions "
                    f"for boom-or-bust yardage."))
    elif adot <= -0.8:
        ins.append((abs(adot) * 2, f"It's a short, rhythm-based passing game — aDOT just **{r['adot']:.1f}** — that "
                    f"lives on YAC and checkdowns, boosting reception totals over big-play yardage."))
    if abs(sr) >= 2:
        ins.append((abs(sr) / 2, f"By the numbers it's {'an efficient' if sr>0 else 'a struggling'} unit — a "
                    f"{r['success_rate']:.0f}% success rate ({'above' if sr>0 else 'below'} the {lg['success_rate']:.0f}% "
                    f"average)."))
    # WR2 involvement
    w2 = r["tgt_WR2"] - lg["tgt_WR2"]
    if w2 >= 3:
        ins.append((w2, f"The WR2 is unusually involved here — **{r['tgt_WR2']:.0f}%** of targets (league "
                    f"~{lg['tgt_WR2']:.0f}%), meaning there's bankable volume beyond the top receiver."))
    # TE involvement
    t2 = r["tgt_TE1"] - lg["tgt_TE1"]
    if t2 >= 3:
        ins.append((t2, f"{_nm(te,'The tight end')} is a genuine centerpiece, not an afterthought — "
                    f"**{r['tgt_TE1']:.0f}%** of targets (league ~{lg['tgt_TE1']:.0f}%)."))
    elif t2 <= -3:
        ins.append((abs(t2) - 1, f"The tight end is barely used in the passing game ({r['tgt_TE1']:.0f}% of targets vs "
                    f"~{lg['tgt_TE1']:.0f}%) — fade their TE's receiving props."))
    # RB receiving
    rr = r["rb_tgt_share"] - lg["rb_tgt_share"]
    if rr >= 3:
        ins.append((rr, f"Their backs are a real part of the passing game — RBs see **{r['rb_tgt_share']:.0f}%** of "
                    f"targets (vs ~{lg['rb_tgt_share']:.0f}%), which props up {_nm(rb,'the back')}'s reception floor."))
    # no-huddle tempo
    nh = r["nohuddle"] - lg["nohuddle"]
    if nh >= 4:
        ins.append((nh / 2, f"They lean on tempo — a {r['nohuddle']:.0f}% no-huddle rate (league ~{lg['nohuddle']:.0f}%) "
                    f"that inflates snap counts and everyone's volume."))
    # shotgun / under center
    sg = r["shotgun"] - lg["shotgun"]
    if sg >= 6:
        ins.append((sg / 3, f"They live in the shotgun ({r['shotgun']:.0f}% of snaps vs ~{lg['shotgun']:.0f}%) — a "
                    f"spread, pass-oriented structure."))
    elif sg <= -6:
        ins.append((abs(sg) / 3, f"They're a heavy under-center team ({r['shotgun']:.0f}% shotgun vs ~{lg['shotgun']:.0f}%) "
                    f"— a pro-style, run-committed structure."))
    # aggression when trailing
    pt = r["pass_trail"] - 54
    if pt >= 4:
        ins.append((pt / 2, f"When they fall behind they abandon the run entirely — {r['pass_trail']:.0f}% pass while "
                    f"trailing, so their pass-catchers have huge comeback-script ceilings."))
    ins.sort(reverse=True, key=lambda x: x[0])
    return [t for _, t in ins]


def def_insights(team, r, lg):
    """Comprehensive defensive read — ranked, specific angles for attacking them."""
    d = []
    fun = r["funnel"]
    d.append((10 if fun != "balanced-D" else 4, f"They surrender **{r['def_rush_pg']:.0f} rushing** and "
              f"**{r['def_pass_pg']:.0f} passing** yards per game (league ~{lg['def_rush_pg']:.0f} / ~{lg['def_pass_pg']:.0f}). "
              + ("The run moves against them — **target opposing RB1s**." if fun == "run-funnel"
                 else "They wall the run and force the throw — **attack with the opposing WR room**." if fun == "pass-funnel"
                 else "No dominant funnel — read it matchup by matchup.")))
    for role, key, lbl, base in [("WR1", "allow_wr1_yds", "opposing WR1s", lg["allow_wr1_yds"]),
                                 ("WR2", "allow_wr2_yds", "opposing WR2s", lg["allow_wr2_yds"]),
                                 ("TE1", "allow_te1_yds", "opposing tight ends", lg["allow_te1_yds"]),
                                 ("RB1-rec", "allow_rb1_recyds", "backs out of the backfield", lg["allow_rb1_recyds"])]:
        gap = r[key] - base
        if gap >= 4:
            d.append((gap, f"**Soft against {lbl}** — {r[key]:.0f} rec yds/game (vs ~{base:.0f}). A rec-yards target."))
        elif gap <= -4:
            d.append((abs(gap) - 2, f"They lock down {lbl} — just {r[key]:.0f} rec yds/game (vs ~{base:.0f}); fade "
                      f"receiving props there."))
    # TD leaks
    for key, lbl, base in [("allow_rb1_rushTD", "RB rushing TDs", lg["allow_rb1_rushTD"]),
                           ("allow_wr1_recTD", "WR1 receiving TDs", lg["allow_wr1_recTD"]),
                           ("allow_te1_recTD", "TE touchdowns", lg["allow_te1_recTD"])]:
        gap = r[key] - base
        if gap >= 0.05:
            d.append((gap * 20, f"They leak **{lbl}** ({r[key]:.2f}/game vs ~{base:.2f}) — an anytime-TD spot to attack."))
    # deep / efficiency
    dp = r["allow_deep_pct"] - lg["allow_deep_pct"]
    if dp >= 2:
        d.append((dp, f"They give up deep shots ({r['allow_deep_pct']:.0f}% of throws travel 15+ vs ~{lg['allow_deep_pct']:.0f}%) "
                  f"— big-play WR upside against them."))
    ds = r["def_success_allowed"] - lg["def_success_allowed"]
    if ds >= 2:
        d.append((ds / 2, f"Broadly a leaky unit — {r['def_success_allowed']:.0f}% success rate allowed (vs "
                  f"~{lg['def_success_allowed']:.0f}%); points come easy against them."))
    elif ds <= -2:
        d.append((abs(ds) / 2, f"A stingy, efficient defense — just {r['def_success_allowed']:.0f}% success allowed "
                  f"(vs ~{lg['def_success_allowed']:.0f}%); temper expectations for opposing skill players."))
    d.sort(reverse=True, key=lambda x: x[0])
    return [t for _, t in d]


def team_report(team, prof, lg, players, sched):
    r = prof
    qb = _top(players, "QB", "pass_yards"); rb = _top(players, "RB", "rush_yards")
    wr = _top(players, "WR", "rec_yards"); te = _top(players, "TE", "rec_yards")
    ins = team_insights(team, r, lg, qb, rb, wr, te)
    dins = def_insights(team, r, lg)
    P = []
    heads = {"run-heavy": ["Built to Run", "A Ground-First Machine", "Bleed the Clock"],
             "pass-heavy": ["Air Raid", "Throw First, Ask Later", "A Passing Identity"],
             "balanced": ["No Fixed Address", "Matchup-Driven", "A Balanced Attack"]}
    P.append("## " + _pick(team, heads[r["identity"]]))
    # lead with the single most distinctive trait
    if ins:
        P.append(ins[0])
    # full offensive blueprint — surface everything else, one per paragraph
    if len(ins) > 1:
        P.append("### The Offensive Blueprint")
        for t in ins[1:]:
            P.append(t)

    # red zone / scoring
    P.append("### In the Red Zone")
    rzl = _pick(team, [
        f"Inside the 20 they {'stay aggressive, throwing' if r['rz_pass']>52 else 'lean run-heavy, passing'} "
        f"**{r['rz_pass']:.0f}%** of the time",
        f"Their red-zone identity is **{'pass-first' if r['rz_pass']>52 else 'run-first'}** ({r['rz_pass']:.0f}% pass)"])
    who = (f", which puts scoring equity on {_nm(wr,'the WR1')}" if r["rz_pass"] > 52
           else f", funnelling touchdown equity to {_nm(rb,'the lead back')}")
    P.append(f"{rzl}{who}." + (f" And with the TE commanding {r['tgt_TE1']:.0f}% of targets, "
             f"{_nm(te,'the tight end')} is a live red-zone option too." if r["tgt_TE1"] > lg["tgt_TE1"] + 2 else ""))

    # ---- DEFENSE (full breakdown) ----
    fun = r["funnel"]
    dhead = _pick(team, {"run-funnel": ["The Run Comes Free", "Soft on the Ground"],
                         "pass-funnel": ["Stout Up Front, Soft in Coverage", "They Force the Throw"],
                         "balanced-D": ["A Balanced Defense", "No Obvious Seam"]}[fun])
    P.append("## Defense — " + dhead)
    for t in dins:
        P.append(t)

    # ---- betting angles: pull it together ----
    P.append("## The Betting Angles")
    takes = []
    if r["identity"] == "run-heavy":
        takes.append(f"back {_nm(rb,'their RB1')}'s rushing volume in favored/positive scripts")
    if r["identity"] == "pass-heavy":
        takes.append(f"{_nm(wr,'their WR1')} holds value even when they're winning")
    if r["pass_trail"] - 54 >= 4:
        takes.append("their pass-catchers have massive ceilings in comeback scripts (fade-material as underdogs)")
    if fun == "run-funnel":
        takes.append("**opposing RB1s** smash on the ground and at the goal line")
    if fun == "pass-funnel":
        takes.append("**opposing WR1/WR2** are the target — this D forces the throw")
    if r["allow_te1_recTD"] > lg["allow_te1_recTD"] + 0.04:
        takes.append("**opposing TE anytime-TD** is a repeatable angle")
    P.append(("Bottom line: " + "; ".join(takes) + ".") if takes else
             "Bottom line: no single dominant angle — this one's a matchup-by-matchup read.")
    P.append("*Built on 2024–25 data; the profile rebuilds through the season as the current staff and roster "
             "set their own tendencies.*")
    return "\n\n".join(P)


def team_spread(grow, team):
    return grow.spread_line if team == grow.home_team else -grow.spread_line


def score_proj(total, home_spread):
    return (total + home_spread) / 2, (total - home_spread) / 2  # (home, away)


def flow(tis, spread):
    sr = float(np.clip(round(spread), -16, 16))
    row = tis.loc[sr] if sr in tis.index else tis.iloc[(np.abs(tis.index.values - sr)).argmin()]
    return {s: float(row[s]) for s in STATES}


def q_interp(row, stat, level):
    lv = [.10, .25, .50, .75, .90]
    qs = [row.get(f"{stat}_p{int(q*100)}") for q in lv]
    if any(pd.isna(q) for q in qs):
        return row.get(f"{stat}_mean", np.nan)
    return float(np.interp(level, lv, qs))


def bet_line(row, stat, edge=0.05):
    """OVER if book line <= over_th (P(over)>=.5+edge); UNDER if >= under_th."""
    over_th = q_interp(row, stat, 0.5 - edge)   # 45th pct -> P(over)=.55
    under_th = q_interp(row, stat, 0.5 + edge)  # 55th pct -> P(over)=.45
    return over_th, under_th


def team_players(slate, game_id, team):
    d = slate[(slate.game_id == game_id) & (slate.team == team)].copy()
    d = d[d.position.isin(["QB", "RB", "WR", "TE"])]
    d["po"] = d.position.map(POS_ORDER)
    # primary-volume sort within position
    d["vol"] = d.apply(lambda r: r.get({"QB": "pass_yards", "RB": "rush_yards",
                                         "WR": "targets", "TE": "targets"}[r.position] + "_mean", 0), axis=1)
    return d.sort_values(["po", "vol"], ascending=[True, False])


# --------------------------------------------------------------- narrative report
def _core(players):
    core = players[players.is_projected_starter] if players.is_projected_starter.any() else players
    out, seen = [], False
    for _, p in core.iterrows():
        if p.position == "QB":
            if seen:
                continue
            seen = True
        out.append(p)
    return out


def _top(players, pos, stat):
    d = players[players.position == pos]
    d = d[d[f"{stat}_mean"] > 0.3]
    if len(d) == 0:
        return None
    return d.sort_values(f"{stat}_mean", ascending=False).iloc[0]


def game_matchup_angles(fav, dog, teams, fav_players, dog_players, grow):
    """Rich, categorised list of every mismatch in THIS game. Returns list of
    (weight, category, text). Categories: run, pass, recv, td, script, pace, env."""
    if teams is None or fav not in teams.index or dog not in teams.index:
        return []
    lg = teams.loc["LEAGUE"]
    hs = grow.spread_line
    margin = abs(hs)
    total = grow.total_line
    roof = str(grow.get("roof", "") or "").lower()
    wind = grow.get("wind", np.nan)
    A = []

    for off, deff, offp, is_fav in [(fav, dog, fav_players, True), (dog, fav, dog_players, False)]:
        op, dp = teams.loc[off], teams.loc[deff]
        rb = _top(offp, "RB", "rush_yards"); wr = _top(offp, "WR", "rec_yards")
        wr2s = offp[offp.position == "WR"]; wr2 = wr2s.iloc[1] if len(wr2s) > 1 else None
        te = _top(offp, "TE", "rec_yards"); qb = _top(offp, "QB", "pass_yards")
        rn, wn, tn = _nm(rb, "the lead back"), _nm(wr, "the top receiver"), _nm(te, "the tight end")

        # --- run angles ---
        if op["identity"] == "run-heavy" and dp["funnel"] == "run-funnel":
            A.append((11 + (3 if is_fav else 0), "run",
                f"**Ground mismatch:** {off} are run-first ({op['pass_lead']:.0f}% pass when leading) into a {deff} "
                f"front that bleeds **{dp['def_rush_pg']:.0f} rush yds/game** and {dp['allow_rb1_rushTD']:.2f} RB1 "
                f"rushing scores. {rn} is a centerpiece{' — and the game script only feeds him' if is_fav else ''}."))
        elif dp["def_rush_pg"] > lg["def_rush_pg"] + 7 and op["carry_RB1"] > 55:
            A.append((7, "run", f"{deff} are soft against the run ({dp['def_rush_pg']:.0f} yds/game allowed), and "
                      f"{off} feed {rn} a heavy **{op['carry_RB1']:.0f}%** carry share — a rushing-yards spot."))
        elif dp["def_rush_pg"] < lg["def_rush_pg"] - 8:
            A.append((3, "run", f"Tough sledding for {rn} — {deff} are stingy against the run "
                      f"({dp['def_rush_pg']:.0f} yds/game, well below the ~{lg['def_rush_pg']:.0f} norm); lean under on "
                      f"his rushing line."))
        if op["carry_RB1"] > 64 and dp["allow_rb1_rushTD"] > lg["allow_rb1_rushTD"]:
            A.append((6, "td", f"{rn} is a bell-cow ({op['carry_RB1']:.0f}% of carries) meeting a defense that "
                      f"allows {dp['allow_rb1_rushTD']:.2f} RB rushing TDs/game — strong anytime-TD equity."))
        if op["outside_run_pct"] > lg["outside_run_pct"] + 5 and dp["def_rush_pg"] > lg["def_rush_pg"] + 8:
            A.append((4, "run", f"{off}'s outside-zone run game ({op['outside_run_pct']:.0f}% to the edge) attacks a "
                      f"leaky {deff} front — chunk-run upside for {rn}."))

        # --- pass / receiving angles ---
        if op["identity"] == "pass-heavy" and dp["funnel"] == "pass-funnel":
            A.append((10, "pass", f"**Air mismatch:** {off} throw by design into a {deff} secondary giving up "
                      f"**{dp['def_pass_pg']:.0f} pass yds/game**. {wn} is the focal point of the attack."))
        if dp["allow_wr1_yds"] > lg["allow_wr1_yds"] + 5:
            A.append((8 if op["tgt_WR1"] > lg["tgt_WR1"] else 5, "recv",
                      f"{wn} draws a plus matchup — {deff} concede **{dp['allow_wr1_yds']:.0f} yds/game to WR1s** "
                      f"(league ~{lg['allow_wr1_yds']:.0f}){', and '+off+' funnel '+str(round(op['tgt_WR1']))+'% of targets his way' if op['tgt_WR1']>lg['tgt_WR1'] else ''}. "
                      f"A rec-yards spot."))
        elif dp["allow_wr1_yds"] < lg["allow_wr1_yds"] - 6:
            A.append((3, "recv", f"{wn} faces a tough cover — {deff} hold WR1s to just {dp['allow_wr1_yds']:.0f} "
                      f"yds/game (vs ~{lg['allow_wr1_yds']:.0f}); a rec-yards under lean."))
        if dp["allow_wr2_yds"] > lg["allow_wr2_yds"] + 5 and wr2 is not None:
            A.append((4, "recv", f"Sneaky value on **{wr2.player_name}** (WR2) — {deff} leak "
                      f"{dp['allow_wr2_yds']:.0f} yds/game to the WR2 role, above the ~{lg['allow_wr2_yds']:.0f} norm."))
        if dp["allow_te1_yds"] > lg["allow_te1_yds"] + 4 and te is not None:
            A.append((5, "recv", f"{tn} has room to work — {deff} allow **{dp['allow_te1_yds']:.0f} yds/game to TE1s** "
                      f"(vs ~{lg['allow_te1_yds']:.0f}), a receiving-yards and target angle."))
        # QB passing volume matchup
        if qb is not None and dp["def_pass_pg"] > lg["def_pass_pg"] + 8:
            A.append((4, "pass", f"{_nm(qb,'The QB')} has a passing-yards tailwind — {deff} allow "
                      f"**{dp['def_pass_pg']:.0f} pass yds/game** (vs ~{lg['def_pass_pg']:.0f})."))
        # red-zone identity
        if op["rz_pass"] > 55 and wr is not None:
            A.append((3, "td", f"In the red zone {off} throw it ({op['rz_pass']:.0f}% pass inside the 20) — "
                      f"{wn}'s TD equity over a goal-line back."))
        elif op["rz_pass"] < 42 and rb is not None:
            A.append((3, "td", f"{off} pound it at the goal line ({op['rz_pass']:.0f}% pass in the red zone) — "
                      f"{rn} owns the rushing-TD equity."))
        if dp["allow_te1_recTD"] > lg["allow_te1_recTD"] + 0.05 and te is not None:
            A.append((6, "td", f"{tn} is a live anytime-TD dart — {deff} are leaky to tight ends in the end zone "
                      f"({dp['allow_te1_recTD']:.2f} TE TDs/game vs ~{lg['allow_te1_recTD']:.2f})."))
        if dp["allow_wr1_recTD"] > lg["allow_wr1_recTD"] + 0.06:
            A.append((5, "td", f"{wn} carries TD equity — {deff} surrender {dp['allow_wr1_recTD']:.2f} WR1 receiving "
                      f"scores a game."))
        if dp["allow_rb1_recyds"] > lg["allow_rb1_recyds"] + 4 and op["rb_tgt_share"] > lg["rb_tgt_share"]:
            A.append((4, "recv", f"{rn} eats as a receiver too — {deff} give up {dp['allow_rb1_recyds']:.0f} rec "
                      f"yds/game to backs, and {off} target the RB on {op['rb_tgt_share']:.0f}% of throws."))
        if op["adot"] > lg["adot"] + 0.8 and dp["allow_deep_pct"] > lg["allow_deep_pct"] + 1.5:
            A.append((4, "pass", f"{off} push it deep (aDOT {op['adot']:.1f}) into a {deff} defense that yields deep "
                      f"shots on {dp['allow_deep_pct']:.0f}% of throws — boom-or-bust upside for {wn}."))
        if op["scramble_pct"] > lg["scramble_pct"] + 1.5 and qb is not None:
            A.append((3, "run", f"{_nm(qb,'The QB')} adds rushing value — he scrambles on {op['scramble_pct']:.1f}% of "
                      f"dropbacks, live for rush-yard and anytime-TD props."))
        if op["success_rate"] > lg["success_rate"] + 2 and dp["def_success_allowed"] > lg["def_success_allowed"] + 2:
            A.append((5, "pass", f"Efficiency edge to {off} — a {op['success_rate']:.0f}% success-rate offense against a "
                      f"{deff} defense that allows {dp['def_success_allowed']:.0f}% — points should come."))

    # --- game-script angles ---
    if margin >= 7:
        favp = fav_players if True else None
        frb = _top(fav_players, "RB", "rush_yards"); dwr = _top(dog_players, "WR", "rec_yards")
        A.append((9, "script", f"**Script leans {fav}.** As a {margin:.0f}-point favorite they project to lead much of "
                  f"the game, tilting toward the run late — {_nm(frb,'their back')}'s carry ceiling rises in the "
                  f"blowout branches. Meanwhile {dog} throw to keep pace, a volume tailwind for {_nm(dwr,'their WR1')}."))
    if margin >= 9:
        drb = _top(dog_players, "RB", "rush_yards")
        A.append((5, "script", f"Fade the {dog} run — as {margin:.0f}-point dogs projected to chase, {_nm(drb,'their back')}'s "
                  f"carries get squeezed, though his receiving work ticks up in catch-up mode."))

    # --- pace / total ---
    fast = teams.loc[fav]["plays_pg"] + teams.loc[dog]["plays_pg"]
    if total >= 48 and teams.loc[fav]["identity"] != "run-heavy" and teams.loc[dog]["identity"] != "run-heavy":
        A.append((6, "pace", f"**Shootout profile** — a {total:.0f} total between two non-run-first offenses points to "
                  f"volume passing on both sides; lean into the overs for the pass-catchers."))
    elif 44 <= total < 48:
        A.append((3, "pace", f"A middling {total:.0f} total — no strong lean either way; the value is in the "
                  f"individual matchups above, not the game environment."))
    if total <= 41:
        A.append((5, "pace", f"**Low-total grind** — a {total:.0f} total signals a slower, run-leaning game; skew toward "
                  f"unders, especially on secondary pass-catchers."))
    if 3.5 <= margin < 7:
        frb = _top(fav_players, "RB", "rush_yards")
        A.append((4, "script", f"A modest lean to {fav} — they should lead more often than not, nudging them toward "
                  f"the run late and giving {_nm(frb,'their back')} a gentle game-script tailwind."))
    if fast >= (lg["plays_pg"] * 2 + 5):
        A.append((3, "pace", f"Both teams play at a quick tempo (~{fast/2:.0f} plays each) — extra snaps lift everyone's "
                  f"counting stats."))

    # --- environment ---
    if roof in ("dome", "closed"):
        A.append((4, "env", "**Indoors** — a dome removes weather variance and historically nudges passing efficiency "
                  "up; a small tailwind for the passing games."))
    if pd.notna(wind) and wind >= 15:
        A.append((7, "env", f"**Wind alert — {wind:.0f} mph.** High wind suppresses the passing game; a real edge toward "
                  f"passing/receiving UNDERS (this is one of the few weather effects the market underprices)."))
    return sorted(A, reverse=True, key=lambda x: x[0])


def report(grow, ts, tis, home_players, away_players, teams=None):
    home, away = grow.home_team, grow.away_team
    hs = grow.spread_line
    hpts, apts = score_proj(grow.total_line, hs)
    fav, dog = (home, away) if hs > 0 else (away, home)
    fav_players = home_players if fav == home else away_players
    dog_players = home_players if dog == home else away_players
    margin = abs(hs)
    fl = flow(tis, abs(hs))
    lead_pct = (fl["lead"] + fl["lead14+"]) * 100
    trail_pct = (fl["trail"] + fl["trail14+"]) * 100
    close_pct = fl["close"] * 100
    P = []

    # ---- lead with the game's defining matchup angle (varies per game) ----
    angles = game_matchup_angles(fav, dog, teams, fav_players, dog_players, grow)
    by_cat = {}
    for w, cat, txt in angles:
        by_cat.setdefault(cat, []).append(txt)
    if angles:
        P.append(angles[0][2])

    # ---- the matchup (hero shows the score; this is the written preview) ----
    P.append(
        f"The **{home}** {'host' if hs>0 else 'welcome'} the **{away}** as "
        f"{'favorites' if hs>0 else 'underdogs'} of **{margin:.1f}**, with the total set at "
        f"**{grow.total_line:.1f}** — a market that implies a **{fav} {max(hpts,apts):.0f}–"
        f"{min(hpts,apts):.0f}** scoreline. Running the game forward from that baseline, we expect a "
        f"contest that spends roughly **{lead_pct:.0f}% of its snaps with {fav} in front**, "
        f"**{close_pct:.0f}% within one score**, and **{trail_pct:.0f}% with {fav} chasing**. That "
        f"distribution is the engine of everything below: game state is what turns a team's tendencies "
        f"into a specific player's workload, so before we get to individuals it's worth being explicit "
        f"about *how* this game is likely to be played.")

    # ---- how the game flows ----
    if margin >= 6:
        arc = (f"This projects as a game **{fav} should control**. A favorite of {margin:.0f} sits in "
               f"front for the majority of the second half in most simulations, which pulls the script "
               f"toward the run and clock management for **{fav}** and toward volume passing and tempo "
               f"for **{dog}**, who will be throwing to keep pace. Expect **{dog}** to run more total "
               f"plays than {fav} — trailing teams go no-huddle (league no-huddle rate climbs from ~8% "
               f"in neutral game states to over 20% when down two scores), and that extra volume is a "
               f"tailwind for {dog}'s pass-catchers even in a losing effort.")
    elif margin <= 3:
        arc = (f"This is a **coin-flip script**. With only {margin:.1f} points separating them, neither "
               f"team projects to spend much time in a blowout state — most of the game lives in the "
               f"'close' bucket, where play-calling stays balanced and usage tracks each player's "
               f"established role rather than being distorted by score. In tight games the stars on "
               f"both sides see their normal, dependable workloads, and there is less garbage-time "
               f"leakage to backups.")
    else:
        arc = (f"A **modest edge to {fav}**. They should lead more often than not, tilting them slightly "
               f"toward the run late, while {dog} leans a touch more pass-heavy to stay in it — but the "
               f"effect is gentle; this isn't a script that dramatically reshapes anyone's usage.")
    P.append(arc)

    # ---- organised angle sections (surface everything) ----
    matchup_txts = by_cat.get("run", []) + by_cat.get("pass", []) + by_cat.get("recv", [])
    # drop the one already used as the headline
    lead_txt = angles[0][2] if angles else None
    matchup_txts = [t for t in matchup_txts if t != lead_txt]
    if matchup_txts:
        P.append("### The Key Matchups")
        for t in matchup_txts:
            P.append(t)
    if by_cat.get("td"):
        P.append("### Touchdown Board")
        for t in by_cat["td"]:
            P.append(t)
    script_env = by_cat.get("script", []) + by_cat.get("pace", []) + by_cat.get("env", [])
    script_env = [t for t in script_env if t != lead_txt]
    if script_env:
        P.append("### Game Script, Pace & Environment")
        for t in script_env:
            P.append(t)

    # ---- per-team deep dive ----
    for team, players in [(fav, home_players if fav == home else away_players),
                          (dog, home_players if dog == home else away_players)]:
        spread = team_spread(grow, team)
        # use team_profiles (recent 2024-25) as the single source of truth, so the
        # per-team read never contradicts the headline angles
        tp = teams.loc[team] if (teams is not None and team in teams.index) else None
        ident = tp["identity"] if tp is not None else "balanced"
        lead_pr = tp["pass_lead"] if tp is not None else 44.0
        trail_pr = tp["pass_trail"] if tp is not None else 54.0
        qb = _top(players, "QB", "pass_yards")
        rb = _top(players, "RB", "rush_yards")
        wr = _top(players, "WR", "rec_yards")
        te = _top(players, "TE", "rec_yards")
        P.append(f"### {team} — a *{ident}* offense")

        # identity explanation with data
        if ident == "run-heavy":
            idl = (f"{team} are one of the league's more run-committed offenses: on early downs while "
                   f"**leading**, they pass just **{lead_pr:.0f}%** of the time, against a league average "
                   f"of 44% — a genuine clock-bleeding identity, not noise (it holds after shrinking their "
                   f"sample toward the league).")
        elif ident == "pass-heavy":
            idl = (f"{team} stay aggressive through the air even with a lead — they pass **{lead_pr:.0f}%** "
                   f"on early downs when ahead versus the league's 44%. They don't sit on the ball, which "
                   f"keeps their receivers live in scripts where most offenses would be running out the clock.")
        else:
            idl = (f"{team} are a balanced offense — their run/pass split by game state tracks the league "
                   f"closely ({lead_pr:.0f}% pass when leading vs 44% average), so their players' usage is "
                   f"driven more by role than by any strong situational lean.")
        P.append(idl)

        # game-script consequence for the key players, with projections + reasoning
        if rb is not None:
            rc, ry = rb.get("rush_att_mean", 0), rb.get("rush_yards_mean", 0)
            if spread > 3 and ident == "run-heavy":
                why = (f"This is the profile that most rewards a lead back. As a {margin:.0f}-point favorite "
                       f"projected to lead for {lead_pct:.0f}% of snaps, **{team}** will be running to close "
                       f"the game, and their run-first identity compounds it. **{rb.player_name}** is the "
                       f"beneficiary — we project **{rc:.0f} carries for {ry:.0f} yards**, and his ceiling "
                       f"is where the value sits: in the simulations where this becomes a two-score game, "
                       f"his volume spikes as {team} salt it away.")
            elif spread > 3:
                why = (f"**{rb.player_name}** ({rc:.0f} carries, {ry:.0f} yards projected) gets a mild "
                       f"game-script tailwind as a favorite, though {team}'s willingness to keep throwing "
                       f"when ahead caps how much the lead converts into extra carries.")
            elif spread < -3:
                why = (f"Game script works against the run here — {team} project to trail and throw, which "
                       f"squeezes **{rb.player_name}**'s rushing projection ({rc:.0f} carries, {ry:.0f} yards) "
                       f"but *raises* his value as a receiver; trailing teams check the ball down, and his "
                       f"target share ticks up as the game gets away from them.")
            else:
                why = (f"**{rb.player_name}** ({rc:.0f} carries, {ry:.0f} yards) projects to his baseline "
                       f"workload — no strong script push either way in a close game.")
            P.append(why)

        if wr is not None:
            wy, wt = wr.get("rec_yards_mean", 0), wr.get("targets_mean", 0)
            if ident == "run-heavy" and spread > 3:
                wl = (f"The flip side: **{wr.player_name}** ({wt:.0f} targets, {wy:.0f} yards) has his "
                      f"ceiling *capped* by the same script that helps the run game — when {team} lead and "
                      f"run, there simply aren't as many pass attempts to go around. He's a safer floor "
                      f"than ceiling play in this spot.")
            elif spread < -3:
                wl = (f"**{wr.player_name}** is the one to target from a volume standpoint: as underdogs "
                      f"throwing to keep pace, {team} funnel work to their top receiver — **{wt:.0f} targets "
                      f"for {wy:.0f} yards** projected, with genuine upside if they fall behind early and "
                      f"the game turns one-dimensional.")
            else:
                wl = (f"**{wr.player_name}** projects for **{wt:.0f} targets and {wy:.0f} yards**, in line "
                      f"with his established role in a script that neither inflates nor suppresses the pass.")
            P.append(wl)
            if te is not None and te.get("rec_yards_mean", 0) > 20:
                P.append(f"Underneath, **{te.player_name}** (TE, {te.get('targets_mean',0):.0f} targets, "
                         f"{te.get('rec_yards_mean',0):.0f} yards) is the dependable secondary read — and "
                         f"note the red zone stays TE-friendly regardless of score, so his touchdown equity "
                         f"holds up even if the game script shifts.")

    # ---- what to watch / bottom line ----
    P.append("### What to watch")
    P.append(
        f"The single biggest swing factor is **availability** — use the injury tool in the sidebar to "
        f"mark anyone out; a starter's absence doesn't just remove his line, it *redistributes* his "
        f"targets and carries to specific teammates, and the report and projections rebuild around it. "
        f"Beyond that: if this game breaks toward the projected {margin:.0f}-point margin, the "
        f"**garbage-time backups** (the ◆-flagged rows below) are where soft lines and real edge tend to "
        f"appear — those depth players get fed in blowouts, and the market is slowest to price them.")
    P.append("*This preview and the projections below regenerate through the week as injuries, "
             "weather and line moves update the inputs.*")
    return "\n\n".join(P)


# ------------------------------------------------------------------- injury tool
def redistribute(players, out_names):
    """Mark players out -> proportionally reallocate their targets & carries to
    same-position teammates. Returns adjusted projection frame."""
    d = players.copy()
    for grp, vol in [(["WR", "TE", "RB"], "targets_mean"), (["RB"], "rush_att_mean")]:
        pool = d[d.position.isin(grp)]
        out = pool[pool.player_name.isin(out_names)]
        avail = pool[~pool.player_name.isin(out_names)]
        vac = out[vol].sum()
        base = avail[vol].sum()
        if vac > 0 and base > 0:
            factor = (base + vac) / base
            for stat in ([ "targets", "receptions", "rec_yards"] if vol == "targets_mean"
                         else ["rush_att", "rush_yards"]):
                col = f"{stat}_mean"
                if col in d.columns:
                    d.loc[avail.index, col] = d.loc[avail.index, col] * factor
    d.loc[d.player_name.isin(out_names), [c for c in d.columns if c.endswith("_mean")]] = 0.0
    return d


# ------------------------------------------------------------------- views
def game_page(gid, slate, games, ts, tis, teams):
    grow = games[games.game_id == gid].iloc[0]
    home, away = grow.home_team, grow.away_team
    if st.button("←  back to the slate"):
        st.session_state.game = None; st.rerun()
    st.markdown(hero_html(grow), unsafe_allow_html=True)

    hp = team_players(slate, gid, home)
    ap = team_players(slate, gid, away)

    # injury tool (affects report + projections)
    with st.sidebar:
        st.subheader("🩹 Injury / inactive tool")
        st.caption("Mark players out — projections & report update live.")
        allp = pd.concat([hp, ap])
        out = st.multiselect("Out this week", allp.player_name.tolist())
    if out:
        hp = redistribute(hp, out); ap = redistribute(ap, out)

    st.markdown('<div class="seclabel">The Match Report</div>', unsafe_allow_html=True)
    st.markdown(md_to_html(report(grow, ts, tis, hp, ap, teams)), unsafe_allow_html=True)
    st.markdown('<div class="seclabel">Projections &amp; Line Calls</div>', unsafe_allow_html=True)

    for team, players in [(away, ap), (home, hp)]:
        st.markdown(f'<h3 style="color:{tcol(team)};margin-top:20px;letter-spacing:.04em">{team}</h3>',
                    unsafe_allow_html=True)
        rows = []
        for _, p in players.iterrows():
            if p.get("rush_yards_mean", 0) + p.get("rec_yards_mean", 0) + p.get("pass_yards_mean", 0) < 1:
                continue
            for lbl, stat in MARKETS.get(p.position, []):
                m = p.get(f"{stat}_mean")
                if pd.isna(m) or m < 0.3:
                    continue
                ov, un = bet_line(p, stat)
                rows.append({"Player": p.player_name, "Pos": p.position, "Market": lbl,
                             "Proj": round(m, 1), "OVER if ≤": round(ov, 1),
                             "UNDER if ≥": round(un, 1),
                             "★": "◆" if not p.is_projected_starter else ""})
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={"★": st.column_config.TextColumn("edge", help="◆ = minor role (softest lines)")})
    # ---- anytime TD sheet ----
    st.divider()
    st.subheader("🎯 Anytime Touchdown — bet thresholds (fractional)")
    st.caption("Model's TD probability → the FAIR price. Take the bet only if the book offers this "
               "price **or bigger** (longer odds = more payout). Below the threshold, there's no edge.")
    tdrows = []
    for team, players in [(away, ap), (home, hp)]:
        for _, p in players.iterrows():
            prob = p.get("anytime_td_mean", np.nan)
            if pd.isna(prob) or prob < 0.06:
                continue
            frac, dec = prob_to_frac(prob)
            tdrows.append({"Player": p.player_name, "Team": team, "Pos": p.position,
                           "Model TD%": round(prob * 100, 1), "Bet at": f"{frac}  or bigger",
                           "_dec": dec, "★": "◆" if not p.is_projected_starter else ""})
    if tdrows:
        td = pd.DataFrame(tdrows).sort_values("Model TD%", ascending=False).drop(columns="_dec")
        st.dataframe(td, use_container_width=True, hide_index=True,
                     column_config={"★": st.column_config.TextColumn("edge", help="◆ = minor role")})
        st.caption("Example: a player the model gives ~50% to score reads as **EVS or bigger**; ~29% "
                   "reads as **5/2 or bigger**. TD projections are noisier than yardage — treat as a "
                   "lean, and shop for the longest price.")
    else:
        st.write("No qualifying TD scorers projected.")

    st.info("**How to read the calls:** books post props at −110 both sides. Bet the **OVER** only if the "
            "posted line is at or below the *OVER-if* number, the **UNDER** only if at or above the *UNDER-if* "
            "number. In between, the market's line is inside our fair range — **no bet**. ◆ minor-role rows are "
            "where soft lines (and real edge) historically live.")


def team_page(team, slate, games, teams):
    prof = teams.loc[team].to_dict()
    lg = teams.loc["LEAGUE"].to_dict()
    c = tcol(team)
    sl = lambda t: st.markdown(
        f'<div class="seclabel" style="color:{c};border-left-color:{c}">{t}</div>',
        unsafe_allow_html=True)
    st.markdown(f"""
    <div class="hero" style="text-align:center;--ca:{c};--cb:{c}">
      <div class="eyebrow">Team Dossier</div>
      <div style="font-family:'Saira Condensed',sans-serif;font-weight:800;font-size:52px;letter-spacing:.01em;color:{c};margin:6px 0 12px;line-height:1">{team}</div>
      <div class="chips">
        <span class="chip">OFF <b style="color:{c}">{prof['identity']}</b></span>
        <span class="chip">DEF <b style="color:{c}">{prof['funnel']}</b></span>
        <span class="chip">{prof['plays_pg']:.0f} plays/gm</span>
        <span class="chip">aDOT <b style="color:{c}">{prof['adot']:.1f}</b></span>
      </div>
    </div>""", unsafe_allow_html=True)

    wk1 = slate[(slate.team == team) & (slate.week == 1)]
    sched = games[(games.home_team == team) | (games.away_team == team)].sort_values("week")

    sl("The Team Report")
    st.markdown(md_to_html(team_report(team, prof, lg, wk1, sched)), unsafe_allow_html=True)

    sl("Projected Leaders")
    rows = []
    for _, p in wk1.iterrows():
        if p.position not in ("QB", "RB", "WR", "TE"):
            continue
        stat = {"QB": ("pass_yards", "pass yds"), "RB": ("rush_yards", "rush yds"),
                "WR": ("rec_yards", "rec yds"), "TE": ("rec_yards", "rec yds")}[p.position]
        v = p.get(f"{stat[0]}_mean", 0)
        if v > 1:
            rows.append({"Player": p.player_name, "Pos": p.position,
                         "Key proj": f"{v:.0f} {stat[1]}",
                         "TD%": round(p.get("anytime_td_mean", 0) * 100, 1),
                         "Role": "starter" if p.is_projected_starter else "depth"})
    if rows:
        st.dataframe(pd.DataFrame(rows).sort_values("TD%", ascending=False),
                     use_container_width=True, hide_index=True)

    sl("2026 Schedule &amp; Projected Results")
    srows = []
    for _, g in sched.iterrows():
        at_home = g.home_team == team
        opp = g.away_team if at_home else g.home_team
        spread = g.spread_line if at_home else -g.spread_line
        hp, ap = score_proj(g.total_line, g.spread_line)
        my, th = (hp, ap) if at_home else (ap, hp)
        srows.append({"Wk": int(g.week), "H/A": "vs" if at_home else "@", "Opp": opp,
                      "Spread": f"{'−' if spread>0 else '+'}{abs(spread):.1f}",
                      "Proj": f"{my:.0f}–{th:.0f}", "Result": "W" if my > th else "L"})
    if srows:
        st.dataframe(pd.DataFrame(srows), use_container_width=True, hide_index=True)

    with st.expander("📊 Full tendency numbers"):
        st.dataframe(pd.DataFrame({"metric": list(prof.keys()), "value": list(prof.values()),
                                   "league": [lg.get(k, "") for k in prof.keys()]}),
                     use_container_width=True, hide_index=True)


def slate_view(week, slate, games, ts, tis):
    st.title(f"🏈 Week {week} — {SEASON}")
    wk = games[games.week == week].copy()
    wk = wk.sort_values(["gameday", "gametime"])
    if wk.empty:
        st.warning("No games."); return
    day = None
    for _, g in wk.iterrows():
        if g.gameday != day:
            day = g.gameday
            st.subheader(pd.to_datetime(day).strftime("%A · %b %-d"))
        fav = g.home_team if g.spread_line > 0 else g.away_team
        c1, c2 = st.columns([4, 1])
        with c1:
            if st.button(f"**{g.away_team} @ {g.home_team}**  ·  {g.gametime}  ·  "
                         f"{fav} −{abs(g.spread_line):.1f}, O/U {g.total_line:.1f}",
                         key=g.game_id, use_container_width=True):
                st.session_state.game = g.game_id; st.rerun()


def main():
    st.set_page_config(page_title="NFL Game Board", layout="wide")
    inject_css()
    slate, games, ts, tis = load()
    teams = load_teams()
    if "game" not in st.session_state:
        st.session_state.game = None
    st.sidebar.markdown(
        f"""<div style="display:flex;align-items:center;gap:12px;margin:2px 0 14px">
          {shield_svg(42)}
          <div style="line-height:1.05">
            <div style="font-family:'Oswald',sans-serif;font-weight:700;font-size:20px;
                 letter-spacing:.04em;color:#f3f7fb;text-transform:uppercase">Game Board</div>
            <div style="font-family:'Oswald',sans-serif;font-weight:500;font-size:10.5px;
                 letter-spacing:.22em;color:#8a9cae;text-transform:uppercase">2026 · Projections</div>
          </div>
        </div>""", unsafe_allow_html=True)
    page = st.sidebar.radio("View", ["🗓️ Games", "🛡️ Teams"], label_visibility="collapsed")
    st.sidebar.divider()

    if page == "🗓️ Games":
        weeks = sorted(games.week.unique())
        week = st.sidebar.selectbox("Week", weeks, index=0)
        if st.session_state.game:
            game_page(st.session_state.game, slate, games, ts, tis, teams)
        else:
            slate_view(week, slate, games, ts, tis)
    else:
        if teams is None:
            st.error("Run deploy/precompute_teams.py first."); return
        team = st.sidebar.selectbox("Team", sorted([t for t in teams.index if t != "LEAGUE"]))
        team_page(team, slate, games, teams)


if __name__ == "__main__":
    main()
