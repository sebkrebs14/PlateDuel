"""
Renders batter-vs-pitcher matchup data for every MLB game scheduled today as
a bare, unstyled HTML page - just for visually sanity-checking the data
before any styling work.
"""

import datetime

from bvp_matchups import (
    API_BASE,
    CURRENT_SEASON,
    fetch_json,
    format_game_time,
    format_matchup_line,
    get_all_games,
    get_matchup,
    get_most_active_batters,
    get_season_stat,
    load_stadiums_by_team,
)
from weather import get_wind_effect_for_stadium

OUTPUT_FILE = "bvp_matchups.html"


def row(cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def get_handedness(player_id):
    """Returns (bats, throws) as single-letter codes, e.g. ('L', 'R')."""
    person = fetch_json(f"{API_BASE}/people/{player_id}")["people"][0]
    bats = person.get("batSide", {}).get("code", "?")
    throws = person.get("pitchHand", {}).get("code", "?")
    return bats, throws


def platoon_class(batter_bats, pitcher_throws):
    """Green when the batter has the platoon advantage (opposite-handed vs.
    the pitcher), blue for switch hitters (always opposite-handed)."""
    if batter_bats == "S":
        return "platoon-switch"
    if batter_bats in ("L", "R") and pitcher_throws in ("L", "R") and batter_bats != pitcher_throws:
        return "platoon-advantage"
    return ""


def build_pitcher_section(batters, pitcher_name, pitcher_id):
    _, pitcher_throws = get_handedness(pitcher_id)
    pitcher_stat = get_season_stat(pitcher_id, "pitching")
    if pitcher_stat:
        header = (
            f"vs {pitcher_name} ({pitcher_throws}) &mdash; {CURRENT_SEASON}: "
            f"{pitcher_stat['wins']}-{pitcher_stat['losses']}, "
            f"ERA {pitcher_stat['era']}, WHIP {pitcher_stat['whip']}, "
            f"{pitcher_stat['inningsPitched']} IP, {pitcher_stat['strikeOuts']} SO"
        )
    else:
        header = f"vs {pitcher_name} ({pitcher_throws}) &mdash; no {CURRENT_SEASON} stats found"

    rows = []
    for batter in batters:
        career_stat, season_stat, _ = get_matchup(batter["id"], pitcher_id)
        batter_stat = get_season_stat(batter["id"], "hitting")
        batter_bats, _ = get_handedness(batter["id"])
        season_line = (
            f"AVG {batter_stat['avg']} / HR {batter_stat['homeRuns']} / OPS {batter_stat['ops']}"
            if batter_stat else "-"
        )
        bats_cell = f"<td class='{platoon_class(batter_bats, pitcher_throws)}'>{batter_bats}</td>"
        rows.append(
            "<tr><td>" + batter["fullName"] + "</td>" + bats_cell
            + f"<td>{season_line}</td>"
            + f"<td>{format_matchup_line(career_stat)}</td>"
            + f"<td>{format_matchup_line(season_stat)}</td></tr>"
        )

    table = (
        "<table>"
        "<tr><th>Batter</th><th>Bats</th><th>{season} season</th><th>Career vs pitcher</th>"
        "<th>{season} vs pitcher</th></tr>".format(season=CURRENT_SEASON)
        + "".join(rows)
        + "</table>"
    )
    return f"<h2>{header}</h2>\n{table}"


def build_stadium_line(stadium):
    if not stadium:
        return "<p><em>(no stadiums.json entry for this home team)</em></p>\n", ""

    dimensions = (
        "<p>"
        f"{stadium['stadium']} &middot; ({stadium['latitude']}, {stadium['longitude']}) &middot; "
        f"home plate orientation {stadium['home_plate_orientation_degrees']}&deg; &middot; "
        f"LF {stadium['left_field_distance_feet']}ft / {stadium['left_field_wall_height_feet']}ft wall, "
        f"CF {stadium['center_field_distance_feet']}ft / {stadium['center_field_wall_height_feet']}ft wall, "
        f"RF {stadium['right_field_distance_feet']}ft / {stadium['right_field_wall_height_feet']}ft wall"
        "</p>\n"
    )

    forecast, classification = get_wind_effect_for_stadium(stadium)
    if classification["effect"] == "indoor":
        wind = f"<p class='wind wind-indoor'>{classification['label']}</p>\n"
    elif forecast:
        wind = (
            f"<p class='wind wind-{classification['effect']}'>"
            f"Wind: {forecast['wind_speed_raw']} from {forecast['wind_from_compass']} "
            f"({forecast['period_name']}, {forecast['temperature']}&deg;{forecast['temperature_unit']}, "
            f"{forecast['short_forecast']}) &rarr; {classification['label']}"
            "</p>\n"
        )
    else:
        wind = "<p class='wind wind-unknown'>Wind: forecast unavailable</p>\n"

    return dimensions, wind


def build_game_section(game, stadiums_by_team):
    away = game["teams"]["away"]
    home = game["teams"]["home"]
    game_time = f"<p class='game-time'>{format_game_time(game['gameDate'])}</p>\n"
    matchup_title = f"{away['team']['name']} @ {home['team']['name']} &mdash; {game['venue']['name']}"
    stadium_line, wind_line = build_stadium_line(stadiums_by_team.get(home["team"]["name"]))

    away_pitcher = away.get("probablePitcher")
    home_pitcher = home.get("probablePitcher")
    lineups = game.get("lineups", {})
    away_batters = lineups.get("awayPlayers", [])
    home_batters = lineups.get("homePlayers", [])

    notice = ""
    if not away_batters or not home_batters:
        notice = (
            "<p><em>Lineups not yet posted &mdash; showing each team's "
            "15 most active batters instead.</em></p>\n"
        )
        away_batters = get_most_active_batters(away["team"]["id"])
        home_batters = get_most_active_batters(home["team"]["id"])

    sections = []
    if home_pitcher:
        sections.append(build_pitcher_section(away_batters, home_pitcher["fullName"], home_pitcher["id"]))
    if away_pitcher:
        sections.append(build_pitcher_section(home_batters, away_pitcher["fullName"], away_pitcher["id"]))

    return f"{game_time}<h1>{matchup_title}</h1>\n{stadium_line}{wind_line}{notice}{''.join(sections)}"


def main():
    today = datetime.date.today().isoformat()
    games = get_all_games(today)
    if not games:
        print(f"No games found for {today}")
        return

    stadiums_by_team = load_stadiums_by_team()

    game_sections = []
    for i, game in enumerate(games, start=1):
        away = game["teams"]["away"]["team"]["name"]
        home = game["teams"]["home"]["team"]["name"]
        print(f"[{i}/{len(games)}] {away} @ {home}")
        game_sections.append(build_game_section(game, stadiums_by_team))

    page = f"""{"<hr>".join(game_sections)}
<style>
body, h1, h2, table {{ color: white; }}
table {{ border-collapse: collapse; margin-bottom: 2rem; }}
th, td {{ border: 1px solid #999; padding: 4px 8px; text-align: left; font-family: monospace; }}
.wind {{ font-weight: bold; }}
.wind-hitters {{ color: #ff6b6b; }}
.wind-pitchers {{ color: #6bb3ff; }}
.wind-neutral {{ color: #aaaaaa; }}
.wind-unknown {{ color: #666666; }}
.wind-indoor {{ color: #999999; font-style: italic; }}
.game-time {{ color: #cccccc; font-weight: bold; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0; }}
.platoon-advantage {{ background: #1f8b3a; color: white; }}
.platoon-switch {{ background: #1f6fbf; color: white; }}
</style>"""

    with open(OUTPUT_FILE, "w") as f:
        f.write(page)

    print(f"Wrote {OUTPUT_FILE} ({len(games)} games)")


if __name__ == "__main__":
    main()
