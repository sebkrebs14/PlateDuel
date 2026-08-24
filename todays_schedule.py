"""
Fetches today's MLB schedule with probable pitchers and lineups (if posted yet).

Lineups are typically not available from MLB until a couple hours before first
pitch, so early in the day most/all games will show "lineup not yet posted".
"""

import datetime
import json
import urllib.request

API_BASE = "https://statsapi.mlb.com/api/v1"


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "todays-schedule-script"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def describe_pitcher(team):
    pitcher = team.get("probablePitcher")
    return pitcher["fullName"] if pitcher else "TBD"


def describe_lineup(players):
    if not players:
        return "not yet posted"
    return ", ".join(p["fullName"] for p in players)


def main():
    today = datetime.date.today().isoformat()
    url = f"{API_BASE}/schedule?sportId=1&date={today}&hydrate=probablePitcher,lineups"
    data = fetch_json(url)

    dates = data.get("dates", [])
    games = dates[0]["games"] if dates else []

    print(f"{today}: {len(games)} game(s)\n")

    for game in games:
        away = game["teams"]["away"]
        home = game["teams"]["home"]
        venue = game["venue"]["name"]
        state = game["status"]["detailedState"]

        print(f"{away['team']['name']} @ {home['team']['name']} - {venue} ({state})")
        print(f"  Probable pitchers: {describe_pitcher(away)} vs {describe_pitcher(home)}")

        lineups = game.get("lineups", {})
        print(f"  Away lineup: {describe_lineup(lineups.get('awayPlayers', []))}")
        print(f"  Home lineup: {describe_lineup(lineups.get('homePlayers', []))}")
        print()


if __name__ == "__main__":
    main()
