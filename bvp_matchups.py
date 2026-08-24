"""
Fetches batter-vs-pitcher matchup history for every MLB game scheduled today:
for each game, every hitter in each team's posted lineup against the opposing
probable starter, plus each player's own season stat line.

Uses the MLB Stats API's `vsPlayerTotal` stat type for career head-to-head
totals and `vsPlayer` (filtered to the current season) for this year's
matchup history, alongside `season` stats for each batter's/pitcher's overall
2026 performance.
"""

import datetime
import json
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from weather import get_wind_effect_for_stadium

API_BASE = "https://statsapi.mlb.com/api/v1"
CURRENT_SEASON = "2026"
STADIUMS_FILE = "stadiums.json"
MAX_FETCH_RETRIES = 4


def format_game_time(game_date_utc):
    """Converts a 'YYYY-MM-DDTHH:MM:SSZ' UTC timestamp to e.g. '7:05 PM ET'."""
    dt = datetime.datetime.fromisoformat(game_date_utc.replace("Z", "+00:00"))
    eastern = dt.astimezone(ZoneInfo("America/New_York"))
    return eastern.strftime("%-I:%M %p ET")


def fetch_json(url):
    """GETs and parses JSON, retrying on transient network errors (connection
    resets, SSL EOF) - a single flaky request shouldn't kill a run that's
    otherwise making hundreds of these."""
    request = urllib.request.Request(url, headers={"User-Agent": "bvp-matchups-script"})
    for attempt in range(MAX_FETCH_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            if attempt == MAX_FETCH_RETRIES - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def load_stadiums_by_team():
    """Maps team name -> stadium record from stadiums.json (home team owns the venue)."""
    with open(STADIUMS_FILE) as f:
        stadiums = json.load(f)
    return {s["team"]: s for s in stadiums}


def get_stat_leaders(categories, stat_group, limit=10):
    """Season stat leaders (qualified players only) for the given MLB Stats
    API leaderCategories (e.g. ['homeRuns', 'battingAverage']). statGroup
    ('hitting' or 'pitching') is required - without it the API returns
    ambiguous duplicate lists (e.g. pitchers' own batting average) mixed in
    with the real leaders for categories that exist in both stat groups."""
    cats = ",".join(categories)
    url = (
        f"{API_BASE}/stats/leaders?leaderCategories={cats}&season={CURRENT_SEASON}"
        f"&sportId=1&limit={limit}&playerPool=Qualified&statGroup={stat_group}"
    )
    data = fetch_json(url)
    result = {}
    for cat in data.get("leagueLeaders", []):
        result[cat["leaderCategory"]] = [
            {
                "rank": leader["rank"],
                "name": leader["person"]["fullName"],
                "team": leader.get("team", {}).get("name", ""),
                "value": leader["value"],
            }
            for leader in cat.get("leaders", [])
        ]
    return result


def get_all_games(date):
    url = f"{API_BASE}/schedule?sportId=1&date={date}&hydrate=probablePitcher,lineups"
    data = fetch_json(url)
    dates = data.get("dates", [])
    if not dates:
        return []
    return dates[0]["games"]


def get_yesterdays_results():
    """Final scores plus MLB's own top-performer picks (already computed
    server-side, in the boxscore's `topPerformers` field) for each of
    yesterday's completed games."""
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    url = f"{API_BASE}/schedule?sportId=1&date={yesterday}"
    data = fetch_json(url)
    dates = data.get("dates", [])
    if not dates:
        return []

    results = []
    for game in dates[0]["games"]:
        if game["status"]["abstractGameState"] != "Final":
            continue
        away = game["teams"]["away"]["team"]["name"]
        home = game["teams"]["home"]["team"]["name"]
        away_id = game["teams"]["away"]["team"]["id"]
        home_id = game["teams"]["home"]["team"]["id"]

        top_performers = []
        try:
            box = fetch_json(f"{API_BASE}/game/{game['gamePk']}/boxscore")
            for entry in box.get("topPerformers", []):
                player = entry["player"]
                stats = player.get("stats", {})
                summary = stats.get("batting", {}).get("summary") or stats.get("pitching", {}).get("summary")
                if not summary:
                    continue
                team_id = player.get("parentTeamId")
                team_name = away if team_id == away_id else home if team_id == home_id else ""
                top_performers.append({
                    "name": player["person"]["fullName"],
                    "team": team_name,
                    "summary": summary,
                })
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, OSError):
            pass  # a missing boxscore just means no performer lines for this game

        results.append({
            "away": away, "home": home,
            "away_score": game["teams"]["away"]["score"], "home_score": game["teams"]["home"]["score"],
            "away_winner": game["teams"]["away"].get("isWinner", False),
            "venue": game["venue"]["name"],
            "top_performers": top_performers,
        })
    return results


GAMES_IN_SEASON = 162


def get_team_season_stats(stat_group, sit_code=None):
    """Full season stat line for all 30 teams (unsorted), 'hitting' or
    'pitching'. Unlike player leaders, MLB doesn't expose a per-category
    leader list for teams, so ranking by any given stat happens client-side
    once this raw list is fetched. Pass sit_code (e.g. 'i01' for first-inning
    only, from /api/v1/situationCodes) to pull a situational split instead
    of the full-season total - MLB's stats type switches from 'season' to
    'statSplits' whenever a sitCodes filter is supplied."""
    stats_type = "season" if sit_code is None else "statSplits"
    url = f"{API_BASE}/teams/stats?stats={stats_type}&group={stat_group}&season={CURRENT_SEASON}&sportId=1"
    if sit_code:
        url += f"&sitCodes={sit_code}"
    data = fetch_json(url)
    splits = data["stats"][0]["splits"] if data.get("stats") else []
    return [
        {"team": s["team"]["name"], "team_id": s["team"]["id"], "stat": s["stat"]}
        for s in splits
    ]


def get_team_streaks():
    """Current winning/losing streak (2+ games) for every team, from the
    same streak codes (e.g. 'W4', 'L2') standings already surfaces. The
    standings endpoint's team objects carry the short club name (e.g.
    'Phillies') rather than the full name used everywhere else on the site
    (team colors, team page slugs), so names are resolved through the
    canonical id -> full name map instead of trusting this response's own
    team.name field."""
    id_to_name = {v: k for k, v in get_team_id_map().items()}
    url = (
        f"{API_BASE}/standings?leagueId=103,104&season={CURRENT_SEASON}"
        f"&standingsTypes=regularSeason"
    )
    data = fetch_json(url)
    winning, losing = [], []
    for rec in data.get("records", []):
        for t in rec["teamRecords"]:
            code = t.get("streak", {}).get("streakCode", "")
            if len(code) < 2 or not code[1:].isdigit():
                continue
            length = int(code[1:])
            if length < 2:
                continue
            team_id = t["team"]["id"]
            entry = {"team": id_to_name.get(team_id, t["team"]["name"]), "team_id": team_id, "length": length}
            if code[0] == "W":
                winning.append(entry)
            elif code[0] == "L":
                losing.append(entry)
    winning.sort(key=lambda x: x["length"], reverse=True)
    losing.sort(key=lambda x: x["length"], reverse=True)
    return {"winning": winning, "losing": losing}


def get_team_scoring_streaks(team_id):
    """Walks a team's full-season hitting gameLog backward from the most
    recent game, computing current streaks (consecutive games, same pattern
    as the player streak functions above) of: a game with a home run,
    scoring 3+ runs, and scoring 5+ runs."""
    url = f"{API_BASE}/teams/{team_id}/stats?stats=gameLog&group=hitting&season={CURRENT_SEASON}&sportId=1"
    data = fetch_json(url)
    splits = []
    for stat_block in data.get("stats", []):
        if stat_block["type"]["displayName"] == "gameLog":
            splits = stat_block["splits"]

    streaks = {"hr_game": 0, "runs_3plus": 0, "runs_5plus": 0}
    done = dict.fromkeys(streaks, False)
    for g in reversed(splits):
        stat = g["stat"]
        if not done["hr_game"]:
            if stat.get("homeRuns", 0) >= 1:
                streaks["hr_game"] += 1
            else:
                done["hr_game"] = True
        if not done["runs_3plus"]:
            if stat.get("runs", 0) >= 3:
                streaks["runs_3plus"] += 1
            else:
                done["runs_3plus"] = True
        if not done["runs_5plus"]:
            if stat.get("runs", 0) >= 5:
                streaks["runs_5plus"] += 1
            else:
                done["runs_5plus"] = True
    return streaks


def get_team_first_inning_streaks(team_id):
    """Walks a team's full-season schedule (with per-inning linescores)
    backward from the most recent completed game, computing current streaks
    of consecutive games with a run scored in the 1st, and consecutive
    games with a run allowed in the 1st. There's no season-long per-game
    stat for this like gameLog gives for hitting, so this pulls linescores
    for the team's full schedule instead."""
    url = (
        f"{API_BASE}/schedule?sportId=1&teamId={team_id}"
        f"&startDate={CURRENT_SEASON}-01-01&endDate={CURRENT_SEASON}-12-31"
        f"&hydrate=linescore"
    )
    data = fetch_json(url)
    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            if g["status"]["abstractGameState"] != "Final":
                continue
            innings = g.get("linescore", {}).get("innings", [])
            first = next((i for i in innings if i["num"] == 1), None)
            if not first:
                continue
            is_home = g["teams"]["home"]["team"]["id"] == team_id
            side, opp_side = ("home", "away") if is_home else ("away", "home")
            games.append({
                "scored": first[side]["runs"],
                "allowed": first[opp_side]["runs"],
            })

    scored_streak = allowed_streak = 0
    done_scored = done_allowed = False
    for g in reversed(games):
        if not done_scored:
            if g["scored"] >= 1:
                scored_streak += 1
            else:
                done_scored = True
        if not done_allowed:
            if g["allowed"] >= 1:
                allowed_streak += 1
            else:
                done_allowed = True
    return {"scored_1st": scored_streak, "allowed_1st": allowed_streak}


def get_standings():
    """Division standings (with each leader's division magic number) plus
    wild card standings for both leagues. Magic number uses the standard
    formula: games left in a full season, minus the leader's wins, minus
    the second-place team's losses, plus one (clinches outright, no tie)."""
    division_url = (
        f"{API_BASE}/standings?leagueId=103,104&season={CURRENT_SEASON}"
        f"&standingsTypes=regularSeason&hydrate=division,league"
    )
    division_data = fetch_json(division_url)

    divisions = []
    for rec in division_data.get("records", []):
        teams = []
        for t in rec["teamRecords"]:
            lr = t["leagueRecord"]
            teams.append({
                "name": t["team"]["name"],
                "wins": lr["wins"],
                "losses": lr["losses"],
                "pct": lr["pct"],
                "games_back": t["gamesBack"],
                "streak": t.get("streak", {}).get("streakCode", "-"),
                "rank": int(t["divisionRank"]),
            })
        teams.sort(key=lambda x: x["rank"])
        magic_number = None
        if len(teams) >= 2:
            leader, second = teams[0], teams[1]
            magic_number = max(GAMES_IN_SEASON + 1 - leader["wins"] - second["losses"], 0)
        divisions.append({
            "league": rec["league"]["name"],
            "division": rec["division"]["name"],
            "teams": teams,
            "magic_number": magic_number,
        })

    wc_url = (
        f"{API_BASE}/standings?leagueId=103,104&season={CURRENT_SEASON}"
        f"&standingsTypes=wildCard&hydrate=league"
    )
    wc_data = fetch_json(wc_url)
    wild_card = []
    for rec in wc_data.get("records", []):
        teams = []
        for t in rec["teamRecords"]:
            lr = t["leagueRecord"]
            teams.append({
                "name": t["team"]["name"],
                "wins": lr["wins"],
                "losses": lr["losses"],
                "games_back": t.get("wildCardGamesBack", "-"),
                "rank": int(t.get("wildCardRank", 0)),
            })
        teams.sort(key=lambda x: x["rank"])
        wild_card.append({"league": rec["league"]["name"], "teams": teams})

    return {"divisions": divisions, "wild_card": wild_card}


def get_team_id_map():
    """team name -> numeric MLB team id, for the roster/schedule endpoints
    that need an id rather than a name."""
    data = fetch_json(f"{API_BASE}/teams?sportId=1")
    return {t["name"]: t["id"] for t in data.get("teams", [])}


def get_team_roster(team_id):
    """The full 40-man roster, including injured/optioned players (not just
    who's active today) - status is included so the page can show it."""
    data = fetch_json(f"{API_BASE}/teams/{team_id}/roster?rosterType=40Man")
    roster = []
    for entry in data.get("roster", []):
        roster.append({
            "id": entry["person"]["id"],
            "name": entry["person"]["fullName"],
            "jersey": entry.get("jerseyNumber", ""),
            "position": entry.get("position", {}).get("abbreviation", ""),
            "position_type": entry.get("position", {}).get("type", ""),
            "status": entry.get("status", {}).get("description", "Active"),
        })
    roster.sort(key=lambda p: p["name"])
    return roster


def get_team_upcoming_schedule(team_id, days=8):
    """This team's games from tomorrow through `days` out (today's game is
    already covered elsewhere on the team page, so it's excluded here)."""
    start = datetime.date.today() + datetime.timedelta(days=1)
    end = start + datetime.timedelta(days=days)
    url = (
        f"{API_BASE}/schedule?sportId=1&teamId={team_id}"
        f"&startDate={start.isoformat()}&endDate={end.isoformat()}"
    )
    data = fetch_json(url)
    games = []
    for d in data.get("dates", []):
        for g in d["games"]:
            home_id = g["teams"]["home"]["team"]["id"]
            is_home = home_id == team_id
            opponent = g["teams"]["away"]["team"]["name"] if is_home else g["teams"]["home"]["team"]["name"]
            games.append({
                "date": g["officialDate"],
                "opponent": opponent,
                "is_home": is_home,
                "status": g["status"]["detailedState"],
            })
    return games


def _sum_stat_splits(stats):
    """Combines multiple same-shaped stat dicts (e.g. one per team stint) into
    one, summing counting stats and recomputing avg - the API returns
    vsPlayerTotal/vsPlayer as several splits (grouped by team configuration
    over the players' careers) rather than one pre-aggregated total."""
    if not stats:
        return None
    totals = {"atBats": 0, "hits": 0, "homeRuns": 0, "strikeOuts": 0, "plateAppearances": 0}
    for stat in stats:
        for key in totals:
            totals[key] += stat.get(key, 0)
    totals["avg"] = f"{totals['hits'] / totals['atBats']:.3f}".lstrip("0") if totals["atBats"] else "-.--"
    return totals


def get_matchup(batter_id, pitcher_id):
    """Returns (career_stat, season_stat, year_splits) from vsPlayerTotal/vsPlayer.
    career_stat/season_stat may be None. year_splits is a season-ascending list of
    {"season": "2024", "stat": {...}} for every season with at least one PA in the
    matchup - used to show historical trends across years.

    Both vsPlayerTotal and vsPlayer come back from the API as several splits
    (grouped by team configuration across the players' careers), not one
    pre-aggregated total, so career_stat/season_stat are summed across all of
    that type's splits rather than taken from a single split."""
    url = (
        f"{API_BASE}/people/{batter_id}/stats"
        f"?stats=vsPlayerTotal,vsPlayer&opposingPlayerId={pitcher_id}&group=hitting"
    )
    data = fetch_json(url)

    career_stat = None
    season_splits = []
    year_splits = []
    for stat_block in data.get("stats", []):
        stat_type = stat_block["type"]["displayName"]
        if stat_type == "vsPlayerTotal" and stat_block["splits"]:
            career_stat = _sum_stat_splits([s["stat"] for s in stat_block["splits"]])
        elif stat_type == "vsPlayer":
            for split in stat_block["splits"]:
                season = split.get("season")
                stat = split["stat"]
                if season == CURRENT_SEASON:
                    season_splits.append(stat)
                if stat.get("plateAppearances"):
                    year_splits.append({"season": season, "stat": stat})
    season_stat = _sum_stat_splits(season_splits)
    year_splits.sort(key=lambda entry: entry["season"])
    return career_stat, season_stat, year_splits


def get_most_active_batters(team_id, limit=15):
    """Returns up to `limit` position players from the active roster, ranked by
    games played this season - used as a stand-in when the actual lineup for a
    game hasn't been posted yet."""
    roster = fetch_json(f"{API_BASE}/teams/{team_id}/roster?rosterType=active&season={CURRENT_SEASON}")["roster"]
    position_players = [p for p in roster if p["position"]["type"] != "Pitcher"]

    ranked = []
    for player in position_players:
        stat = get_season_stat(player["person"]["id"], "hitting")
        games_played = stat.get("gamesPlayed", 0) if stat else 0
        ranked.append((games_played, player["person"]))

    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [person for _, person in ranked[:limit]]


def get_season_stat(player_id, group):
    url = f"{API_BASE}/people/{player_id}/stats?stats=season&group={group}&season={CURRENT_SEASON}"
    data = fetch_json(url)
    for stat_block in data.get("stats", []):
        if stat_block["type"]["displayName"] == "season" and stat_block["splits"]:
            return stat_block["splits"][0]["stat"]
    return None


def get_career_stat(player_id, group):
    """All-time totals across every season - not scoped to a specific opponent
    or the current year, unlike get_season_stat/get_matchup."""
    url = f"{API_BASE}/people/{player_id}/stats?stats=career&group={group}"
    data = fetch_json(url)
    for stat_block in data.get("stats", []):
        if stat_block["type"]["displayName"] == "career" and stat_block["splits"]:
            return stat_block["splits"][0]["stat"]
    return None


def compute_batter_streaks(splits):
    """Walks a batter's full-season gameLog backward from the most recent
    game, computing several 'currently active' streaks in one pass. Games
    with zero plate appearances (didn't play) are skipped entirely for every
    streak. For the hit/home-run streaks specifically, a plate appearance
    with zero at-bats (walk, HBP, sac bunt) is also skipped rather than
    breaking the streak, matching how MLB officially tracks hitting streaks."""
    streaks = {"hit": 0, "on_base": 0, "hr": 0, "walk": 0, "rbi": 0}
    done = dict.fromkeys(streaks, False)

    for g in reversed(splits):
        stat = g["stat"]
        if not stat.get("plateAppearances"):
            continue
        has_ab = bool(stat.get("atBats"))

        if not done["hit"]:
            if not has_ab:
                pass
            elif stat.get("hits", 0) >= 1:
                streaks["hit"] += 1
            else:
                done["hit"] = True

        if not done["hr"]:
            if not has_ab:
                pass
            elif stat.get("homeRuns", 0) >= 1:
                streaks["hr"] += 1
            else:
                done["hr"] = True

        if not done["on_base"]:
            reached = stat.get("hits", 0) + stat.get("baseOnBalls", 0) + stat.get("hitByPitch", 0)
            if reached >= 1:
                streaks["on_base"] += 1
            else:
                done["on_base"] = True

        if not done["walk"]:
            if stat.get("baseOnBalls", 0) >= 1:
                streaks["walk"] += 1
            else:
                done["walk"] = True

        if not done["rbi"]:
            if stat.get("rbi", 0) >= 1:
                streaks["rbi"] += 1
            else:
                done["rbi"] = True

        if all(done.values()):
            break

    return streaks


def get_recent_form(player_id, last_n=15):
    """Returns (aggregate_totals, games, batter_streaks) for a batter this
    season. aggregate_totals/games cover just the last `last_n` games played;
    batter_streaks is computed from the full season log so it isn't
    artificially capped by last_n. aggregate_totals is None if the player
    hasn't played yet."""
    url = f"{API_BASE}/people/{player_id}/stats?stats=gameLog&group=hitting&season={CURRENT_SEASON}"
    data = fetch_json(url)
    splits = []
    for stat_block in data.get("stats", []):
        if stat_block["type"]["displayName"] == "gameLog":
            splits = stat_block["splits"]

    batter_streaks = compute_batter_streaks(splits)
    recent = splits[-last_n:]
    if not recent:
        return None, [], batter_streaks

    totals = {"atBats": 0, "hits": 0, "homeRuns": 0, "strikeOuts": 0, "rbi": 0}
    for g in recent:
        for key in totals:
            totals[key] += g["stat"].get(key, 0)
    totals["avg"] = f"{totals['hits'] / totals['atBats']:.3f}".lstrip("0") if totals["atBats"] else "-.--"

    games = [
        {"date": g.get("date"), "opponent": (g.get("opponent") or {}).get("name", ""), "summary": g["stat"].get("summary", "")}
        for g in reversed(recent)
    ]
    return totals, games, batter_streaks


def parse_innings_pitched(ip_str):
    """MLB's 'X.Y' innings notation has Y as OUTS (0/1/2), not tenths - '4.1'
    is 4 innings + 1 out. Returns total outs as an int."""
    if not ip_str:
        return 0
    whole, _, frac = str(ip_str).partition(".")
    return int(whole or 0) * 3 + int(frac or 0)


def format_outs_as_ip(outs):
    return f"{outs // 3}.{outs % 3}"


def get_pitcher_streaks(pitcher_id, k_thresholds=(4, 5, 6)):
    """Walks a pitcher's full-season gameLog backward from the most recent
    appearance, returning:
    - the current scoreless-innings streak (consecutive appearances with 0
      earned runs, plus the total innings across them)
    - the current streak of consecutive appearances with at least K strikeouts,
      for each threshold in `k_thresholds`
    - the current win streak: consecutive DECISIONS that were wins (a
      no-decision appearance is skipped rather than breaking the streak; a
      loss breaks it)."""
    url = f"{API_BASE}/people/{pitcher_id}/stats?stats=gameLog&group=pitching&season={CURRENT_SEASON}"
    data = fetch_json(url)
    splits = []
    for stat_block in data.get("stats", []):
        if stat_block["type"]["displayName"] == "gameLog":
            splits = stat_block["splits"]

    scoreless_appearances = 0
    scoreless_outs = 0
    reached_scoreless_end = False
    k_streaks = dict.fromkeys(k_thresholds, 0)
    k_done = dict.fromkeys(k_thresholds, False)
    win_streak = 0
    reached_win_end = False

    for g in reversed(splits):
        stat = g["stat"]
        if not reached_scoreless_end:
            if stat.get("earnedRuns", 0) == 0 and parse_innings_pitched(stat.get("inningsPitched")) > 0:
                scoreless_appearances += 1
                scoreless_outs += parse_innings_pitched(stat.get("inningsPitched"))
            else:
                reached_scoreless_end = True

        for threshold in k_thresholds:
            if k_done[threshold]:
                continue
            if stat.get("strikeOuts", 0) >= threshold:
                k_streaks[threshold] += 1
            else:
                k_done[threshold] = True

        if not reached_win_end:
            if stat.get("wins", 0) >= 1:
                win_streak += 1
            elif stat.get("losses", 0) >= 1:
                reached_win_end = True
            # else: no decision - skip, doesn't break or extend

        if reached_scoreless_end and all(k_done.values()) and reached_win_end:
            break

    last_date = splits[-1].get("date") if splits else None
    return {
        "scoreless_appearances": scoreless_appearances,
        "scoreless_ip": format_outs_as_ip(scoreless_outs),
        "k_streaks": k_streaks,
        "win_streak": win_streak,
        "last_date": last_date,
    }


def format_matchup_line(stat):
    if not stat or not stat.get("plateAppearances"):
        return "no history"
    return (
        f"AB {stat['atBats']:<3} H {stat['hits']:<3} HR {stat['homeRuns']:<2} "
        f"SO {stat['strikeOuts']:<3} AVG {stat['avg']}"
    )


def print_pitcher_season(pitcher_name, stat):
    if not stat:
        print(f"vs {pitcher_name} (probable starter) - no {CURRENT_SEASON} stats found")
        return
    print(
        f"\nvs {pitcher_name} (probable starter) - "
        f"{CURRENT_SEASON} season: {stat['wins']}-{stat['losses']}, "
        f"ERA {stat['era']}, WHIP {stat['whip']}, {stat['inningsPitched']} IP, "
        f"{stat['strikeOuts']} SO, {stat['baseOnBalls']} BB"
    )
    print("-" * 78)


def print_matchups(batters, pitcher_name, pitcher_id):
    pitcher_season_stat = get_season_stat(pitcher_id, "pitching")
    print_pitcher_season(pitcher_name, pitcher_season_stat)

    for batter in batters:
        career_stat, season_stat, _ = get_matchup(batter["id"], pitcher_id)
        batter_season_stat = get_season_stat(batter["id"], "hitting")

        print(f"  {batter['fullName']}")
        if batter_season_stat:
            print(
                f"    {CURRENT_SEASON} season:   "
                f"AVG {batter_season_stat['avg']}  HR {batter_season_stat['homeRuns']}  "
                f"OPS {batter_season_stat['ops']}"
            )
        print(f"    Career vs him: {format_matchup_line(career_stat)}")
        print(f"    {CURRENT_SEASON} vs him:    {format_matchup_line(season_stat)}")


def print_stadium_info(stadium):
    if not stadium:
        print("(no stadiums.json entry for this home team)")
        return
    print(
        f"{stadium['stadium']} @ ({stadium['latitude']}, {stadium['longitude']}) | "
        f"home plate orientation {stadium['home_plate_orientation_degrees']}° | "
        f"LF {stadium['left_field_distance_feet']}ft/{stadium['left_field_wall_height_feet']}ft wall, "
        f"CF {stadium['center_field_distance_feet']}ft/{stadium['center_field_wall_height_feet']}ft wall, "
        f"RF {stadium['right_field_distance_feet']}ft/{stadium['right_field_wall_height_feet']}ft wall"
    )

    forecast, classification = get_wind_effect_for_stadium(stadium)
    if classification["effect"] == "indoor":
        print(classification["label"])
    elif forecast:
        print(
            f"Wind: {forecast['wind_speed_raw']} from {forecast['wind_from_compass']} "
            f"({forecast['period_name']}, {forecast['temperature']}°{forecast['temperature_unit']}, "
            f"{forecast['short_forecast']}) -> {classification['label']}"
        )
    else:
        print("Wind: forecast unavailable")


def print_game(game, stadiums_by_team):
    away = game["teams"]["away"]
    home = game["teams"]["home"]
    print(f"\n{'=' * 78}")
    print(format_game_time(game["gameDate"]))
    print(f"{away['team']['name']} @ {home['team']['name']} - {game['venue']['name']}")
    print_stadium_info(stadiums_by_team.get(home["team"]["name"]))
    print("=" * 78)

    away_pitcher = away.get("probablePitcher")
    home_pitcher = home.get("probablePitcher")
    lineups = game.get("lineups", {})
    away_batters = lineups.get("awayPlayers", [])
    home_batters = lineups.get("homePlayers", [])

    if not away_batters or not home_batters:
        print("Lineups not yet posted - showing each team's 15 most active batters instead.")
        away_batters = get_most_active_batters(away["team"]["id"])
        home_batters = get_most_active_batters(home["team"]["id"])

    if home_pitcher:
        print_matchups(away_batters, home_pitcher["fullName"], home_pitcher["id"])
    if away_pitcher:
        print_matchups(home_batters, away_pitcher["fullName"], away_pitcher["id"])


def main():
    today = datetime.date.today().isoformat()
    games = get_all_games(today)
    if not games:
        print(f"No games found for {today}")
        return

    stadiums_by_team = load_stadiums_by_team()

    print(f"{len(games)} game(s) scheduled for {today}")
    for game in games:
        print_game(game, stadiums_by_team)


if __name__ == "__main__":
    main()
