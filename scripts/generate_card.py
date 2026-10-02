"""Generate the animated ASCII profile cards (dark_mode.svg / light_mode.svg).

Pulls live stats from the GitHub GraphQL API and renders them next to the
ASCII portrait in scripts/ascii/. Run by .github/workflows/profile-card.yml.

Usage: GITHUB_TOKEN=... python scripts/generate_card.py [username]
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_USER", "KrsnaOn")

FONT = "'Consolas', 'Menlo', 'DejaVu Sans Mono', monospace"
ART_SIZE, ART_CHAR, ART_LINE = 8, 4.8, 9.6
TEXT_SIZE, TEXT_CHAR, TEXT_LINE = 16, 9.6, 20
PAD_X, PAD_TOP, PAD_BOTTOM, GAP = 28, 34.6, 31, 32
WIDTH = 57  # characters per stats line
COL = 26  # characters per column in two-column rows
SPARK = " ▁▂▃▄▅▆▇█"

THEMES = {
    "dark": dict(bg="#0d1117", border="#30363d", text="#c9d1d9", key="#ffa657",
                 dots="#484f58", rule="#3d444d", title="#58a6ff", num="#79c0ff",
                 spark="#39d353"),
    "light": dict(bg="#ffffff", border="#d0d7de", text="#24292f", key="#953800",
                  dots="#8c959f", rule="#d0d7de", title="#0969da", num="#0550ae",
                  spark="#2da44e"),
}


# --------------------------------------------------------------------------- data

def graphql(query, **variables):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": "profile-card"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)
    if body.get("errors"):
        sys.exit(f"GraphQL error: {body['errors']}")
    return body["data"]


PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositoriesContributedTo(contributionTypes: [COMMIT, PULL_REQUEST, ISSUE, REPOSITORY]) { totalCount }
    allRepos: repositories(ownerAffiliations: OWNER, privacy: PUBLIC) { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false) {
      nodes {
        name
        stargazerCount
        forkCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
    contributionsCollection {
      totalPullRequestReviewContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { contributionCount } }
      }
    }
  }
}
"""


def all_time_commits(created):
    """Commit contributions summed over every year since the account was created."""
    now = datetime.now(timezone.utc)
    parts = []
    for i, year in enumerate(range(created.year, now.year + 1)):
        start = max(created, datetime(year, 1, 1, tzinfo=timezone.utc))
        end = min(now, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        parts.append(
            f'y{i}: contributionsCollection(from: "{start.isoformat()}", to: "{end.isoformat()}") '
            "{ totalCommitContributions }"
        )
    query = 'query($login: String!) { user(login: $login) { %s } }' % " ".join(parts)
    years = graphql(query, login=USER)["user"].values()
    return sum(y["totalCommitContributions"] for y in years)


def uptime(created):
    now = datetime.now(timezone.utc)
    months = (now.year - created.year) * 12 + now.month - created.month
    if now.day < created.day:
        months -= 1
    anchor_year = created.year + (created.month - 1 + months) // 12
    anchor_month = (created.month - 1 + months) % 12 + 1
    try:
        anchor = created.replace(year=anchor_year, month=anchor_month)
    except ValueError:  # e.g. created on the 31st
        anchor = created.replace(year=anchor_year, month=anchor_month, day=28)
    days = (now.date() - anchor.date()).days
    years, months = divmod(months, 12)
    out = []
    if years:
        out.append(f"{years} year{'s' * (years != 1)}")
    if months:
        out.append(f"{months} month{'s' * (months != 1)}")
    out.append(f"{days} day{'s' * (days != 1)}")
    return ", ".join(out)


def fetch_stats():
    u = graphql(PROFILE_QUERY, login=USER)["user"]
    created = datetime.fromisoformat(u["createdAt"].replace("Z", "+00:00"))
    repos = u["repositories"]["nodes"]

    lang_bytes = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            lang_bytes[name] = lang_bytes.get(name, 0) + edge["size"]
    total = sum(lang_bytes.values()) or 1
    top_langs = sorted(lang_bytes.items(), key=lambda kv: -kv[1])[:3]
    languages = ", ".join(f"{n} {round(b * 100 / total)}%" for n, b in top_langs) or "-"

    top = max(repos, key=lambda r: (r["stargazerCount"], r["forkCount"]), default=None)
    contrib = u["contributionsCollection"]
    weeks = [sum(d["contributionCount"] for d in w["contributionDays"])
             for w in contrib["contributionCalendar"]["weeks"]]

    return {
        "uptime": uptime(created),
        "languages": languages,
        "repos": u["allRepos"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "forks": sum(r["forkCount"] for r in repos),
        "followers": u["followers"]["totalCount"],
        "commits": all_time_commits(created),
        "contributed": u["repositoriesContributedTo"]["totalCount"],
        "prs": u["pullRequests"]["totalCount"],
        "issues": u["issues"]["totalCount"],
        "top_repo": f"{top['name']} ({top['stargazerCount']} ★)" if top else "-",
        "contributions": contrib["contributionCalendar"]["totalContributions"],
        "reviews": contrib["totalPullRequestReviewContributions"],
        "weeks": weeks,
    }


# ------------------------------------------------------------------------- render

def sparkline(weeks, slots=26):
    """One bar per two-week bucket, separated by spaces."""
    weeks = weeks[-slots * 2:]
    buckets = [sum(weeks[i:i + 2]) for i in range(0, len(weeks), 2)]
    peak = max(buckets, default=0) or 1
    bars = []
    for b in buckets:
        level = 0 if b == 0 else max(1, round(b / peak * (len(SPARK) - 1)))
        bars.append(SPARK[level])
    return " ".join(bars)


def stats_lines(s):
    """Each line is a list of (text, color-key) spans."""
    def header(title):
        label = f" {title} "
        return [("─", "rule"), (label, "title"), ("─" * (WIDTH + 1 - len(label)), "rule")]

    def field(key, value, width=WIDTH, color="text"):
        key, value = f". {key}: ", f" {value}"
        return [(key, "key"), ("." * max(1, width - len(key) - len(value)), "dots"), (value, color)]

    def pair(k1, v1, k2, v2):
        return field(k1, v1, COL, "num") + [(" | ", "rule")] + field(k2, v2, COL, "num")

    return [
        header(f"{USER}@github"),
        field("Uptime", s["uptime"]),
        field("Languages", s["languages"]),
        None,
        header("Contact"),
        field("GitHub", f"github.com/{USER}"),
        None,
        header("GitHub Stats"),
        pair("Repos", s["repos"], "Stars", s["stars"]),
        pair("Forks", s["forks"], "Followers", s["followers"]),
        pair("Commits", s["commits"], "Contributed", s["contributed"]),
        pair("PRs", s["prs"], "Issues", s["issues"]),
        field("Top repo", s["top_repo"]),
        None,
        header("Last 12 Months"),
        pair("Contributions", s["contributions"], "Reviews", s["reviews"]),
        [(sparkline(s["weeks"]), "spark")],
    ]


def render(mode, stats):
    t = THEMES[mode]
    art = (ROOT / "scripts" / "ascii" / f"{mode}.txt").read_text(encoding="utf-8").rstrip("\n").split("\n")
    lines = stats_lines(stats)

    art_w = max(map(len, art)) * ART_CHAR
    text_x = PAD_X + art_w + GAP
    width = round(text_x + (WIDTH + 1) * TEXT_CHAR + PAD_X - 0.4, 1)
    art_h = (len(art) - 1) * ART_LINE
    text_h = (len(lines) - 1) * TEXT_LINE
    height = round(PAD_TOP + max(art_h, text_h) + PAD_BOTTOM, 1)
    text_y = PAD_TOP + (max(art_h, text_h) - text_h) / 2 + TEXT_SIZE / 2

    art_dur = 1.6  # seconds for the whole portrait to draw in
    type_start, type_step = 0.6, 0.12

    css = f"""
    .art {{ opacity: 0; animation: fade .5s ease-out forwards; }}
    .cover {{ animation: type .6s steps(60, end) forwards; }}
    .cursor {{ opacity: 0; animation: show 0s linear forwards, blink 1s step-end infinite; }}
    @keyframes fade {{ from {{ opacity: 0; transform: translateX(-6px); }} to {{ opacity: 1; transform: none; }} }}
    @keyframes type {{ to {{ transform: translateX({width}px); }} }}
    @keyframes show {{ to {{ opacity: 1; }} }}
    @keyframes blink {{ 50% {{ fill: transparent; }} }}
    @media (prefers-reduced-motion: reduce) {{
      .art, .cursor {{ animation: none; opacity: 1; }}
      .cover {{ display: none; }}
    }}"""

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="ASCII GitHub profile card for {escape(USER)}">',
        f"  <style>{css}\n  </style>",
        f'  <clipPath id="card"><rect width="{width}" height="{height}" rx="8"/></clipPath>',
        f'  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="8" fill="{t["bg"]}" stroke="{t["border"]}"/>',
        '  <g clip-path="url(#card)">',
    ]

    for i, line in enumerate(art):
        y = round(PAD_TOP + i * ART_LINE, 1)
        delay = round(i * art_dur / len(art), 2)
        out.append(
            f'    <text class="art" style="animation-delay:{delay}s" x="{PAD_X}" y="{y}" fill="{t["text"]}" '
            f'font-family="{FONT}" xml:space="preserve" font-size="{ART_SIZE}">{escape(line)}</text>'
        )

    n = 0
    last = None
    for i, line in enumerate(lines):
        if not line:
            continue
        y = round(text_y + i * TEXT_LINE, 1)
        spans = "".join(f'<tspan fill="{t[c]}">{escape(txt)}</tspan>' for txt, c in line)
        delay = round(type_start + n * type_step * 3, 2)
        out.append(
            f'    <text x="{round(text_x, 1)}" y="{y}" font-family="{FONT}" xml:space="preserve" '
            f'font-size="{TEXT_SIZE}">{spans}</text>'
        )
        # A background-coloured cover slides right off the line, revealing it like typing.
        out.append(
            f'    <rect class="cover" style="animation-delay:{delay}s" x="{round(text_x - 2, 1)}" '
            f'y="{round(y - TEXT_SIZE, 1)}" width="{width}" height="{TEXT_LINE + 2}" fill="{t["bg"]}"/>'
        )
        n += 1
        last = (y, sum(len(txt) for txt, _ in line))

    if last:
        y, chars = last
        out.append(
            f'    <text class="cursor" style="animation-delay:{round(type_start + n * type_step * 3, 2)}s, '
            f'{round(type_start + n * type_step * 3, 2)}s" x="{round(text_x + (chars + 1) * TEXT_CHAR, 1)}" '
            f'y="{y}" fill="{t["title"]}" font-family="{FONT}" font-size="{TEXT_SIZE}">█</text>'
        )

    out += ["  </g>", "</svg>", ""]
    return "\n".join(out)


def main():
    stats = fetch_stats()
    for mode in THEMES:
        (ROOT / f"{mode}_mode.svg").write_text(render(mode, stats), encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in stats.items() if k != "weeks"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
