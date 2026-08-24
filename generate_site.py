"""
Builds index.html: an interactive front end for today's MLB slate, combining
the schedule, stadium dimensions/orientation, live NWS wind conditions, and
batter-vs-pitcher matchup data (including season-by-season historical trends)
into one self-contained page. No server or build step required - open the
file directly.

Layout: sticky header (date + team search) above a responsive grid of
collapsed game cards. Clicking a card (a native <details> element) expands it
in place into a full-width detail view with a weather panel, a matchup table
per probable starter, and a historical-trends panel for any batter/pitcher
pair with more than one season of head-to-head history.
"""

import datetime
import html
import itertools
import json
import math
import os
import random
import re
import unicodedata

from bvp_matchups import (
    API_BASE,
    CURRENT_SEASON,
    fetch_json,
    format_game_time,
    format_matchup_line,
    get_all_games,
    get_career_stat,
    get_matchup,
    get_most_active_batters,
    get_pitcher_streaks,
    get_recent_form,
    get_season_stat,
    get_standings,
    get_stat_leaders,
    get_team_id_map,
    get_team_first_inning_streaks,
    get_team_roster,
    get_team_scoring_streaks,
    get_team_season_stats,
    get_team_streaks,
    get_team_upcoming_schedule,
    get_yesterdays_results,
    load_stadiums_by_team,
    parse_innings_pitched,
)
from bvp_matchups_html import get_handedness
from weather import get_wind_effect_for_stadium

OUTPUT_FILE = "dashboard.html"
HOME_PAGE_FILE = "index.html"
ROSTER_MOVES_LOOKBACK_DAYS = 3
ROSTER_MOVES_PER_CATEGORY = 30

# Custom domain (no trailing slash) - robots.txt, sitemap.xml, and the
# JSON-LD blocks all build absolute URLs from this.
SITE_BASE_URL = "https://plateduel.com"

PROMOTION_DEMOTION_CODES = {"CU", "OPT", "SE"}  # Recalled, Optioned, Selected

TEAM_ABBR = {
    "Arizona Diamondbacks": "AZ",
    "Athletics": "ATH",
    "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}

# Primary color per team, used for the game-card accent stripe and abbreviation
# pills. Uses 3-letter TEAM_ABBR codes instead of single-letter initials since
# several teams share a first letter (Boston/Baltimore, Texas/Toronto/Tampa Bay).
TEAM_COLORS = {
    "Arizona Diamondbacks": "#a71930",
    "Athletics": "#003831",
    "Atlanta Braves": "#ce1141",
    "Baltimore Orioles": "#df4601",
    "Boston Red Sox": "#bd3039",
    "Chicago Cubs": "#0e3386",
    "Chicago White Sox": "#27251f",
    "Cincinnati Reds": "#c6011f",
    "Cleveland Guardians": "#00385d",
    "Colorado Rockies": "#333366",
    "Detroit Tigers": "#0c2340",
    "Houston Astros": "#eb6e1f",
    "Kansas City Royals": "#004687",
    "Los Angeles Angels": "#ba0021",
    "Los Angeles Dodgers": "#005a9c",
    "Miami Marlins": "#00a3e0",
    "Milwaukee Brewers": "#12284b",
    "Minnesota Twins": "#002b5c",
    "New York Mets": "#002d72",
    "New York Yankees": "#0c2340",
    "Philadelphia Phillies": "#e81828",
    "Pittsburgh Pirates": "#fdb827",
    "San Diego Padres": "#2f241d",
    "San Francisco Giants": "#fd5a1e",
    "Seattle Mariners": "#0c2c56",
    "St. Louis Cardinals": "#c41e3a",
    "Tampa Bay Rays": "#092c5c",
    "Texas Rangers": "#003278",
    "Toronto Blue Jays": "#134a8e",
    "Washington Nationals": "#ab0003",
}

BADGE_LABELS = {
    "hitters": "Favors Hitters",
    "pitchers": "Favors Pitchers",
    "neutral": "Neutral",
    "indoor": "Fixed Roof",
    "unknown": "No Forecast",
}

# Approximate multi-year HR park factors (1.0 = league average), hand-compiled
# from general public reputation - the free MLB Stats API doesn't expose real
# Statcast park factors. Wind is handled separately via live weather, so these
# are meant to capture only the "all else equal" dimension/altitude effect.
PARK_HR_FACTOR = {
    "Coors Field": 1.35,
    "Great American Ball Park": 1.20,
    "Yankee Stadium": 1.15,
    "Citizens Bank Park": 1.12,
    "Daikin Park": 1.10,
    "Chase Field": 1.10,
    "Sutter Health Park": 1.10,
    "American Family Field": 1.08,
    "Rate Field": 1.05,
    "Truist Park": 1.05,
    "Globe Life Field": 1.05,
    "Wrigley Field": 1.02,
    "Fenway Park": 1.00,
    "Angel Stadium": 1.00,
    "UNIQLO Field at Dodger Stadium": 1.00,
    "Nationals Park": 1.00,
    "Rogers Centre": 1.00,
    "Target Field": 0.95,
    "Progressive Field": 0.95,
    "Oriole Park at Camden Yards": 0.95,
    "Tropicana Field": 0.92,
    "Citi Field": 0.92,
    "Comerica Park": 0.88,
    "Busch Stadium": 0.88,
    "Petco Park": 0.88,
    "T-Mobile Park": 0.88,
    "PNC Park": 0.90,
    "loanDepot park": 0.85,
    "Kauffman Stadium": 0.85,
    "Oracle Park": 0.80,
}

LEAGUE_AVG_HR_RATE = 0.032  # ~1 HR per 31 plate appearances, modern-era MLB
LEAGUE_AVG_HR_PER_9 = 1.25
WEATHER_HR_MULTIPLIER = {"hitters": 1.15, "pitchers": 0.85, "neutral": 1.0, "indoor": 1.0, "unknown": 1.0}
PLATOON_SAME_HAND_MULTIPLIER = 0.95
PLATOON_OPPOSITE_HAND_MULTIPLIER = 1.08
PLATOON_SWITCH_MULTIPLIER = 1.05
MIN_PA_FOR_HR_PICKS = 50
HR_PICKS_TOP_N = 25
HR_PICKS_FILE = "hr-picks.html"
PARK_FACTORS_FILE = "park-factors.html"
PLAYERS_DIR = "players"
TEAMS_DIR = "teams"
RECENT_FORM_GAMES = 15


def compute_hr_pick(batter_stat, pitcher_stat, batter_bats, pitcher_throws, park_factor, weather_effect):
    """Composite HR-likelihood score for one batter/pitcher/park/weather combo.
    Returns None when the batter doesn't have enough plate appearances for
    their season HR rate to mean anything. This is a transparent heuristic
    (product of factors relative to league average), not a predictive model -
    treat it as an informed ranking, not a real probability."""
    if not batter_stat or not pitcher_stat:
        return None
    plate_appearances = batter_stat.get("plateAppearances") or 0
    if plate_appearances < MIN_PA_FOR_HR_PICKS:
        return None

    batter_hr_rate = batter_stat["homeRuns"] / plate_appearances
    batter_factor = batter_hr_rate / LEAGUE_AVG_HR_RATE

    pitcher_hr_per_9 = parse_avg(pitcher_stat.get("homeRunsPer9"))
    pitcher_factor = (pitcher_hr_per_9 / LEAGUE_AVG_HR_PER_9) if pitcher_hr_per_9 else 1.0

    weather_factor = WEATHER_HR_MULTIPLIER.get(weather_effect, 1.0)

    if batter_bats == "S":
        platoon_factor, platoon_label = PLATOON_SWITCH_MULTIPLIER, "Switch hitter"
    elif batter_bats and pitcher_throws and batter_bats != pitcher_throws:
        platoon_factor, platoon_label = PLATOON_OPPOSITE_HAND_MULTIPLIER, "Platoon edge"
    else:
        platoon_factor, platoon_label = PLATOON_SAME_HAND_MULTIPLIER, "Same-handed"

    score = batter_factor * pitcher_factor * park_factor * weather_factor * platoon_factor
    return {
        "score": score,
        "season_hr": batter_stat["homeRuns"],
        "plate_appearances": plate_appearances,
        "pitcher_hr_per_9": pitcher_hr_per_9,
        "park_factor": park_factor,
        "weather_effect": weather_effect,
        "platoon_label": platoon_label,
    }


def esc(value):
    return html.escape(str(value))


def brand_icon_svg(size=24):
    """The PlateDuel mark: a home plate split into a batter (red) and
    pitcher (blue) half by a baseball-stitch seam, in the site's existing
    accent colors. Used in place of the old plain brand-dot everywhere."""
    return f'''<svg width="{size}" height="{size}" viewBox="0 0 100 100" class="brand-icon" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <path d="M10 8 H50 V95 L10 50 Z" fill="#ff5266"/>
  <path d="M50 8 H90 V50 L50 95 Z" fill="#6bb3ff"/>
  <path d="M10 8 H90 V50 L50 95 L10 50 Z" fill="none" stroke="#000" stroke-width="7" stroke-linejoin="round"/>
  <g fill="#000">
    <circle cx="50" cy="18" r="3.4"/>
    <circle cx="50" cy="31" r="3.4"/>
    <circle cx="50" cy="44" r="3.4"/>
    <circle cx="50" cy="57" r="3.4"/>
    <circle cx="50" cy="70" r="3.4"/>
    <circle cx="50" cy="83" r="3.4"/>
  </g>
</svg>'''


def slugify(name):
    """'Tyler O'Neill' -> 'tyler-o-neill', 'St. Louis Cardinals' -> 'st-louis-cardinals'."""
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower())
    return slug.strip("-")


def parse_avg(value):
    """Parses an API average/OPS string like '.254' or '1.667' into a float;
    returns None for missing or placeholder ('-.--') values."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


ROSTER_MOVE_CATEGORIES = [
    ("promotions", "Promotions & Demotions"),
    ("trades", "Trades"),
    ("activations", "Injury Activations"),
]


def get_roster_moves():
    """Fetches recent transactions and returns a single flat list, newest
    first, tagged with the category the roster-moves panel cares about:
    promotions/demotions between the majors and minors, trades, and
    activations off the injured list (a 'Status Change' transaction whose
    description says 'activated' - the same typeCode also covers IL
    placements and transfers, which we don't want)."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=ROSTER_MOVES_LOOKBACK_DAYS)
    url = f"{API_BASE}/transactions?sportId=1&startDate={start.isoformat()}&endDate={end.isoformat()}"
    try:
        data = fetch_json(url)
    except Exception:
        return []

    transactions = sorted(data.get("transactions", []), key=lambda t: t.get("date", ""), reverse=True)

    moves = []
    counts = {"promotions": 0, "trades": 0, "activations": 0}
    for t in transactions:
        description = t.get("description")
        if not description:
            continue
        type_code = t.get("typeCode")
        if type_code in PROMOTION_DEMOTION_CODES:
            category = "promotions"
        elif type_code == "TR":
            category = "trades"
        elif type_code == "SC" and "activated" in description.lower():
            category = "activations"
        else:
            continue
        if counts[category] >= ROSTER_MOVES_PER_CATEGORY:
            continue
        counts[category] += 1

        teams = {team["name"] for team in (t.get("fromTeam"), t.get("toTeam")) if team}
        moves.append({
            "date": t.get("date"),
            "description": esc(description),
            "player": esc((t.get("person") or {}).get("fullName", "")),
            "teams": sorted(teams),
            "category": category,
        })

    moves.sort(key=lambda m: m["date"], reverse=True)
    return moves


def format_matchup_line_html(stat):
    if not stat or not stat.get("plateAppearances"):
        return "no history"
    return (
        f"AB {stat['atBats']} / H {stat['hits']} / HR {stat['homeRuns']} / "
        f"SO {stat['strikeOuts']} / AVG {stat['avg']}"
    )


def matchup_numbers(stat):
    """Extracts sortable numeric fields from a vsPlayerTotal/vsPlayer stat dict.
    Returns all-None when there's no recorded plate appearance (no history)."""
    if not stat or not stat.get("plateAppearances"):
        return {"ab": None, "h": None, "hr": None, "so": None, "avg": None}
    return {
        "ab": stat.get("atBats"),
        "h": stat.get("hits"),
        "hr": stat.get("homeRuns"),
        "so": stat.get("strikeOuts"),
        "avg": parse_avg(stat.get("avg")),
    }


def row(cells):
    tds = []
    for c in cells:
        if isinstance(c, tuple):
            value, cls = c
            tds.append(f"<td class='{cls}'>{value}</td>" if cls else f"<td>{value}</td>")
        else:
            tds.append(f"<td>{c}</td>")
    return "<tr>" + "".join(tds) + "</tr>"


def bats_class(batter_bats, pitcher_throws):
    """Green when the batter has the platoon advantage (opposite-handed vs. the
    pitcher), blue for switch hitters (always get the platoon advantage)."""
    if batter_bats == "S":
        return "bats-switch"
    if batter_bats and pitcher_throws and batter_bats != pitcher_throws:
        return "bats-platoon"
    return ""


def build_weather_panel(stadium):
    if not stadium:
        return "<p><em>No stadiums.json entry for this home team.</em></p>", "unknown", "No data"

    dims = (
        f"<p class='dims'>{esc(stadium['stadium'])} &middot; "
        f"({stadium['latitude']}, {stadium['longitude']}) &middot; "
        f"home plate orientation {stadium['home_plate_orientation_degrees']}&deg;<br>"
        f"LF {stadium['left_field_distance_feet']}ft / {stadium['left_field_wall_height_feet']}ft wall &middot; "
        f"CF {stadium['center_field_distance_feet']}ft / {stadium['center_field_wall_height_feet']}ft wall &middot; "
        f"RF {stadium['right_field_distance_feet']}ft / {stadium['right_field_wall_height_feet']}ft wall</p>"
    )

    forecast, classification = get_wind_effect_for_stadium(stadium)
    effect = classification["effect"]
    if effect == "indoor":
        detail = f"<p class='wind wind-{effect}'>{esc(classification['label'])}</p>"
    elif forecast:
        detail = (
            f"<p class='wind wind-{effect}'>"
            f"Wind: {esc(forecast['wind_speed_raw'])} from {esc(forecast['wind_from_compass'])} "
            f"({esc(forecast['period_name'])}, {forecast['temperature']}&deg;{esc(forecast['temperature_unit'])}, "
            f"{esc(forecast['short_forecast'])}) &rarr; {esc(classification['label'])}</p>"
        )
    else:
        detail = f"<p class='wind wind-{effect}'>Wind: forecast unavailable</p>"

    return f"<div class='weather-panel'>{dims}{detail}</div>", effect, BADGE_LABELS[effect], classification, forecast


def build_matchup_section(
    batters, pitcher_name, pitcher_id, trend_entries, master_rows, game_label,
    hr_pick_rows=None, batting_team=None, venue=None, weather_effect="unknown", park_factor=1.0,
    player_pages=None, team_pages=None, opponent_team=None, game_time=None, is_home=False,
):
    _, pitcher_throws = get_handedness(pitcher_id)

    if team_pages is not None and batting_team is not None:
        team_pages.setdefault(batting_team, {
            "team": batting_team, "opponent": opponent_team, "venue": venue,
            "game_time": game_time, "opponent_pitcher": pitcher_name, "is_home": is_home,
            "opponent_pitcher_throws": pitcher_throws, "weather_effect": weather_effect,
            "lineup_rows": [], "hr_picks": [], "recent_moves": [],
        })
    pitcher_stat = get_season_stat(pitcher_id, "pitching")
    if pitcher_stat:
        header = (
            f"vs {esc(pitcher_name)} ({pitcher_throws}) &mdash; {CURRENT_SEASON}: "
            f"{pitcher_stat['wins']}-{pitcher_stat['losses']}, ERA {pitcher_stat['era']}, "
            f"WHIP {pitcher_stat['whip']}, {pitcher_stat['inningsPitched']} IP, {pitcher_stat['strikeOuts']} SO"
        )
    else:
        header = f"vs {esc(pitcher_name)} ({pitcher_throws}) &mdash; no {CURRENT_SEASON} stats found"

    rows = []
    for batter in batters:
        career_stat, season_stat, year_splits = get_matchup(batter["id"], pitcher_id)
        batter_stat = get_season_stat(batter["id"], "hitting")
        batter_bats, _ = get_handedness(batter["id"])
        season_line = (
            f"AVG {batter_stat['avg']} / HR {batter_stat['homeRuns']} / OPS {batter_stat['ops']}"
            if batter_stat else "-"
        )
        player_slug = slugify(batter["fullName"])
        rows.append(row([
            f"<a class='player-link' href='{PLAYERS_DIR}/{player_slug}.html'>{esc(batter['fullName'])}</a>",
            (batter_bats, bats_class(batter_bats, pitcher_throws)),
            season_line,
            format_matchup_line_html(career_stat),
            format_matchup_line_html(season_stat),
        ]))

        if len({entry["season"] for entry in year_splits}) >= 2:
            trend_entries.append({
                "batter": batter["fullName"],
                "pitcher": pitcher_name,
                "splits": year_splits,
            })

        career_nums = matchup_numbers(career_stat)
        current_nums = matchup_numbers(season_stat)

        if team_pages is not None and batting_team is not None:
            team_pages[batting_team]["lineup_rows"].append({
                "player": batter["fullName"], "bats": batter_bats,
                "season_avg": batter_stat["avg"] if batter_stat else None,
                "season_hr": batter_stat["homeRuns"] if batter_stat else None,
                "season_ops": batter_stat["ops"] if batter_stat else None,
            })

        if player_pages is not None:
            career_stat_all = get_career_stat(batter["id"], "hitting")
            recent_totals, recent_games, batter_streaks = get_recent_form(batter["id"], last_n=RECENT_FORM_GAMES)
            player_pages[batter["fullName"]] = {
                "player": batter["fullName"], "bats": batter_bats, "team": batting_team,
                "season_avg": batter_stat["avg"] if batter_stat else None,
                "season_hr": batter_stat["homeRuns"] if batter_stat else None,
                "season_ops": batter_stat["ops"] if batter_stat else None,
                "season_pa": batter_stat["plateAppearances"] if batter_stat else None,
                "opponent_pitcher": pitcher_name, "pitcher_throws": pitcher_throws,
                "opponent_team": opponent_team, "venue": venue, "game_time": game_time,
                "weather_effect": weather_effect,
                "career_nums": career_nums,
                "year_splits": year_splits,
                "hr_pick_rank": None, "hr_pick_score": None,
                "career_stat_all": career_stat_all,
                "recent_totals": recent_totals, "recent_games": recent_games,
                "streaks": batter_streaks,
            }
        master_rows.append({
            "game": game_label,
            "batter": batter["fullName"], "bats": batter_bats,
            "pitcher": pitcher_name, "throws": pitcher_throws,
            "season_avg": parse_avg(batter_stat["avg"]) if batter_stat else None,
            "season_hr": batter_stat["homeRuns"] if batter_stat else None,
            "season_ops": parse_avg(batter_stat["ops"]) if batter_stat else None,
            "career_ab": career_nums["ab"], "career_h": career_nums["h"],
            "career_hr": career_nums["hr"], "career_so": career_nums["so"], "career_avg": career_nums["avg"],
            "cur_ab": current_nums["ab"], "cur_h": current_nums["h"],
            "cur_hr": current_nums["hr"], "cur_so": current_nums["so"], "cur_avg": current_nums["avg"],
        })

        if hr_pick_rows is not None:
            pick = compute_hr_pick(batter_stat, pitcher_stat, batter_bats, pitcher_throws, park_factor, weather_effect)
            if pick:
                hr_pick_rows.append({
                    "player": batter["fullName"],
                    "team": batting_team,
                    "bats": batter_bats,
                    "opponent_pitcher": pitcher_name,
                    "pitcher_throws": pitcher_throws,
                    "venue": venue,
                    "game": game_label,
                    **pick,
                })

    table = (
        "<table><tr><th>Batter</th><th>Bats</th>"
        f"<th>{CURRENT_SEASON} season</th><th>Career vs pitcher</th><th>{CURRENT_SEASON} vs pitcher</th></tr>"
        + "".join(rows) + "</table>"
    )
    return f"<h3>{header}</h3>{table}"


def build_trends_panel(trend_entries):
    if not trend_entries:
        return (
            "<div class='trends-panel'><p><em>No batter has faced either starter "
            "across multiple seasons - nothing to trend yet.</em></p></div>"
        )

    blocks = []
    for entry in trend_entries:
        # Splits are per stint against this pitcher, not one per season (a
        # batter can rack up several same-season entries) - merge by season.
        by_season = {}
        for split in entry["splits"]:
            totals = by_season.setdefault(split["season"], {"atBats": 0, "hits": 0, "homeRuns": 0, "strikeOuts": 0})
            stat = split["stat"]
            totals["atBats"] += stat.get("atBats", 0)
            totals["hits"] += stat.get("hits", 0)
            totals["homeRuns"] += stat.get("homeRuns", 0)
            totals["strikeOuts"] += stat.get("strikeOuts", 0)
        year_rows = "".join(
            row([season, format_matchup_line({
                "plateAppearances": t["atBats"], "atBats": t["atBats"], "hits": t["hits"],
                "homeRuns": t["homeRuns"], "strikeOuts": t["strikeOuts"],
                "avg": f"{t['hits'] / t['atBats']:.3f}" if t["atBats"] else "-",
            })])
            for season, t in sorted(by_season.items())
        )
        blocks.append(
            f"<div class='trend-block'><h4>{esc(entry['batter'])} vs {esc(entry['pitcher'])}</h4>"
            f"<table><tr><th>Season</th><th>Line</th></tr>{year_rows}</table></div>"
        )
    return f"<div class='trends-panel'>{''.join(blocks)}</div>"


def build_sports_events_jsonld(games, finished_game_labels):
    """JSON-LD SportsEvent list for today's not-yet-finished games, matching
    exactly what's rendered on the dashboard (finished games are dropped
    from the page itself, so they're dropped here too rather than leaving
    structured data that describes content no longer on the page)."""
    events = []
    for game in games:
        away = game["teams"]["away"]["team"]["name"]
        home = game["teams"]["home"]["team"]["name"]
        label = f"{TEAM_ABBR.get(away, away)} @ {TEAM_ABBR.get(home, home)}"
        if label in finished_game_labels:
            continue
        events.append({
            "@context": "https://schema.org",
            "@type": "SportsEvent",
            "name": f"{away} at {home}",
            "startDate": game["gameDate"],
            "sport": "Baseball",
            "homeTeam": {"@type": "SportsTeam", "name": home},
            "awayTeam": {"@type": "SportsTeam", "name": away},
            "location": {"@type": "Place", "name": game["venue"]["name"]},
        })
    return json.dumps(events).replace("</", "<\\/")


def build_game_card(game, stadiums_by_team, master_rows, hr_pick_rows, player_pages=None, team_pages=None, pitcher_infos=None, weather_rows=None):
    away = game["teams"]["away"]["team"]["name"]
    home = game["teams"]["home"]["team"]["name"]
    venue = game["venue"]["name"]
    game_time = format_game_time(game["gameDate"])
    game_label = f"{TEAM_ABBR.get(away, away)} @ {TEAM_ABBR.get(home, home)}"

    weather_html, effect, badge_label, classification, forecast = build_weather_panel(stadiums_by_team.get(home))
    park_factor = PARK_HR_FACTOR.get(venue, 1.0)

    if weather_rows is not None:
        angle_diff = classification.get("angle_diff_degrees")
        speed = forecast["wind_speed_mph"] if forecast else None
        wind_score = (
            speed * math.cos(math.radians(angle_diff))
            if angle_diff is not None and speed is not None
            else None
        )
        weather_rows.append({
            "away": away, "home": home, "venue": venue, "game_time": game_time,
            "effect": effect, "label": classification.get("label", badge_label),
            "wind_speed_mph": speed, "angle_diff_degrees": angle_diff, "wind_score": wind_score,
        })

    away_pitcher = game["teams"]["away"].get("probablePitcher")
    home_pitcher = game["teams"]["home"].get("probablePitcher")

    if pitcher_infos is not None:
        for pitcher, team, opponent in [(away_pitcher, away, home), (home_pitcher, home, away)]:
            if pitcher and pitcher["id"] not in pitcher_infos:
                _, throws = get_handedness(pitcher["id"])
                pitcher_infos[pitcher["id"]] = {
                    "id": pitcher["id"], "name": pitcher["fullName"], "throws": throws,
                    "team": team, "opponent": opponent, "venue": venue, "game_time": game_time,
                }

    lineups = game.get("lineups", {})
    away_batters = lineups.get("awayPlayers", [])
    home_batters = lineups.get("homePlayers", [])

    notice = ""
    if not away_batters or not home_batters:
        notice = (
            "<p class='notice'><em>Lineups not yet posted - showing each team's "
            "15 most active batters instead.</em></p>"
        )
        away_batters = get_most_active_batters(game["teams"]["away"]["team"]["id"])
        home_batters = get_most_active_batters(game["teams"]["home"]["team"]["id"])

    trend_entries = []
    matchup_sections = []
    if home_pitcher:
        matchup_sections.append(build_matchup_section(
            away_batters, home_pitcher["fullName"], home_pitcher["id"], trend_entries, master_rows, game_label,
            hr_pick_rows=hr_pick_rows, batting_team=away, venue=venue,
            weather_effect=effect, park_factor=park_factor,
            player_pages=player_pages, team_pages=team_pages, opponent_team=home, game_time=game_time, is_home=False,
        ))
    if away_pitcher:
        matchup_sections.append(build_matchup_section(
            home_batters, away_pitcher["fullName"], away_pitcher["id"], trend_entries, master_rows, game_label,
            hr_pick_rows=hr_pick_rows, batting_team=home, venue=venue,
            weather_effect=effect, park_factor=park_factor,
            player_pages=player_pages, team_pages=team_pages, opponent_team=away, game_time=game_time, is_home=True,
        ))

    pitcher_names = " / ".join(
        p["fullName"] for p in (away_pitcher, home_pitcher) if p
    ) or "TBD"

    player_names = " ".join(
        p["fullName"] for p in [away_pitcher, home_pitcher, *away_batters, *home_batters] if p
    )
    search_key = esc(f"{away} {home} {venue} {player_names}".lower())
    away_color = TEAM_COLORS.get(away, "#333")
    home_color = TEAM_COLORS.get(home, "#333")

    return f"""<details class="game-card" data-search="{search_key}" style="--team-accent: {away_color}; --team-accent-home: {home_color}">
<summary>
  <div class="team-dots">
    <span class="team-dot" style="background: {away_color}">{esc(TEAM_ABBR.get(away, away))}</span>
    <span class="vs">@</span>
    <span class="team-dot" style="background: {home_color}">{esc(TEAM_ABBR.get(home, home))}</span>
  </div>
  <div class="summary-main">
    <span class="matchup">
      <a class="team-link" href="{TEAMS_DIR}/{slugify(away)}.html" onclick="event.stopPropagation()">{esc(away)}</a>
      @
      <a class="team-link" href="{TEAMS_DIR}/{slugify(home)}.html" onclick="event.stopPropagation()">{esc(home)}</a>
    </span>
    <span class="meta">{esc(game_time)} &middot; {esc(venue)}</span>
    <span class="meta pitchers">{esc(pitcher_names)}</span>
  </div>
  <span class="badge badge-{effect}">{esc(badge_label)}</span>
</summary>
<div class="detail">
  <section class="weather-section">
    <h3 class="section-label">Weather</h3>
    {weather_html}
  </section>
  <section class="matchup-sections">
    <h3 class="section-label">Matchups</h3>
    {notice}
    {''.join(matchup_sections)}
  </section>
  <section class="trends-section">
    <h3 class="section-label">Historical Trends</h3>
    {build_trends_panel(trend_entries)}
  </section>
</div>
</details>"""


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Today's Matchups - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="{meta_description}">
<script type="application/ld+json">{sports_events_jsonld}</script>
</head>
<body>
<header class="site-header">
  <div class="site-header-top">
    <div class="brand">
      {brand_icon}
      <h1>MLB Matchup Dashboard</h1>
    </div>
    <span class="date">{date_label}</span>
    <a class="header-link" href="{home_page}">{home_icon}Home</a>
  </div>
  <input id="search" type="text" placeholder="Search teams, venue, or player..." autocomplete="off">
</header>
<main class="games-grid" id="games-grid">
{cards}
</main>
<p class="empty-state" id="empty-state" hidden>No games match your search.</p>

<section class="all-matchups">
  <div class="all-matchups-header">
    <h2>All Matchups</h2>
    <button type="button" id="opposite-hand-toggle" class="toggle-btn">Opposite-hand matchups only</button>
  </div>
  <p class="table-hint">Sorted by career hits vs pitcher by default. Click a column header to sort. Click Bats or Throws to cycle through L / R / S filters. Rows with no recorded history sort to the bottom.</p>
  <div class="table-scroll">
    <table id="matchup-table">
      <thead><tr id="matchup-group"></tr><tr id="matchup-head"></tr></thead>
      <tbody id="matchup-body"></tbody>
    </table>
  </div>
</section>

<script id="matchup-data" type="application/json">{matchup_data_json}</script>
<script>
const search = document.getElementById('search');
const cards = Array.from(document.querySelectorAll('.game-card'));
const emptyState = document.getElementById('empty-state');

let searchQuery = '';

search.addEventListener('input', () => {{
  searchQuery = search.value.trim().toLowerCase();
  let visible = 0;
  for (const card of cards) {{
    const match = card.dataset.search.includes(searchQuery);
    card.style.display = match ? '' : 'none';
    if (match) visible++;
  }}
  emptyState.hidden = visible !== 0;
  renderBody();
}});

const MATCHUP_COLUMNS = [
  {{key: 'game', label: 'Game', type: 'text'}},
  {{key: 'batter', label: 'Batter', type: 'text'}},
  {{key: 'bats', label: 'Bats', type: 'text'}},
  {{key: 'pitcher', label: 'Pitcher', type: 'text'}},
  {{key: 'throws', label: 'Throws', type: 'text'}},
  {{key: 'season_avg', label: "'{season_short} AVG", type: 'avg'}},
  {{key: 'season_hr', label: "'{season_short} HR", type: 'int'}},
  {{key: 'season_ops', label: "'{season_short} OPS", type: 'avg'}},
  {{key: 'career_ab', label: 'AB', type: 'int', group: 'Career vs P'}},
  {{key: 'career_h', label: 'H', type: 'int', group: 'Career vs P'}},
  {{key: 'career_hr', label: 'HR', type: 'int', group: 'Career vs P'}},
  {{key: 'career_so', label: 'SO', type: 'int', group: 'Career vs P'}},
  {{key: 'career_avg', label: 'AVG', type: 'avg', group: 'Career vs P'}},
  {{key: 'cur_ab', label: 'AB', type: 'int', group: "'{season_short} VS P"}},
  {{key: 'cur_h', label: 'H', type: 'int', group: "'{season_short} VS P"}},
  {{key: 'cur_hr', label: 'HR', type: 'int', group: "'{season_short} VS P"}},
  {{key: 'cur_so', label: 'SO', type: 'int', group: "'{season_short} VS P"}},
  {{key: 'cur_avg', label: 'AVG', type: 'avg', group: "'{season_short} VS P"}},
];

const HAND_CYCLE = {{bats: ['L', 'R', 'S'], throws: ['L', 'R']}};

const matchupRows = JSON.parse(document.getElementById('matchup-data').textContent);
const groupRow = document.getElementById('matchup-group');
const headRow = document.getElementById('matchup-head');
const body = document.getElementById('matchup-body');
let sortKey = 'career_h';
let sortDir = -1;
let handFilters = {{bats: null, throws: null}};
let oppositeHandOnly = false;

function formatCell(value, type) {{
  if (value === null || value === undefined) return '-';
  if (type === 'avg') {{
    const fixed = value.toFixed(3);
    return fixed.startsWith('0.') ? fixed.slice(1) : fixed;
  }}
  return value;
}}

function renderGroupHead() {{
  groupRow.innerHTML = '';
  let i = 0;
  while (i < MATCHUP_COLUMNS.length) {{
    const group = MATCHUP_COLUMNS[i].group;
    let span = 1;
    while (i + span < MATCHUP_COLUMNS.length && MATCHUP_COLUMNS[i + span].group === group) span++;
    const th = document.createElement('th');
    th.colSpan = span;
    if (group) {{
      th.textContent = group;
      th.classList.add('group-header');
    }}
    groupRow.appendChild(th);
    i += span;
  }}
}}

function renderHead() {{
  headRow.innerHTML = '';
  for (const col of MATCHUP_COLUMNS) {{
    const th = document.createElement('th');
    const cycle = HAND_CYCLE[col.key];
    if (cycle) {{
      const active = handFilters[col.key];
      th.textContent = col.label + (active ? `: ${{active}}` : '');
      th.title = 'Click to cycle L / R / S filter';
      th.addEventListener('click', () => {{
        const idx = active === null ? -1 : cycle.indexOf(active);
        handFilters[col.key] = idx + 1 < cycle.length ? cycle[idx + 1] : null;
        renderHead();
        renderBody();
      }});
    }} else {{
      th.textContent = col.label + (sortKey === col.key ? (sortDir === 1 ? ' ▲' : ' ▼') : '');
      th.title = 'Click to sort';
      th.addEventListener('click', () => {{
        if (sortKey === col.key) {{
          sortDir *= -1;
        }} else {{
          sortKey = col.key;
          sortDir = 1;
        }}
        renderHead();
        renderBody();
      }});
    }}
    headRow.appendChild(th);
  }}
}}

function renderBody() {{
  let filtered = searchQuery
    ? matchupRows.filter(r => (
        r.batter.toLowerCase().includes(searchQuery) ||
        r.pitcher.toLowerCase().includes(searchQuery) ||
        r.game.toLowerCase().includes(searchQuery)
      ))
    : matchupRows;
  if (handFilters.bats) filtered = filtered.filter(r => r.bats === handFilters.bats);
  if (handFilters.throws) filtered = filtered.filter(r => r.throws === handFilters.throws);
  if (oppositeHandOnly) filtered = filtered.filter(r => r.bats && r.throws && (r.bats === 'S' || r.bats !== r.throws));
  const sorted = filtered.slice().sort((a, b) => {{
    const av = a[sortKey], bv = b[sortKey];
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (typeof av === 'string') return av.localeCompare(bv) * sortDir;
    return (av - bv) * sortDir;
  }});

  body.innerHTML = '';
  for (const r of sorted) {{
    const tr = document.createElement('tr');
    for (const col of MATCHUP_COLUMNS) {{
      const td = document.createElement('td');
      td.textContent = formatCell(r[col.key], col.type);
      if (col.key === 'bats') {{
        if (r.bats === 'S') td.classList.add('bats-switch');
        else if (r.bats && r.throws && r.bats !== r.throws) td.classList.add('bats-platoon');
      }}
      tr.appendChild(td);
    }}
    body.appendChild(tr);
  }}
}}

renderGroupHead();
renderHead();
renderBody();

const oppositeHandToggle = document.getElementById('opposite-hand-toggle');
oppositeHandToggle.addEventListener('click', () => {{
  oppositeHandOnly = !oppositeHandOnly;
  oppositeHandToggle.classList.toggle('active', oppositeHandOnly);
  renderBody();
}});

</script>

<style>
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.site-header {{
  position: sticky; top: 0; z-index: 10;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.85rem 1.25rem 0.75rem;
}}
.site-header-top {{
  display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;
  margin-bottom: 0.6rem;
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.site-header h1 {{ font-size: 1.15rem; margin: 0; white-space: nowrap; }}
.site-header .date {{ color: #999; font-size: 0.9rem; white-space: nowrap; }}

.header-link {{
  background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.header-link:first-of-type {{ margin-left: auto; }}
.header-link:hover {{ border-color: #ff5266; color: #ff5266; }}
.site-header input {{
  display: block; width: 100%; box-sizing: border-box;
  background: #0d0d0d; border: 1px solid #333; color: #f2f2f2;
  padding: 0.5rem 0.75rem; border-radius: 7px; font-size: 0.9rem;
  transition: border-color 0.15s ease;
}}
.site-header input:focus {{ outline: none; border-color: #ff5266; }}

.move-section {{ margin-bottom: 1.25rem; }}
.move-section:last-child {{ margin-bottom: 0; }}
.move-section h3 {{
  font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em;
  color: #999; margin: 0 0 0.5rem;
}}
.move-section ul {{ list-style: none; margin: 0; padding: 0; }}
.move-section li {{
  font-size: 0.85rem; padding: 0.4rem 0; border-bottom: 1px solid #222; line-height: 1.4;
}}
.move-section li:last-child {{ border-bottom: none; }}
.move-date {{ color: #6bb3ff; font-family: ui-monospace, monospace; font-size: 0.78rem; margin-right: 0.6rem; }}
.move-empty {{ color: #777; font-style: italic; }}

.games-grid {{
  display: grid; grid-template-columns: repeat(6, 1fr);
  gap: 0.75rem; padding: 1.25rem;
}}
@media (max-width: 1400px) {{ .games-grid {{ grid-template-columns: repeat(4, 1fr); }} }}
@media (max-width: 900px) {{ .games-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
@media (max-width: 520px) {{ .games-grid {{ grid-template-columns: 1fr; }} }}
.game-card {{
  background: #161616; border: 1px solid #262626;
  border-left: 3px solid var(--team-accent, #333); border-right: 3px solid var(--team-accent-home, #333);
  border-radius: 10px; overflow: hidden;
}}
.game-card[open] {{ grid-column: 1 / -1; }}
.game-card summary {{
  list-style: none; cursor: pointer; padding: 0.85rem;
  display: flex; flex-direction: column; align-items: flex-start; gap: 0.55rem;
}}
.game-card summary::-webkit-details-marker {{ display: none; }}
.team-dots {{ display: flex; align-items: center; gap: 8px; }}
.team-dot {{
  display: inline-block; padding: 2px 8px; border-radius: 999px; color: #fff;
  font-size: 11px; font-weight: 700; letter-spacing: 0.02em; flex-shrink: 0;
  font-family: inherit;
}}
.team-dots .vs {{ color: #666; font-size: 12px; }}
.summary-main {{ display: flex; flex-direction: column; gap: 0.2rem; }}
.matchup {{ font-weight: 700; font-size: 0.88rem; line-height: 1.3; }}
.player-link, .team-link {{
  color: inherit; text-decoration: none; border-bottom: 1px solid transparent;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.player-link:hover, .team-link:hover {{ color: #ff5266; border-bottom-color: #ff5266; }}
.meta {{ color: #999; font-size: 0.72rem; }}
.meta.pitchers {{ color: #bbb; }}

.badge {{
  flex-shrink: 0; font-size: 0.66rem; font-weight: 700; padding: 0.25rem 0.55rem;
  border-radius: 999px; white-space: nowrap; text-transform: uppercase; letter-spacing: 0.03em;
}}
.badge-hitters {{ background: rgba(74,222,128,0.15); color: #4ade80; }}
.badge-pitchers {{ background: rgba(107,179,255,0.15); color: #6bb3ff; }}
.badge-neutral {{ background: rgba(170,170,170,0.15); color: #aaaaaa; }}
.badge-indoor {{ background: rgba(153,153,153,0.15); color: #999999; }}
.badge-unknown {{ background: rgba(102,102,102,0.15); color: #666666; }}

.detail {{ border-top: 1px solid #262626; padding: 1.1rem; display: flex; flex-direction: column; gap: 1.5rem; }}
.section-label {{ margin: 0 0 0.6rem; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: #888; }}

.weather-panel .dims {{ margin: 0 0 0.4rem; color: #ccc; font-size: 0.88rem; line-height: 1.5; }}
.wind {{ font-weight: 600; margin: 0; font-size: 0.85rem; display: flex; align-items: center; gap: 6px; }}
.wind-hitters {{ color: #4ade80; }}
.wind-pitchers {{ color: #6bb3ff; }}
.wind-neutral {{ color: #aaaaaa; }}
.wind-unknown {{ color: #666666; }}
.wind-indoor {{ color: #999999; font-style: italic; }}

.notice {{ color: #ccc; margin: 0 0 0.75rem; }}
.matchup-sections h3 {{ font-size: 0.95rem; margin: 1rem 0 0.5rem; }}
.matchup-sections h3:first-of-type {{ margin-top: 0; }}

table {{ border-collapse: collapse; width: 100%; margin-bottom: 0.75rem; font-size: 0.85rem; }}
th, td {{ border: none; border-bottom: 1px solid #222; padding: 6px 10px; text-align: left; font-family: ui-monospace, monospace; }}
th {{ border-bottom: 1px solid #333; color: #aaa; font-family: -apple-system, sans-serif; font-size: 0.78rem; text-transform: uppercase; }}

.trends-panel {{ display: flex; flex-direction: column; gap: 1rem; }}
.trend-block h4 {{ margin: 0 0 0.4rem; font-size: 0.85rem; color: #ddd; }}
.trend-block table {{ max-width: 420px; }}

.empty-state {{ text-align: center; color: #888; padding: 3rem 1rem; }}

.all-matchups {{ padding: 0 1.25rem 2rem; }}
.all-matchups-header {{
  display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
}}
.all-matchups h2 {{ font-size: 1.1rem; margin: 0 0 0.25rem; }}
.table-hint {{ color: #888; font-size: 0.8rem; margin: 0 0 0.75rem; }}
.toggle-btn {{
  background: #1a1a1a; border: 1px solid #333; color: #ccc;
  padding: 0.45rem 0.8rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  cursor: pointer; white-space: nowrap; transition: all 0.15s ease; flex-shrink: 0;
}}
.toggle-btn:hover {{ border-color: #ff5266; color: #ff5266; }}
.toggle-btn.active {{ background: rgba(255,82,102,0.15); border-color: #ff5266; color: #ff5266; }}
.table-scroll {{ overflow: auto; max-height: 70vh; border: 1px solid #262626; border-radius: 8px; }}
#matchup-table {{ margin-bottom: 0; font-size: 0.8rem; }}
#matchup-table th {{
  position: sticky; background: #1a1a1a; cursor: pointer; user-select: none;
  white-space: nowrap; z-index: 2;
}}
#matchup-group th {{ top: 0; cursor: default; text-align: center; color: #999; }}
#matchup-head th {{ top: 28px; }}
#matchup-head th:hover {{ color: #f2f2f2; }}
#matchup-table td {{ white-space: nowrap; }}
.group-header {{
  border-bottom: 1px solid #3a3a3a; font-size: 0.72rem;
  text-transform: uppercase; letter-spacing: 0.04em;
}}

.bats-platoon {{ background: rgba(74,222,128,0.18); color: #4ade80; font-weight: 600; }}
.bats-switch {{ background: rgba(107,179,255,0.18); color: #6bb3ff; font-weight: 600; }}
</style>
</body>
</html>
"""

ROSTER_MOVES_FILE = "roster-moves.html"
GAME_PAGE_FILE = "162-0-challenge.html"

ROSTER_MOVES_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Roster Moves - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Recent MLB roster moves - call-ups, injuries, and transactions across the league.">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>Roster Moves</h1>
  </div>
  <span class="date">Last {lookback_days} days</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<div class="filters-bar">
  <select id="team-filter">
    <option value="">All Teams</option>
    {team_options}
  </select>
  <select id="type-filter">
    <option value="">All Move Types</option>
    {type_options}
  </select>
  <input id="player-search" type="text" placeholder="Search player or team..." autocomplete="off">
</div>
<main class="roster-moves-main"><ul class="move-list" id="move-list"></ul></main>
<p class="empty-state" id="empty-state" hidden>No moves match your filters.</p>

<script id="roster-moves-data" type="application/json">{roster_moves_json}</script>
<script id="roster-moves-category-labels" type="application/json">{category_labels_json}</script>
<script>
const allMoves = JSON.parse(document.getElementById('roster-moves-data').textContent);
const categoryLabels = JSON.parse(document.getElementById('roster-moves-category-labels').textContent);
const moveList = document.getElementById('move-list');
const emptyState = document.getElementById('empty-state');
const teamFilter = document.getElementById('team-filter');
const typeFilter = document.getElementById('type-filter');
const playerSearch = document.getElementById('player-search');

function matchesFilters(entry, team, type, query) {{
  if (team && !entry.teams.includes(team)) return false;
  if (type && entry.category !== type) return false;
  if (query) {{
    const haystack = (entry.player + ' ' + entry.teams.join(' ') + ' ' + entry.description).toLowerCase();
    if (!haystack.includes(query)) return false;
  }}
  return true;
}}

function render() {{
  const team = teamFilter.value;
  const type = typeFilter.value;
  const query = playerSearch.value.trim().toLowerCase();

  const entries = allMoves.filter(e => matchesFilters(e, team, type, query));
  moveList.innerHTML = entries.map(e => {{
    return `<li><span class="move-date">${{e.date}}</span>` +
      `<span class="move-type move-type-${{e.category}}">${{categoryLabels[e.category]}}</span>` +
      `${{e.description}}</li>`;
  }}).join('');
  emptyState.hidden = entries.length !== 0;
}}

teamFilter.addEventListener('change', render);
typeFilter.addEventListener('change', render);
playerSearch.addEventListener('input', render);
render();
</script>

<style>
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.site-header {{
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.site-header h1 {{ font-size: 1.15rem; margin: 0; white-space: nowrap; }}
.site-header .date {{ color: #999; font-size: 0.9rem; white-space: nowrap; }}
.header-link {{
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.header-link:hover {{ border-color: #ff5266; color: #ff5266; }}

.filters-bar {{
  display: flex; gap: 0.75rem; flex-wrap: wrap;
  max-width: 800px; margin: 1.25rem auto 0; padding: 0 1.25rem;
}}
.filters-bar select, .filters-bar input {{
  background: #0d0d0d; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.7rem; border-radius: 6px; font-size: 0.9rem;
}}
.filters-bar select {{ flex: 0 1 200px; }}
.filters-bar input {{ flex: 1 1 220px; min-width: 0; }}
.filters-bar select:focus, .filters-bar input:focus {{ outline: none; border-color: #6bb3ff; }}

@media (max-width: 520px) {{
  .header-link {{ margin-right: auto; }}
  #team-filter, #type-filter {{ margin-left: auto; margin-right: auto; }}
}}

.roster-moves-main {{ max-width: 800px; margin: 0 auto; padding: 1rem 1.25rem 1.5rem; }}
.empty-state {{ max-width: 800px; margin: 0 auto; padding: 0 1.25rem; color: #888; }}
.move-list {{ list-style: none; margin: 0; padding: 0; }}
.move-list li {{
  font-size: 0.9rem; padding: 0.55rem 0; border-bottom: 1px solid #222; line-height: 1.4;
}}
.move-list li:last-child {{ border-bottom: none; }}
.move-date {{ color: #6bb3ff; font-family: ui-monospace, monospace; font-size: 0.8rem; margin-right: 0.6rem; }}
.move-type {{
  display: inline-block; font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; margin-right: 0.6rem;
}}
.move-type-promotions {{ background: rgba(107,179,255,0.15); color: #6bb3ff; }}
.move-type-trades {{ background: rgba(245,166,35,0.18); color: #f5a623; }}
.move-type-activations {{ background: rgba(74,222,128,0.15); color: #4ade80; }}
</style>
</body>
</html>
"""


def build_roster_moves_page(moves):
    team_options = "\n".join(
        f"<option value='{esc(name)}'>{esc(name)}</option>" for name in sorted(TEAM_ABBR)
    )
    type_options = "\n".join(
        f"<option value='{key}'>{esc(label)}</option>" for key, label in ROSTER_MOVE_CATEGORIES
    )
    category_labels = dict(ROSTER_MOVE_CATEGORIES)
    roster_moves_json = json.dumps(moves).replace("</", "<\\/")
    category_labels_json = json.dumps(category_labels).replace("</", "<\\/")

    return ROSTER_MOVES_PAGE_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        lookback_days=ROSTER_MOVES_LOOKBACK_DAYS,
        team_options=team_options,
        type_options=type_options,
        roster_moves_json=roster_moves_json,
        category_labels_json=category_labels_json,
    )


HR_PICKS_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>HR Picks - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Today's best MLB home run picks, ranked by matchup strength and ballpark factors.">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>Today's Home Run Picks</h1>
  </div>
  <span class="date">{date_label}</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<p class="disclaimer">
  Ranked by a composite score - season HR rate, the opposing pitcher's HR/9 allowed, this
  stadium's approximate park factor, today's live wind reading, and batter/pitcher handedness -
  each relative to league average. This is a statistical heuristic, not a prediction: home runs
  are high-variance events and a high score means "well-supported by the numbers," not "likely."
  Batters need at least {min_pa} plate appearances this season to qualify.
</p>
<main class="hr-picks-main">
  <div class="table-scroll">
    <table>
      <tr>
        <th>#</th><th>Score</th><th>Player</th><th>Bats</th><th>Team</th><th>Opp. Pitcher</th>
        <th>{season} HR</th><th>PA</th><th>Pitcher HR/9</th><th>Park</th><th>Venue</th><th>Wind</th><th>Platoon</th>
      </tr>
      {rows}
    </table>
  </div>
</main>

<style>
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.site-header {{
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.site-header h1 {{ font-size: 1.15rem; margin: 0; white-space: nowrap; }}
.site-header .date {{ color: #999; font-size: 0.9rem; white-space: nowrap; }}
.header-link {{
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.header-link:hover {{ border-color: #ff5266; color: #ff5266; }}

.disclaimer {{
  max-width: 1100px; margin: 1.25rem auto 0; padding: 0.75rem 1rem;
  color: #aaa; font-size: 0.85rem; line-height: 1.5;
  background: rgba(255,82,102,0.06); border-left: 3px solid #ff5266; border-radius: 0 8px 8px 0;
}}

.hr-picks-main {{ max-width: none; margin: 0 auto; padding: 1rem 0.75rem 1.5rem; }}
.table-scroll {{ overflow: auto; border: 1px solid #262626; border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.8rem; }}
th, td {{ border: none; border-bottom: 1px solid #222; padding: 6px 7px; text-align: left; font-family: ui-monospace, monospace; white-space: nowrap; }}
th {{ position: sticky; top: 0; background: #1a1a1a; border-bottom: 1px solid #333; }}
tr:first-child td, tr:first-child th {{ font-weight: 700; }}
tr:last-child td {{ border-bottom: none; }}
.rank-1, .rank-2, .rank-3 {{ color: #ffd166; font-weight: 700; }}
.wind-hitters {{ color: #4ade80; }}
.wind-pitchers {{ color: #6bb3ff; }}
.wind-neutral, .wind-indoor, .wind-unknown {{ color: #999999; }}
.player-link, .team-link {{
  color: inherit; text-decoration: none; border-bottom: 1px solid transparent;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.player-link:hover, .team-link:hover {{ color: #ff5266; border-bottom-color: #ff5266; }}

/* --- mobile: reflow the table into stat cards, no markup changes needed --- */
@media (max-width: 640px) {{
  .table-scroll {{ border: none; background: transparent; overflow: visible; }}
  table {{ border: none; }}
  table tr:first-child {{ display: none; }}
  table, tbody, tr, td {{ display: block; width: 100%; box-sizing: border-box; }}
  tr {{
    position: relative; background: #14141a; border: 1px solid #262626; border-radius: 10px;
    padding: 10px 14px 12px; margin-bottom: 10px;
  }}
  td {{
    border: none; padding: 0; white-space: normal;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}

  td:nth-child(1) {{
    position: absolute; top: 10px; left: 10px; width: 22px; height: 22px; border-radius: 50%;
    background: rgba(255,209,102,0.15); color: #ffd166; font-weight: 700; font-size: 12px;
    display: flex; align-items: center; justify-content: center;
  }}
  td:nth-child(1).rank-1, td:nth-child(1).rank-2, td:nth-child(1).rank-3 {{
    background: #ffd166; color: #1a1500;
  }}
  td:nth-child(2) {{
    position: absolute; top: 8px; right: 14px; width: auto; font-size: 17px; font-weight: 700; color: #ff5266;
  }}
  td:nth-child(3) {{
    display: inline; font-size: 14px; font-weight: 600; color: #f2f2f2; padding-left: 32px;
  }}
  td:nth-child(4), td:nth-child(5) {{ display: inline; font-size: 12px; color: #888; }}
  td:nth-child(4)::before, td:nth-child(5)::before {{ content: ' · '; color: #555; }}
  td:nth-child(6) {{ display: block; margin-top: 3px; padding-left: 32px; font-size: 12px; color: #888; }}
  td:nth-child(6)::before {{ content: 'vs '; }}
  td:nth-child(7) {{ display: inline; font-size: 12px; color: #888; }}
  td:nth-child(7)::before {{ content: ' · '; color: #555; }}
  td:nth-child(7)::after {{ content: ' HR'; }}
  td:nth-child(8), td:nth-child(9), td:nth-child(10), td:nth-child(13) {{ display: none; }}
  td:nth-child(11) {{ display: inline; font-size: 12px; color: #888; }}
  td:nth-child(11)::before {{ content: ' · '; color: #555; }}
  td:nth-child(12) {{
    display: inline-block; margin-top: 6px; margin-left: 32px; font-size: 10.5px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.03em; padding: 2px 9px; border-radius: 999px;
  }}
  td:nth-child(12).wind-hitters {{ background: rgba(74,222,128,0.15); }}
  td:nth-child(12).wind-pitchers {{ background: rgba(107,179,255,0.15); }}
  td:nth-child(12).wind-neutral, td:nth-child(12).wind-indoor, td:nth-child(12).wind-unknown {{ background: rgba(170,170,170,0.12); }}
}}
</style>
</body>
</html>
"""


def build_hr_picks_page(picks, date_label):
    rows = []
    for rank, pick in enumerate(picks, start=1):
        player_slug = slugify(pick["player"])
        team_slug = slugify(pick["team"] or "")
        rows.append(row([
            (rank, f"rank-{rank}" if rank <= 3 else ""),
            f"{pick['score']:.2f}",
            f"<a class='player-link' href='{PLAYERS_DIR}/{player_slug}.html'>{esc(pick['player'])}</a>",
            pick["bats"] or "-",
            f"<a class='team-link' href='{TEAMS_DIR}/{team_slug}.html'>{esc(TEAM_ABBR.get(pick['team'], pick['team'] or '-'))}</a>",
            f"{esc(pick['opponent_pitcher'])} ({pick['pitcher_throws']})",
            pick["season_hr"],
            pick["plate_appearances"],
            f"{pick['pitcher_hr_per_9']:.2f}" if pick["pitcher_hr_per_9"] is not None else "-",
            f"{pick['park_factor']:.2f}",
            esc(pick["venue"]),
            (BADGE_LABELS[pick["weather_effect"]], f"wind-{pick['weather_effect']}"),
            pick["platoon_label"],
        ]))

    return HR_PICKS_PAGE_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        date_label=date_label,
        season=CURRENT_SEASON,
        min_pa=MIN_PA_FOR_HR_PICKS,
        rows="\n".join(rows),
    )


PARK_FACTORS_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Park Factors - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="MLB ballpark factors for every stadium - how each park affects home runs and scoring.">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>Park Factors</h1>
  </div>
  <span class="date">{date_label}</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<p class="disclaimer">
  A park factor above 1.00 means a stadium historically produces more home runs than a
  neutral park; below 1.00 means fewer. This is the same figure already used in the HR
  picks composite score. All 30 MLB parks, ranked highest to lowest.
</p>
<main class="park-main">
  <div class="park-list" id="park-list">
{cards}
  </div>
</main>

<style>
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.site-header {{
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.site-header h1 {{ font-size: 1.15rem; margin: 0; white-space: nowrap; }}
.site-header .date {{ color: #999; font-size: 0.9rem; white-space: nowrap; }}
.header-link {{
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.header-link:hover {{ border-color: #ff5266; color: #ff5266; }}

.disclaimer {{
  max-width: 900px; margin: 1.25rem auto 0; padding: 0.75rem 1rem;
  color: #aaa; font-size: 0.85rem; line-height: 1.5;
  background: rgba(255,82,102,0.06); border-left: 3px solid #ff5266; border-radius: 0 8px 8px 0;
}}

.park-main {{ max-width: 900px; margin: 0 auto; padding: 1rem 1.25rem 1.5rem; }}
.park-list {{ display: flex; flex-direction: column; gap: 0.6rem; }}
.park-card {{
  display: flex; align-items: center; gap: 0.9rem;
  background: #14141a; border: 1px solid #262626; border-left: 3px solid var(--team-accent, #333);
  border-radius: 10px; padding: 0.75rem 1rem;
}}
.park-rank {{
  flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%;
  background: #1a1a1a; color: #888; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}}
.park-body {{ flex: 1; min-width: 0; }}
.park-head {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
.park-name {{ font-size: 0.95rem; font-weight: 600; color: #f5f5f5; }}
.park-team {{ font-size: 0.78rem; color: #999; }}
.park-dims {{ margin: 3px 0 0; font-size: 0.78rem; color: #999; line-height: 1.5; }}
.park-factor {{ flex-shrink: 0; text-align: right; }}
.factor-value {{ display: block; font-size: 1.3rem; font-weight: 700; font-family: ui-monospace, monospace; }}
.factor-label {{
  display: block; font-size: 0.66rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.03em; margin-top: 2px;
}}
.factor-hitters .factor-value, .factor-hitters .factor-label {{ color: #4ade80; }}
.factor-pitchers .factor-value, .factor-pitchers .factor-label {{ color: #6bb3ff; }}
.factor-neutral .factor-value, .factor-neutral .factor-label {{ color: #999999; }}

@media (max-width: 560px) {{
  .park-card {{ flex-wrap: wrap; }}
  .park-factor {{ margin-left: auto; text-align: right; }}
}}
</style>
</body>
</html>
"""


def classify_park_factor(factor):
    if factor >= 1.05:
        return "factor-hitters", "Favors hitters"
    if factor <= 0.90:
        return "factor-pitchers", "Favors pitchers"
    return "factor-neutral", "Neutral"


def build_park_factors_page(stadiums_by_team, date_label):
    entries = []
    for team, record in stadiums_by_team.items():
        stadium_name = record["stadium"]
        factor = PARK_HR_FACTOR.get(stadium_name)
        if factor is None:
            continue
        entries.append({"team": team, "stadium": stadium_name, "factor": factor, "dims": record})
    entries.sort(key=lambda e: e["factor"], reverse=True)

    cards = []
    for rank, e in enumerate(entries, start=1):
        color = TEAM_COLORS.get(e["team"], "#333")
        cls, label = classify_park_factor(e["factor"])
        dims = e["dims"]
        dims_line = (
            f"LF {dims['left_field_distance_feet']}ft / {dims['left_field_wall_height_feet']}ft wall &middot; "
            f"CF {dims['center_field_distance_feet']}ft / {dims['center_field_wall_height_feet']}ft wall &middot; "
            f"RF {dims['right_field_distance_feet']}ft / {dims['right_field_wall_height_feet']}ft wall &middot; "
            f"orientation {dims['home_plate_orientation_degrees']}&deg;"
        )
        cards.append(f"""    <div class="park-card" style="--team-accent: {color}">
      <div class="park-rank">{rank}</div>
      <div class="park-body">
        <div class="park-head">
          <span class="park-name">{esc(e['stadium'])}</span>
          <span class="park-team">{esc(e['team'])}</span>
        </div>
        <p class="park-dims">{dims_line}</p>
      </div>
      <div class="park-factor {cls}">
        <span class="factor-value">{e['factor']:.2f}</span>
        <span class="factor-label">{label}</span>
      </div>
    </div>""")

    return PARK_FACTORS_PAGE_TEMPLATE.format(brand_icon=brand_icon_svg(), home_icon=brand_icon_svg(16), date_label=date_label, cards="\n".join(cards))


WEATHER_PAGE_FILE = "weather-watch.html"

WEATHER_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Weather Watch - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Wind and weather conditions for today's MLB games, and how they affect scoring.">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>Weather Watch</h1>
  </div>
  <span class="date">{date_label}</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<p class="disclaimer">
  Today's games ranked by wind, from most hitter-aided to most pitcher-suppressed. The wind
  score is wind speed (mph) scaled by how directly it's blowing out toward center field versus
  in from it - a strong crosswind scores near zero even at high speed. Indoor and no-data games
  are listed separately, unranked.
</p>
<main class="park-main">
  <div class="park-list" id="park-list">
{cards}
  </div>
{extra_section}
</main>

<style>
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.site-header {{
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.site-header h1 {{ font-size: 1.15rem; margin: 0; white-space: nowrap; }}
.site-header .date {{ color: #999; font-size: 0.9rem; white-space: nowrap; }}
.header-link {{
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.header-link:hover {{ border-color: #ff5266; color: #ff5266; }}

.disclaimer {{
  max-width: 900px; margin: 1.25rem auto 0; padding: 0.75rem 1rem;
  color: #aaa; font-size: 0.85rem; line-height: 1.5;
  background: rgba(255,82,102,0.06); border-left: 3px solid #ff5266; border-radius: 0 8px 8px 0;
}}

.park-main {{ max-width: 900px; margin: 0 auto; padding: 1rem 1.25rem 1.5rem; }}
.park-list {{ display: flex; flex-direction: column; gap: 0.6rem; }}
.park-card {{
  display: flex; align-items: center; gap: 0.9rem;
  background: #14141a; border: 1px solid #262626; border-left: 3px solid var(--team-accent, #333);
  border-radius: 10px; padding: 0.75rem 1rem;
}}
.park-rank {{
  flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%;
  background: #1a1a1a; color: #888; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}}
.park-body {{ flex: 1; min-width: 0; }}
.park-head {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
.park-name {{ font-size: 0.95rem; font-weight: 600; color: #f5f5f5; }}
.park-team {{ font-size: 0.78rem; color: #999; }}
.park-dims {{ margin: 3px 0 0; font-size: 0.78rem; color: #999; line-height: 1.5; }}
.park-factor {{ flex-shrink: 0; text-align: right; }}
.factor-value {{ display: block; font-size: 1.3rem; font-weight: 700; font-family: ui-monospace, monospace; }}
.factor-label {{
  display: block; font-size: 0.66rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.03em; margin-top: 2px;
}}
.factor-hitters .factor-value, .factor-hitters .factor-label {{ color: #4ade80; }}
.factor-pitchers .factor-value, .factor-pitchers .factor-label {{ color: #6bb3ff; }}
.factor-neutral .factor-value, .factor-neutral .factor-label {{ color: #999999; }}
.empty-note {{ margin: 0; font-size: 0.85rem; color: #777; font-style: italic; padding: 0.5rem 0.25rem; }}
.section-label {{ margin: 1.5rem 0 0.6rem; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: #888; }}

@media (max-width: 560px) {{
  .park-card {{ flex-wrap: wrap; }}
  .park-factor {{ margin-left: auto; text-align: right; }}
}}
</style>
</body>
</html>
"""


def build_weather_digest_page(weather_rows, date_label):
    def team_matchup_card(r, rank=None, show_factor=True):
        color = TEAM_COLORS.get(r["home"], "#333")
        rank_html = f'<div class="park-rank">{rank}</div>' if rank is not None else ""
        factor_html = ""
        if show_factor:
            cls = {"hitters": "factor-hitters", "pitchers": "factor-pitchers"}.get(r["effect"], "factor-neutral")
            value = f"{r['wind_speed_mph']:.0f} mph" if r["wind_speed_mph"] is not None else "-"
            factor_html = (
                f'<div class="park-factor {cls}">'
                f'<span class="factor-value">{value}</span>'
                f'<span class="factor-label">{BADGE_LABELS[r["effect"]]}</span>'
                "</div>"
            )
        return f"""    <div class="park-card" style="--team-accent: {color}">
      {rank_html}
      <div class="park-body">
        <div class="park-head">
          <span class="park-name">{esc(TEAM_ABBR.get(r['away'], r['away']))} @ {esc(TEAM_ABBR.get(r['home'], r['home']))}</span>
          <span class="park-team">{esc(r['venue'])} &middot; {esc(r['game_time'])}</span>
        </div>
        <p class="park-dims">{esc(r['label'])}</p>
      </div>
      {factor_html}
    </div>"""

    ranked = sorted(
        (r for r in weather_rows if r["wind_score"] is not None),
        key=lambda r: r["wind_score"], reverse=True,
    )
    unranked = [r for r in weather_rows if r["wind_score"] is None]

    cards = "\n".join(team_matchup_card(r, rank=i) for i, r in enumerate(ranked, start=1))
    if not cards:
        cards = "    <p class='empty-note'>No wind data available for today's games.</p>"

    extra_section = ""
    if unranked:
        extra_cards = "\n".join(team_matchup_card(r, show_factor=False) for r in unranked)
        extra_section = (
            '  <h2 class="section-label">Indoor / No Forecast</h2>\n'
            f'  <div class="park-list">\n{extra_cards}\n  </div>'
        )

    return WEATHER_PAGE_TEMPLATE.format(brand_icon=brand_icon_svg(), home_icon=brand_icon_svg(16), date_label=date_label, cards=cards, extra_section=extra_section)


PLAYER_TEAM_SHARED_STYLE = """
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.site-header {{
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.site-header h1 {{ font-size: 1.15rem; margin: 0; white-space: nowrap; }}
.site-header .date {{ color: #999; font-size: 0.9rem; white-space: nowrap; }}
.header-link {{
  background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.header-link:first-of-type {{ margin-left: auto; }}
.header-link:hover {{ border-color: #ff5266; color: #ff5266; }}

.player-main, .team-main {{ max-width: 700px; margin: 0 auto; padding: 1.25rem; display: flex; flex-direction: column; gap: 1rem; }}

.stat-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.6rem; }}
.stat-card {{
  background: #14141a; border: 1px solid #262626; border-radius: 10px;
  padding: 0.75rem 0.6rem; text-align: center;
}}
.stat-label {{ display: block; font-size: 0.68rem; color: #888; text-transform: uppercase; letter-spacing: 0.03em; }}
.stat-value {{ display: block; font-size: 1.2rem; font-weight: 700; margin-top: 3px; font-family: ui-monospace, monospace; }}
@media (max-width: 480px) {{ .stat-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}

.panel {{ background: #14141a; border: 1px solid #262626; border-radius: 10px; padding: 1rem; }}
.section-label {{ margin: 0 0 0.6rem; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: #888; }}

.today-row {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }}
.today-line {{ margin: 0; font-size: 0.9rem; color: #eee; }}
.today-sub {{ margin: 3px 0 0; font-size: 0.8rem; font-weight: 600; }}
.wind-hitters {{ color: #4ade80; }}
.wind-pitchers {{ color: #6bb3ff; }}
.wind-neutral, .wind-indoor, .wind-unknown {{ color: #999999; }}
.pick-badge {{
  flex-shrink: 0; background: rgba(255,209,102,0.12); color: #ffd166; text-decoration: none;
  font-size: 0.78rem; font-weight: 700; padding: 0.4rem 0.75rem; border-radius: 999px;
  white-space: nowrap;
}}

.career-line {{ margin: 0 0 0.75rem; font-size: 0.85rem; color: #ccc; font-family: ui-monospace, monospace; }}
.trend-table, .lineup-table {{ border-collapse: collapse; width: 100%; font-size: 0.82rem; }}
.trend-table th, .trend-table td, .lineup-table th, .lineup-table td {{
  border-bottom: 1px solid #222; padding: 6px 8px; text-align: left; font-family: ui-monospace, monospace;
}}
.trend-table th, .lineup-table th {{ color: #888; font-family: -apple-system, sans-serif; font-size: 0.72rem; text-transform: uppercase; }}
.trend-table tr:last-child td, .lineup-table tr:last-child td {{ border-bottom: none; }}
.roster-status-out {{ color: #ff9e6b; }}

.player-link, .team-link {{
  color: inherit; text-decoration: none; border-bottom: 1px solid transparent;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.player-link:hover, .team-link:hover {{ color: #ff5266; border-bottom-color: #ff5266; }}

.empty-note {{ margin: 0; font-size: 0.82rem; color: #777; font-style: italic; line-height: 1.5; }}

.pick-list {{ list-style: none; margin: 0 0 0.75rem; padding: 0; }}
.pick-list li {{
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  padding: 0.4rem 0; border-bottom: 1px solid #222; font-size: 0.88rem;
}}
.pick-list li:last-child {{ border-bottom: none; }}
.pick-rank {{ color: #ffd166; font-size: 0.78rem; font-family: ui-monospace, monospace; white-space: nowrap; }}

.move-mini-list {{ list-style: none; margin: 0 0 0.75rem; padding: 0; }}
.move-mini-list li {{ padding: 0.4rem 0; border-bottom: 1px solid #222; font-size: 0.85rem; line-height: 1.4; }}
.move-mini-list li:last-child {{ border-bottom: none; }}
.move-date {{ color: #6bb3ff; font-family: ui-monospace, monospace; font-size: 0.78rem; margin-right: 0.6rem; }}

.see-all-link {{ display: inline-block; font-size: 0.82rem; color: #999; text-decoration: none; }}
.see-all-link:hover {{ color: #ff5266; }}
"""

PLAYER_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{player_name} - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="{meta_description}">
<script type="application/ld+json">{player_jsonld}</script>
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>{player_name}</h1>
  </div>
  <span class="date">{bats} &middot; {team}</span>
  <a class="header-link" href="../index.html">{home_icon}Home</a>
  <a class="header-link" href="../{team_link}">&larr; {team}</a>
</header>

<main class="player-main">
  <section class="stat-grid">
    <div class="stat-card"><span class="stat-label">{season} AVG</span><span class="stat-value">{avg}</span></div>
    <div class="stat-card"><span class="stat-label">{season} HR</span><span class="stat-value">{hr}</span></div>
    <div class="stat-card"><span class="stat-label">{season} OPS</span><span class="stat-value">{ops}</span></div>
    <div class="stat-card"><span class="stat-label">{season} PA</span><span class="stat-value">{pa}</span></div>
  </section>

  <section class="panel">
    <h3 class="section-label">Today</h3>
    <div class="today-row">
      <div>
        <p class="today-line">vs {opponent_pitcher} ({pitcher_throws}) &middot; {venue} &middot; {game_time}</p>
        <p class="today-sub wind-{weather_effect}">{weather_label}</p>
      </div>
      {pick_badge}
    </div>
  </section>

  <section class="panel">
    <h3 class="section-label">Career vs {opponent_pitcher}</h3>
    {career_content}
  </section>

  <section class="panel">
    <h3 class="section-label">Recent form (last {recent_n} games)</h3>
    {recent_form_content}
  </section>

  <section class="panel">
    <h3 class="section-label">Full career stats</h3>
    {career_stats_content}
  </section>
</main>


<style>""" + PLAYER_TEAM_SHARED_STYLE + """</style>
</body>
</html>
"""

TEAM_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{team_name} - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="{meta_description}">
<script type="application/ld+json">{team_jsonld}</script>
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>{team_name}</h1>
  </div>
  <span class="date">Today: {location_prefix} {opponent}</span>
  <a class="header-link" href="../index.html">{home_icon}Home</a>
</header>

<main class="team-main">
  <section class="panel">
    <h3 class="section-label">Today's game</h3>
    <p class="today-line">{matchup_line} &middot; {game_time} &middot; {venue}</p>
    <p class="today-sub">{pitcher_line}</p>
    <p class="today-sub wind-{weather_effect}">{weather_label}</p>
  </section>

  <section class="panel">
    <h3 class="section-label">In today's HR picks</h3>
    {hr_picks_content}
  </section>

  <section class="panel">
    <h3 class="section-label">Today's lineup vs {opponent_pitcher}</h3>
    {lineup_content}
  </section>

  <section class="panel">
    <h3 class="section-label">Recent roster moves</h3>
    {moves_content}
  </section>

  <section class="panel">
    <h3 class="section-label">Upcoming Schedule</h3>
    {schedule_content}
  </section>

  <section class="panel">
    <h3 class="section-label">40-Man Roster</h3>
    {roster_content}
  </section>
</main>


<style>""" + PLAYER_TEAM_SHARED_STYLE + """</style>
</body>
</html>
"""


def build_player_page(data):
    team = data["team"] or "Free Agent"
    team_slug = slugify(team)
    brand_color = TEAM_COLORS.get(team, "#ff5266")

    if data["hr_pick_rank"] is not None:
        pick_badge = (
            f"<a class='pick-badge' href='../{HR_PICKS_FILE}'>"
            f"#{data['hr_pick_rank']} HR pick &middot; score {data['hr_pick_score']:.2f}</a>"
        )
    else:
        pick_badge = ""

    splits = data["year_splits"]
    if splits:
        # The API returns one split per stint against this pitcher, not one per
        # season (e.g. several entries all tagged "2026" for repeat matchups
        # within the same year) - aggregate into one row per season.
        by_season = {}
        for s in splits:
            totals = by_season.setdefault(s["season"], {"ab": 0, "h": 0, "hr": 0, "so": 0})
            stat = s["stat"]
            totals["ab"] += stat.get("atBats", 0)
            totals["h"] += stat.get("hits", 0)
            totals["hr"] += stat.get("homeRuns", 0)
            totals["so"] += stat.get("strikeOuts", 0)

        career_nums = data["career_nums"]
        if career_nums["ab"] is not None:
            # avg can still be None here even with recorded at-bats (e.g. 0
            # official at-bats from an all-walks history against this pitcher).
            avg_text = f"{career_nums['avg']:.3f}" if career_nums["avg"] is not None else "-"
            summary = (
                f"<p class='career-line'>AB {career_nums['ab']} &middot; H {career_nums['h']} &middot; "
                f"HR {career_nums['hr']} &middot; SO {career_nums['so']} &middot; AVG {avg_text}</p>"
            )
        else:
            summary = ""
        year_rows = "".join(
            row([season, t["ab"], t["h"], t["hr"], t["so"], f"{t['h'] / t['ab']:.3f}" if t["ab"] else "-"])
            for season, t in sorted(by_season.items())
        )
        career_content = (
            f"{summary}<table class='trend-table'>"
            f"<tr><th>Season</th><th>AB</th><th>H</th><th>HR</th><th>SO</th><th>AVG</th></tr>{year_rows}</table>"
        )
    else:
        career_content = "<p class='empty-note'>No recorded at-bats against this pitcher.</p>"

    recent_totals, recent_games = data.get("recent_totals"), data.get("recent_games") or []
    if recent_totals and recent_totals["atBats"]:
        summary = (
            f"<p class='career-line'>AB {recent_totals['atBats']} &middot; H {recent_totals['hits']} &middot; "
            f"HR {recent_totals['homeRuns']} &middot; RBI {recent_totals['rbi']} &middot; "
            f"SO {recent_totals['strikeOuts']} &middot; AVG {recent_totals['avg']}</p>"
        )
        game_items = "".join(
            f"<li><span class='move-date'>{esc(g['date'])}</span>{esc(g['opponent'])} &mdash; {esc(g['summary']) or 'No at-bats'}</li>"
            for g in recent_games
        )
        recent_form_content = f"{summary}<ul class='move-mini-list'>{game_items}</ul>"
    else:
        recent_form_content = "<p class='empty-note'>No games played yet this season.</p>"

    career_all = data.get("career_stat_all")
    if career_all:
        career_stats_content = f"""<section class="stat-grid">
      <div class="stat-card"><span class="stat-label">Career AVG</span><span class="stat-value">{career_all.get('avg', '-')}</span></div>
      <div class="stat-card"><span class="stat-label">Career HR</span><span class="stat-value">{career_all.get('homeRuns', '-')}</span></div>
      <div class="stat-card"><span class="stat-label">Career OPS</span><span class="stat-value">{career_all.get('ops', '-')}</span></div>
      <div class="stat-card"><span class="stat-label">Career H</span><span class="stat-value">{career_all.get('hits', '-')}</span></div>
    </section>
    <p class="career-line" style="margin-top: 0.75rem;">
      {career_all.get('gamesPlayed', '-')} games &middot; {career_all.get('atBats', '-')} AB &middot;
      {career_all.get('rbi', '-')} RBI &middot; {career_all.get('obp', '-')} OBP &middot; {career_all.get('slg', '-')} SLG
    </p>"""
    else:
        career_stats_content = "<p class='empty-note'>Career totals not available for this player.</p>"

    meta_avg = data["season_avg"] or "-"
    meta_hr = data["season_hr"] if data["season_hr"] is not None else "-"
    meta_ops = data["season_ops"] or "-"
    meta_description = esc(
        f"{data['player']} ({team}) {CURRENT_SEASON} stats: {meta_avg} AVG, {meta_hr} HR, {meta_ops} OPS. "
        f"Matchups, streaks, and season performance."
    )
    player_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Person",
        "name": data["player"],
        "jobTitle": "Professional Baseball Player",
        "memberOf": {"@type": "SportsTeam", "name": team, "sport": "Baseball"},
        "url": f"{SITE_BASE_URL}/{PLAYERS_DIR}/{slugify(data['player'])}.html",
    }).replace("</", "<\\/")

    return PLAYER_PAGE_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        player_name=esc(data["player"]),
        meta_description=meta_description,
        player_jsonld=player_jsonld,
        bats=data["bats"] or "-",
        team=esc(team),
        team_link=f"{TEAMS_DIR}/{team_slug}.html",
        brand_color=brand_color,
        season=CURRENT_SEASON,
        avg=data["season_avg"] or "-",
        hr=data["season_hr"] if data["season_hr"] is not None else "-",
        ops=data["season_ops"] or "-",
        pa=data["season_pa"] if data["season_pa"] is not None else "-",
        opponent_pitcher=esc(data["opponent_pitcher"]),
        pitcher_throws=data["pitcher_throws"] or "-",
        venue=esc(data["venue"]),
        game_time=esc(data["game_time"]),
        weather_effect=data["weather_effect"],
        weather_label=BADGE_LABELS[data["weather_effect"]],
        pick_badge=pick_badge,
        career_content=career_content,
        recent_n=len(recent_games) if recent_games else RECENT_FORM_GAMES,
        recent_form_content=recent_form_content,
        career_stats_content=career_stats_content,
    )


def build_team_page(data, player_pages):
    team = data["team"]
    opponent = data["opponent"] or "TBD"
    location_prefix = "vs" if data.get("is_home") else "@"
    matchup_line = f"{esc(team)} {location_prefix} {esc(opponent)}"

    if data["hr_picks"]:
        items = "".join(
            f"<li><a class='player-link' href='../{PLAYERS_DIR}/{slugify(p['player'])}.html'>{esc(p['player'])}</a>"
            f"<span class='pick-rank'>#{p['rank']} &middot; {p['score']:.2f}</span></li>"
            for p in data["hr_picks"]
        )
        hr_picks_content = f"<ul class='pick-list'>{items}</ul><a class='see-all-link' href='../{HR_PICKS_FILE}'>See full HR picks list &rarr;</a>"
    else:
        hr_picks_content = f"<p class='empty-note'>No {esc(team)} players in today's top HR picks.</p>"

    if data["lineup_rows"]:
        lineup_rows_html = "".join(
            row([
                f"<a class='player-link' href='../{PLAYERS_DIR}/{slugify(p['player'])}.html'>{esc(p['player'])}</a>",
                p["bats"] or "-", p["season_avg"] or "-",
                p["season_hr"] if p["season_hr"] is not None else "-", p["season_ops"] or "-",
            ])
            for p in data["lineup_rows"]
        )
        lineup_content = f"<table class='lineup-table'><tr><th>Batter</th><th>Bats</th><th>{CURRENT_SEASON} AVG</th><th>HR</th><th>OPS</th></tr>{lineup_rows_html}</table>"
    else:
        lineup_content = "<p class='empty-note'>Lineup not yet posted.</p>"

    if data["recent_moves"]:
        items = "".join(
            f"<li><span class='move-date'>{m['date']}</span>{m['description']}</li>"
            for m in data["recent_moves"]
        )
        moves_content = f"<ul class='move-mini-list'>{items}</ul><a class='see-all-link' href='../{ROSTER_MOVES_FILE}'>See all roster moves &rarr;</a>"
    else:
        moves_content = f"<p class='empty-note'>No recent moves for {esc(team)}.</p>"

    if data.get("schedule"):
        items = "".join(
            f"<li><span class='move-date'>{esc(g['date'])}</span>"
            f"{'vs' if g['is_home'] else '@'} "
            f"<a class='player-link' href='{slugify(g['opponent'])}.html'>{esc(g['opponent'])}</a></li>"
            for g in data["schedule"]
        )
        schedule_content = f"<ul class='move-mini-list'>{items}</ul>"
    else:
        schedule_content = "<p class='empty-note'>Upcoming schedule not available.</p>"

    if data.get("roster"):
        roster_rows = "".join(
            row([
                p["jersey"] or "-",
                f"<a class='player-link' href='../{PLAYERS_DIR}/{slugify(p['name'])}.html'>{esc(p['name'])}</a>"
                if p["name"] in player_pages else esc(p["name"]),
                p["position"] or "-",
                (esc(p["status"]), "" if p["status"] == "Active" else "roster-status-out"),
            ])
            for p in data["roster"]
        )
        roster_content = f"<table class='lineup-table'><tr><th>#</th><th>Player</th><th>Pos</th><th>Status</th></tr>{roster_rows}</table>"
    else:
        roster_content = "<p class='empty-note'>Roster not available.</p>"

    meta_description = esc(
        f"{team} roster, schedule, and {CURRENT_SEASON} season stats - today's matchup {location_prefix} {opponent}."
    )
    team_jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "SportsTeam",
        "name": team,
        "sport": "Baseball",
        "url": f"{SITE_BASE_URL}/{TEAMS_DIR}/{slugify(team)}.html",
    }).replace("</", "<\\/")

    return TEAM_PAGE_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        team_name=esc(team),
        meta_description=meta_description,
        team_jsonld=team_jsonld,
        brand_color=TEAM_COLORS.get(team, "#ff5266"),
        location_prefix=location_prefix,
        opponent=esc(opponent),
        matchup_line=matchup_line,
        game_time=esc(data["game_time"]),
        venue=esc(data["venue"]),
        pitcher_line=f"vs {esc(data['opponent_pitcher'])} ({data['opponent_pitcher_throws'] or '-'})",
        weather_effect=data["weather_effect"],
        weather_label=BADGE_LABELS[data["weather_effect"]],
        opponent_pitcher=esc(data["opponent_pitcher"]),
        hr_picks_content=hr_picks_content,
        lineup_content=lineup_content,
        moves_content=moves_content,
        schedule_content=schedule_content,
        roster_content=roster_content,
    )


STREAKS_FILE = "streaks.html"
MIN_STREAK = 2
MIN_SCORELESS_APPEARANCES = 1
K_STREAK_THRESHOLDS = (4, 5, 6, 7, 8)


def gather_league_wide_streaks(player_pages, pitcher_streaks):
    """Expands the batter/pitcher pools used for the Streaks page beyond
    today's slate to every team's active roster, so a hot streak still shows
    up on a team's day off (or for a reliever who isn't today's probable
    starter, which today's-slate-only pitcher_streaks entirely misses).
    Skips anyone already covered by today's games to avoid redundant
    fetches - on a full 30-team slate this mostly just fills in relievers
    and bench bats, not a full re-fetch of the league."""
    try:
        team_id_map = get_team_id_map()
    except Exception as exc:
        print(f"  SKIPPED league-wide streaks (team lookup failed): {exc!r}")
        return player_pages, pitcher_streaks

    covered_batters = set(player_pages.keys())
    covered_pitcher_ids = {p["id"] for p in pitcher_streaks}
    all_batters = dict(player_pages)
    all_pitchers = list(pitcher_streaks)

    for team_name, team_id in team_id_map.items():
        try:
            roster = get_team_roster(team_id)
        except Exception as exc:
            print(f"  SKIPPED roster for {team_name} (league-wide streaks): {exc!r}")
            continue

        for p in roster:
            if p["status"] != "Active":
                continue

            if p["position_type"] == "Pitcher":
                if p["id"] in covered_pitcher_ids:
                    continue
                covered_pitcher_ids.add(p["id"])
                try:
                    streaks = get_pitcher_streaks(p["id"], k_thresholds=K_STREAK_THRESHOLDS)
                except Exception:
                    continue
                all_pitchers.append({"id": p["id"], "name": p["name"], "team": team_name, **streaks})
            else:
                if p["name"] in covered_batters:
                    continue
                covered_batters.add(p["name"])
                try:
                    batter_stat = get_season_stat(p["id"], "hitting")
                    _, _, batter_streaks = get_recent_form(p["id"], last_n=RECENT_FORM_GAMES)
                except Exception:
                    continue
                all_batters[p["name"]] = {
                    "player": p["name"], "team": team_name,
                    "season_avg": parse_avg(batter_stat["avg"]) if batter_stat else None,
                    "season_hr": batter_stat["homeRuns"] if batter_stat else None,
                    "season_ops": parse_avg(batter_stat["ops"]) if batter_stat else None,
                    "streaks": batter_streaks,
                }

    return all_batters, all_pitchers

STREAKS_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Streaks - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Active MLB hitting and pitching streaks across the league, updated daily.">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>Streaks</h1>
  </div>
  <span class="date">{date_label}</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<p class="disclaimer">
  Active streaks for players across the league, whether or not their team plays today, computed
  from this season's game-by-game logs.
  Batting streaks skip games with zero plate appearances rather than breaking on them (hit/home
  run streaks specifically also skip a plate appearance with zero at-bats, e.g. a walk) - this
  matches how MLB officially tracks hitting streaks. Win streaks count consecutive decisions that
  were wins, skipping no-decisions. Strikeout streaks count consecutive starts at or above the
  listed strikeout count.
</p>
{view_toggle}
<main class="park-main">
  <div id="view-players">
{sections_html}
  </div>
  <div id="view-teams" style="display:none">
{team_sections_html}
  </div>
</main>

<script>{show_more_js}</script>

<style>
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.site-header {{
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.site-header h1 {{ font-size: 1.15rem; margin: 0; white-space: nowrap; }}
.site-header .date {{ color: #999; font-size: 0.9rem; white-space: nowrap; }}
.header-link {{
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.header-link:hover {{ border-color: #ff5266; color: #ff5266; }}

.disclaimer {{
  max-width: 900px; margin: 1.25rem auto 0; padding: 0.75rem 1rem;
  color: #aaa; font-size: 0.85rem; line-height: 1.5;
  background: rgba(255,82,102,0.06); border-left: 3px solid #ff5266; border-radius: 0 8px 8px 0;
}}

.view-toggle {{
  display: flex; justify-content: center; gap: 0.5rem;
  max-width: 900px; margin: 1.1rem auto 0; padding: 0 1.25rem;
}}
.view-toggle-btn {{
  background: #1a1a1a; border: 1px solid #333; color: #999;
  padding: 0.5rem 1.3rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  cursor: pointer; transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}}
.view-toggle-btn.active {{ background: #ff5266; border-color: #ff5266; color: #fff; }}
.view-toggle-btn:hover:not(.active) {{ border-color: #666; color: #f2f2f2; }}

.park-main {{ max-width: 900px; margin: 0 auto; padding: 1rem 1.25rem 1.5rem; }}
.streak-section {{ margin-bottom: 1.75rem; }}
.streak-heading {{ font-size: 1rem; margin: 0 0 0.6rem; }}
.park-list {{ display: flex; flex-direction: column; gap: 0.6rem; }}
.streak-more {{ order: 1; }}
.streak-more summary {{
  list-style: none; cursor: pointer; user-select: none;
  color: #999; font-size: 0.82rem; font-weight: 600;
  padding: 0.6rem 0.25rem; text-align: center;
  border: 1px dashed #333; border-radius: 10px;
  transition: color 0.15s ease, border-color 0.15s ease;
}}
.streak-more summary::-webkit-details-marker {{ display: none; }}
.streak-more summary:hover {{ color: #f2f2f2; border-color: #555; }}
.streak-more[open] summary {{ margin-bottom: 0.6rem; }}
.streak-more-list {{ margin: 0; }}
.park-card {{
  display: flex; align-items: center; gap: 0.9rem;
  background: #14141a; border: 1px solid #262626; border-left: 3px solid var(--team-accent, #333);
  border-radius: 10px; padding: 0.75rem 1rem;
}}
.park-rank {{
  flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%;
  background: #1a1a1a; color: #888; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}}
.park-body {{ flex: 1; min-width: 0; }}
.park-head {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
.park-name {{ font-size: 0.95rem; font-weight: 600; color: #f5f5f5; }}
.park-team {{ font-size: 0.78rem; color: #999; }}
.park-dims {{ margin: 3px 0 0; font-size: 0.78rem; color: #999; line-height: 1.5; }}
.park-factor {{ flex-shrink: 0; text-align: right; }}
.factor-value {{ display: block; font-size: 1.3rem; font-weight: 700; font-family: ui-monospace, monospace; }}
.factor-label {{
  display: block; font-size: 0.66rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.03em; margin-top: 2px;
}}
.factor-hitters .factor-value, .factor-hitters .factor-label {{ color: #4ade80; }}
.factor-pitchers .factor-value, .factor-pitchers .factor-label {{ color: #6bb3ff; }}
.factor-neutral .factor-value, .factor-neutral .factor-label {{ color: #999999; }}
.empty-note {{ margin: 0; font-size: 0.85rem; color: #777; font-style: italic; padding: 0.5rem 0.25rem; }}
.player-link {{ color: inherit; text-decoration: none; border-bottom: 1px solid transparent; }}
.player-link:hover {{ color: #ff5266; border-bottom-color: #ff5266; }}

@media (max-width: 560px) {{
  .park-card {{ flex-wrap: wrap; }}
  .park-factor {{ margin-left: auto; text-align: right; }}
}}
</style>
</body>
</html>
"""


def build_streak_card(rank, team, name_html, sub_text, value_text, value_label, factor_class, show_team_label=True):
    color = TEAM_COLORS.get(team, "#333")
    team_label = f"<span class=\"park-team\">{esc(team)}</span>" if show_team_label else ""
    return f"""    <div class="park-card" style="--team-accent: {color}">
      <div class="park-rank">{rank}</div>
      <div class="park-body">
        <div class="park-head">
          <span class="park-name">{name_html}</span>
          {team_label}
        </div>
        <p class="park-dims">{sub_text}</p>
      </div>
      <div class="park-factor {factor_class}">
        <span class="factor-value">{value_text}</span>
        <span class="factor-label">{value_label}</span>
      </div>
    </div>"""


STREAKS_TOP_N = 3

# Plain string (not passed through .format()) so JS braces stay literal -
# toggles each "Show N more ▾" dropdown's summary text to "Show less ▴" and
# back, on both the Streaks and League Leaders pages. Driven off the
# summary's own click (deferred one tick so the browser's default
# open/close action has already run) rather than the details "toggle"
# event, which isn't reliably dispatched in every rendering context.
SHOW_MORE_TOGGLE_JS = """
document.querySelectorAll('details.streak-more').forEach((details) => {
  const summary = details.querySelector('summary');
  if (!summary) return;
  const collapsedText = summary.textContent;
  summary.addEventListener('click', () => {
    setTimeout(() => {
      summary.textContent = details.open ? 'Show less ▴' : collapsedText;
    }, 0);
  });
});
"""

# Plain string (not passed through .format()) - drives the Players/Teams
# toggle shared by the Streaks and League Leaders pages. Both views are
# rendered into the page up front; the toggle just swaps which is visible,
# so there's no extra page load or duplicated fetch.
VIEW_TOGGLE_JS = """
document.querySelectorAll('.view-toggle-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.view-toggle-btn').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    const view = btn.dataset.view;
    document.getElementById('view-players').style.display = view === 'players' ? '' : 'none';
    document.getElementById('view-teams').style.display = view === 'teams' ? '' : 'none';
  });
});
"""

VIEW_TOGGLE_HTML = """<div class="view-toggle">
  <button type="button" class="view-toggle-btn active" data-view="players">Players</button>
  <button type="button" class="view-toggle-btn" data-view="teams">Teams</button>
</div>"""


def render_streak_section(cards, empty_message, top_n=STREAKS_TOP_N):
    """Renders the top `top_n` cards directly, with any remainder tucked
    behind a native <details> dropdown so the page opens short."""
    if not cards:
        return f"    <p class='empty-note'>{empty_message}</p>"

    top = "\n".join(cards[:top_n])
    rest = cards[top_n:]
    if not rest:
        return top

    rest_html = "\n".join(rest)
    return (
        f"{top}\n"
        f"    <details class='streak-more'>\n"
        f"      <summary>Show {len(rest)} more &#9662;</summary>\n"
        f"      <div class='park-list streak-more-list'>\n{rest_html}\n      </div>\n"
        f"    </details>"
    )


def _plural(n, word):
    return word if n == 1 else f"{word}s"


def batter_streak_cards(player_pages, key, sub_label, value_label, factor_class="factor-hitters"):
    entries = sorted(
        (p for p in player_pages.values() if p.get("streaks", {}).get(key, 0) >= MIN_STREAK),
        key=lambda p: p["streaks"][key], reverse=True,
    )
    return [
        build_streak_card(
            i, p["team"] or "-",
            f"<a class='player-link' href='{PLAYERS_DIR}/{slugify(p['player'])}.html'>{esc(p['player'])}</a>",
            sub_label.format(p=p), str(p["streaks"][key]), value_label, factor_class,
        )
        for i, p in enumerate(entries, start=1)
    ]


def pitcher_k_streak_cards(pitcher_streaks, threshold):
    entries = sorted(
        (p for p in pitcher_streaks if p["k_streaks"].get(threshold, 0) >= 1),
        key=lambda p: p["k_streaks"][threshold], reverse=True,
    )
    return [
        build_streak_card(
            i, p["team"] or "-", esc(p["name"]),
            f"{p['k_streaks'][threshold]} straight {_plural(p['k_streaks'][threshold], 'start')} with {threshold}+ K",
            str(p["k_streaks"][threshold]), "starts", "factor-neutral",
        )
        for i, p in enumerate(entries, start=1)
    ]


def build_team_streak_sections():
    """Team-level counterpart to the player streak sections: current
    winning/losing streaks (from the same streak codes shown on the
    standings page), plus current streaks of consecutive games with a
    first-inning run scored/allowed, a home run, 3+ runs, or 5+ runs
    (walked from each team's game log/schedule, same backward-from-most-
    recent pattern the player streak functions use)."""
    streaks = get_team_streaks()

    team_ids = get_team_id_map()
    scoring, first_inning = [], []
    for name, team_id in team_ids.items():
        try:
            scoring.append({"team": name, **get_team_scoring_streaks(team_id)})
        except Exception as exc:
            print(f"  SKIPPED team scoring streaks for {name}: {exc!r}")
        try:
            first_inning.append({"team": name, **get_team_first_inning_streaks(team_id)})
        except Exception as exc:
            print(f"  SKIPPED team 1st-inning streaks for {name}: {exc!r}")

    def team_cards(entries, key, unit, factor_class):
        ranked = sorted((e for e in entries if e[key] >= MIN_STREAK), key=lambda e: e[key], reverse=True)
        return [
            build_streak_card(
                i, e["team"],
                f"<a class='player-link' href='{TEAMS_DIR}/{slugify(e['team'])}.html'>{esc(e['team'])}</a>",
                f"{e[key]} straight {_plural(e[key], 'game')}", str(e[key]), unit, factor_class,
                show_team_label=False,
            )
            for i, e in enumerate(ranked, start=1)
        ]

    sections = [
        ("Winning Streaks", streaks["winning"], "length", "wins", "factor-pitchers",
         "No teams riding a win streak of 2+ games right now."),
        ("Losing Streaks", streaks["losing"], "length", "losses", "factor-neutral",
         "No teams riding a losing streak of 2+ games right now."),
        ("1st-Inning Scoring Streaks", first_inning, "scored_1st", "games", "factor-hitters",
         "No teams with a first-inning scoring streak of 2+ games right now."),
        ("1st-Inning Runs Allowed Streaks", first_inning, "allowed_1st", "games", "factor-neutral",
         "No teams allowing a first-inning run in 2+ straight games right now."),
        ("Home Run Game Streaks", scoring, "hr_game", "games", "factor-hitters",
         "No teams with a home run in 2+ straight games right now."),
        ("3+ Run Game Streaks", scoring, "runs_3plus", "games", "factor-hitters",
         "No teams scoring 3+ runs in 2+ straight games right now."),
        ("5+ Run Game Streaks", scoring, "runs_5plus", "games", "factor-hitters",
         "No teams scoring 5+ runs in 2+ straight games right now."),
    ]

    return "\n".join(f"""  <section class="streak-section">
    <h2 class="streak-heading">{heading}</h2>
    <div class="park-list">
{render_streak_section(team_cards(entries, key, unit, factor_class), empty_message)}
    </div>
  </section>""" for heading, entries, key, unit, factor_class, empty_message in sections)


def build_streaks_page(player_pages, pitcher_streaks, date_label):
    scoreless = sorted(
        (p for p in pitcher_streaks if p["scoreless_appearances"] >= MIN_SCORELESS_APPEARANCES),
        key=lambda p: parse_innings_pitched(p["scoreless_ip"]), reverse=True,
    )
    scoreless_cards = [
        build_streak_card(
            i, p["team"] or "-", esc(p["name"]),
            f"{p['scoreless_appearances']} straight scoreless {_plural(p['scoreless_appearances'], 'outing')}",
            p["scoreless_ip"], "IP", "factor-pitchers",
        )
        for i, p in enumerate(scoreless, start=1)
    ]

    win_streaks = sorted(
        (p for p in pitcher_streaks if p["win_streak"] >= MIN_STREAK),
        key=lambda p: p["win_streak"], reverse=True,
    )
    win_cards = [
        build_streak_card(
            i, p["team"] or "-", esc(p["name"]),
            f"{p['win_streak']} straight winning {_plural(p['win_streak'], 'decision')}",
            str(p["win_streak"]), "wins", "factor-pitchers",
        )
        for i, p in enumerate(win_streaks, start=1)
    ]

    sections = [
        ("Hit Streaks", batter_streak_cards(player_pages, "hit", "Season AVG {p[season_avg]}", "games"),
         "No active hit streaks of 2+ games right now."),
        ("On-Base Streaks", batter_streak_cards(player_pages, "on_base", "Season OPS {p[season_ops]}", "games"),
         "No active on-base streaks of 2+ games right now."),
        ("Home Run Streaks", batter_streak_cards(player_pages, "hr", "Season HR {p[season_hr]}", "games"),
         "No active home run streaks of 2+ games right now."),
        ("Walk Streaks", batter_streak_cards(player_pages, "walk", "Season AVG {p[season_avg]}", "games", "factor-neutral"),
         "No active walk streaks of 2+ games right now."),
        ("RBI Streaks", batter_streak_cards(player_pages, "rbi", "Season AVG {p[season_avg]}", "games"),
         "No active RBI streaks of 2+ games right now."),
        ("Scoreless Innings Streaks", scoreless_cards,
         "No pitchers riding a scoreless streak right now."),
        ("Win Streaks", win_cards,
         "No pitchers riding a win streak right now."),
        ("Strikeout Streaks (4+ K)", pitcher_k_streak_cards(pitcher_streaks, 4),
         "No pitchers riding a 4+ strikeout streak right now."),
        ("Strikeout Streaks (5+ K)", pitcher_k_streak_cards(pitcher_streaks, 5),
         "No pitchers riding a 5+ strikeout streak right now."),
        ("Strikeout Streaks (6+ K)", pitcher_k_streak_cards(pitcher_streaks, 6),
         "No pitchers riding a 6+ strikeout streak right now."),
        ("Strikeout Streaks (7+ K)", pitcher_k_streak_cards(pitcher_streaks, 7),
         "No pitchers riding a 7+ strikeout streak right now."),
        ("Strikeout Streaks (8+ K)", pitcher_k_streak_cards(pitcher_streaks, 8),
         "No pitchers riding an 8+ strikeout streak right now."),
    ]

    sections_html = "\n".join(
        f"""  <section class="streak-section">
    <h2 class="streak-heading">{heading}</h2>
    <div class="park-list">
{render_streak_section(cards, empty_message)}
    </div>
  </section>"""
        for heading, cards, empty_message in sections
    )

    try:
        team_sections_html = build_team_streak_sections()
    except Exception as exc:
        print(f"  SKIPPED team streaks: {exc!r}")
        team_sections_html = "    <p class='empty-note'>Team streaks are unavailable right now.</p>"

    return STREAKS_PAGE_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        date_label=date_label,
        sections_html=sections_html,
        team_sections_html=team_sections_html,
        view_toggle=VIEW_TOGGLE_HTML,
        show_more_js=SHOW_MORE_TOGGLE_JS + VIEW_TOGGLE_JS,
    )


LEADERBOARD_PAGE_FILE = "leaders.html"

# (request category, response lookup key, heading, unit label, stat group, color class)
# Lookup key usually matches the request category, except where the API
# echoes back a different canonical name (whip -> walksAndHitsPerInningPitched).
LEADERBOARD_CATEGORIES = [
    ("homeRuns", "homeRuns", "Home Runs", "HR", "hitting", "factor-hitters"),
    ("battingAverage", "battingAverage", "Batting Average", "AVG", "hitting", "factor-hitters"),
    ("onBasePlusSlugging", "onBasePlusSlugging", "OPS", "OPS", "hitting", "factor-hitters"),
    ("runsBattedIn", "runsBattedIn", "RBI", "RBI", "hitting", "factor-hitters"),
    ("hits", "hits", "Hits", "H", "hitting", "factor-hitters"),
    ("stolenBases", "stolenBases", "Stolen Bases", "SB", "hitting", "factor-hitters"),
    ("earnedRunAverage", "earnedRunAverage", "ERA", "ERA", "pitching", "factor-pitchers"),
    ("strikeouts", "strikeouts", "Strikeouts", "K", "pitching", "factor-pitchers"),
    ("wins", "wins", "Wins", "W", "pitching", "factor-pitchers"),
    ("saves", "saves", "Saves", "SV", "pitching", "factor-pitchers"),
    ("whip", "walksAndHitsPerInningPitched", "WHIP", "WHIP", "pitching", "factor-pitchers"),
]

LEADERBOARD_TOP_N = 5

# (raw stat key, heading, unit label, stat group, color class, higher-is-better)
TEAM_LEADERBOARD_CATEGORIES = [
    ("avg", "Team Batting Average", "AVG", "hitting", "factor-hitters", True),
    ("homeRuns", "Team Home Runs", "HR", "hitting", "factor-hitters", True),
    ("runs", "Team Runs Scored", "R", "hitting", "factor-hitters", True),
    ("obp", "Team On-Base %", "OBP", "hitting", "factor-hitters", True),
    ("stolenBases", "Team Stolen Bases", "SB", "hitting", "factor-hitters", True),
    ("era", "Team ERA", "ERA", "pitching", "factor-pitchers", False),
    ("wins", "Team Wins", "W", "pitching", "factor-pitchers", True),
    ("strikeOuts", "Team Strikeouts", "K", "pitching", "factor-pitchers", True),
    ("saves", "Team Saves", "SV", "pitching", "factor-pitchers", True),
    ("whip", "Team WHIP", "WHIP", "pitching", "factor-pitchers", False),
    ("blownSaves", "Team Blown Saves", "BS", "pitching", "factor-pitchers", False),
    ("runs", "Team Runs Allowed", "RA", "pitching", "factor-pitchers", False),
    ("runs", "Team 1st-Inning Runs Given Up", "R", "pitching_i01", "factor-pitchers", False),
    ("runs", "Team 1st-Inning Runs Scored", "R", "hitting_i01", "factor-hitters", True),
]


def build_team_leader_sections():
    """Team-level counterpart to the player leaderboard sections - all 30
    teams ranked per stat, since the MLB team-stats endpoint returns every
    team's full line rather than a pre-sorted leader list."""
    stats_by_group = {
        "hitting": get_team_season_stats("hitting"),
        "pitching": get_team_season_stats("pitching"),
        "hitting_i01": get_team_season_stats("hitting", sit_code="i01"),
        "pitching_i01": get_team_season_stats("pitching", sit_code="i01"),
    }

    sections = []
    for stat_key, heading, unit, stat_group, factor_class, higher_better in TEAM_LEADERBOARD_CATEGORIES:
        def sort_value(t, stat_key=stat_key):
            try:
                return float(t["stat"].get(stat_key))
            except (TypeError, ValueError):
                return float("-inf")

        ranked = sorted(stats_by_group[stat_group], key=sort_value, reverse=higher_better)
        cards = [
            build_streak_card(
                i, t["team"],
                f"<a class='player-link' href='{TEAMS_DIR}/{slugify(t['team'])}.html'>{esc(t['team'])}</a>",
                "", t["stat"].get(stat_key, "-"), unit, factor_class,
                show_team_label=False,
            )
            for i, t in enumerate(ranked, start=1)
        ]
        cards_html = render_streak_section(cards, "No team data available.", top_n=LEADERBOARD_TOP_N)
        sections.append(f"""  <section class="streak-section">
    <h2 class="streak-heading">{heading}</h2>
    <div class="park-list">
{cards_html}
    </div>
  </section>""")

    return "\n".join(sections)

LEADERBOARD_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>League Leaders - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="MLB league leaders in home runs, batting average, ERA, strikeouts, and more - for players and teams.">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>League Leaders</h1>
  </div>
  <span class="date">{date_label}</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<p class="disclaimer">
  Season leaders among qualified players, independent of who's playing today. Names link to
  a player page when that player also appears in today's slate.
</p>
{view_toggle}
<main class="park-main">
  <div id="view-players">
{sections_html}
  </div>
  <div id="view-teams" style="display:none">
{team_sections_html}
  </div>
</main>

<script>{show_more_js}</script>

<style>
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.site-header {{
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.site-header h1 {{ font-size: 1.15rem; margin: 0; white-space: nowrap; }}
.site-header .date {{ color: #999; font-size: 0.9rem; white-space: nowrap; }}
.header-link {{
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.header-link:hover {{ border-color: #ff5266; color: #ff5266; }}

.disclaimer {{
  max-width: 900px; margin: 1.25rem auto 0; padding: 0.75rem 1rem;
  color: #aaa; font-size: 0.85rem; line-height: 1.5;
  background: rgba(255,82,102,0.06); border-left: 3px solid #ff5266; border-radius: 0 8px 8px 0;
}}

.view-toggle {{
  display: flex; justify-content: center; gap: 0.5rem;
  max-width: 900px; margin: 1.1rem auto 0; padding: 0 1.25rem;
}}
.view-toggle-btn {{
  background: #1a1a1a; border: 1px solid #333; color: #999;
  padding: 0.5rem 1.3rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  cursor: pointer; transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}}
.view-toggle-btn.active {{ background: #ff5266; border-color: #ff5266; color: #fff; }}
.view-toggle-btn:hover:not(.active) {{ border-color: #666; color: #f2f2f2; }}

.park-main {{ max-width: 900px; margin: 0 auto; padding: 1rem 1.25rem 1.5rem; }}
.streak-section {{ margin-bottom: 1.75rem; }}
.streak-heading {{ font-size: 1rem; margin: 0 0 0.6rem; }}
.park-list {{ display: flex; flex-direction: column; gap: 0.6rem; }}
.streak-more {{ order: 1; }}
.streak-more summary {{
  list-style: none; cursor: pointer; user-select: none;
  color: #999; font-size: 0.82rem; font-weight: 600;
  padding: 0.6rem 0.25rem; text-align: center;
  border: 1px dashed #333; border-radius: 10px;
  transition: color 0.15s ease, border-color 0.15s ease;
}}
.streak-more summary::-webkit-details-marker {{ display: none; }}
.streak-more summary:hover {{ color: #f2f2f2; border-color: #555; }}
.streak-more[open] summary {{ margin-bottom: 0.6rem; }}
.streak-more-list {{ margin: 0; }}
.park-card {{
  display: flex; align-items: center; gap: 0.9rem;
  background: #14141a; border: 1px solid #262626; border-left: 3px solid var(--team-accent, #333);
  border-radius: 10px; padding: 0.75rem 1rem;
}}
.park-rank {{
  flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%;
  background: #1a1a1a; color: #888; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}}
.park-body {{ flex: 1; min-width: 0; }}
.park-head {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
.park-name {{ font-size: 0.95rem; font-weight: 600; color: #f5f5f5; }}
.park-team {{ font-size: 0.78rem; color: #999; }}
.park-dims {{ margin: 3px 0 0; font-size: 0.78rem; color: #999; line-height: 1.5; }}
.park-factor {{ flex-shrink: 0; text-align: right; }}
.factor-value {{ display: block; font-size: 1.3rem; font-weight: 700; font-family: ui-monospace, monospace; }}
.factor-label {{
  display: block; font-size: 0.66rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.03em; margin-top: 2px;
}}
.factor-hitters .factor-value, .factor-hitters .factor-label {{ color: #4ade80; }}
.factor-pitchers .factor-value, .factor-pitchers .factor-label {{ color: #6bb3ff; }}
.empty-note {{ margin: 0; font-size: 0.85rem; color: #777; font-style: italic; padding: 0.5rem 0.25rem; }}
.player-link {{ color: inherit; text-decoration: none; border-bottom: 1px solid transparent; }}
.player-link:hover {{ color: #ff5266; border-bottom-color: #ff5266; }}

@media (max-width: 560px) {{
  .park-card {{ flex-wrap: wrap; }}
  .park-factor {{ margin-left: auto; text-align: right; }}
}}
</style>
</body>
</html>
"""


def build_leaderboards_page(player_pages, date_label):
    hitting = get_stat_leaders(
        [c[0] for c in LEADERBOARD_CATEGORIES if c[4] == "hitting"], "hitting"
    )
    pitching = get_stat_leaders(
        [c[0] for c in LEADERBOARD_CATEGORIES if c[4] == "pitching"], "pitching"
    )
    leaders_by_category = {**hitting, **pitching}

    sections = []
    for _request_key, lookup_key, heading, unit, _stat_group, factor_class in LEADERBOARD_CATEGORIES:
        entries = leaders_by_category.get(lookup_key, [])
        cards = []
        for e in entries:
            if e["name"] in player_pages:
                name_html = f"<a class='player-link' href='{PLAYERS_DIR}/{slugify(e['name'])}.html'>{esc(e['name'])}</a>"
            else:
                name_html = esc(e["name"])
            cards.append(build_streak_card(e["rank"], e["team"], name_html, "", e["value"], unit, factor_class))
        cards_html = render_streak_section(cards, "No qualified leaders available.", top_n=LEADERBOARD_TOP_N)
        sections.append(f"""  <section class="streak-section">
    <h2 class="streak-heading">{heading}</h2>
    <div class="park-list">
{cards_html}
    </div>
  </section>""")

    try:
        team_sections_html = build_team_leader_sections()
    except Exception as exc:
        print(f"  SKIPPED team leaders: {exc!r}")
        team_sections_html = "    <p class='empty-note'>Team leaders are unavailable right now.</p>"

    return LEADERBOARD_PAGE_TEMPLATE.format(
        brand_icon=brand_icon_svg(), home_icon=brand_icon_svg(16), date_label=date_label,
        sections_html="\n".join(sections), team_sections_html=team_sections_html,
        view_toggle=VIEW_TOGGLE_HTML, show_more_js=SHOW_MORE_TOGGLE_JS + VIEW_TOGGLE_JS,
    )


STANDINGS_PAGE_FILE = "standings.html"

STANDINGS_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Standings - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="{meta_description}">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>Standings</h1>
  </div>
  <span class="date">{date_label}</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<p class="disclaimer">
  The real pennant race, next to the fantasy one over in the 162-0 Challenge. Magic number is
  each division leader's standard clinch number against the second-place team. Wild card tables
  are ranked 1-12 per league; the top 3 in each currently hold a playoff spot.
</p>
<main class="standings-main">
  <h2 class="standings-league-heading">American League</h2>
  <div class="standings-columns">
{al_divisions}
  </div>

  <h2 class="standings-league-heading">National League</h2>
  <div class="standings-columns">
{nl_divisions}
  </div>

  <h2 class="standings-league-heading">Wild Card Race</h2>
  <div class="standings-columns">
{wild_card}
  </div>
</main>

<style>
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.site-header {{
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.site-header h1 {{ font-size: 1.15rem; margin: 0; white-space: nowrap; }}
.site-header .date {{ color: #999; font-size: 0.9rem; white-space: nowrap; }}
.header-link {{
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}}
.header-link:hover {{ border-color: #ff5266; color: #ff5266; }}

.disclaimer {{
  max-width: 1000px; margin: 1.25rem auto 0; padding: 0.75rem 1rem;
  color: #aaa; font-size: 0.85rem; line-height: 1.5;
  background: rgba(255,82,102,0.06); border-left: 3px solid #ff5266; border-radius: 0 8px 8px 0;
}}

.standings-main {{ max-width: 1000px; margin: 0 auto; padding: 1rem 1.25rem 2rem; }}
.standings-league-heading {{
  font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: #888; margin: 2rem 0 1rem; border-bottom: 1px solid #232323; padding-bottom: 0.6rem;
}}
.standings-league-heading:first-child {{ margin-top: 0; }}
.standings-columns {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem; }}
.standings-block {{ margin-bottom: 0.5rem; }}
.standings-block-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 0.4rem; }}
.standings-block-head h3 {{ margin: 0; font-size: 0.92rem; }}
.standings-magic {{
  font-size: 0.7rem; font-weight: 700; color: #ffd166; background: rgba(255,209,102,0.12);
  padding: 3px 9px; border-radius: 999px; white-space: nowrap;
}}
.standings-table {{ border-collapse: collapse; width: 100%; font-size: 0.82rem; }}
.standings-table th, .standings-table td {{
  border-bottom: 1px solid #222; padding: 6px 8px; text-align: right; font-family: ui-monospace, monospace;
}}
.standings-table th:nth-child(-n+2), .standings-table td.standings-team {{ text-align: left; }}
.standings-table th {{ color: #888; font-family: -apple-system, sans-serif; font-size: 0.68rem; text-transform: uppercase; }}
.standings-table tr:last-child td {{ border-bottom: none; }}
.standings-team {{ font-weight: 600; color: #f2f2f2; }}
tr.standings-leader td {{ background: rgba(255,209,102,0.06); }}
tr.standings-leader .standings-team {{ color: #ffd166; }}
tr.standings-cutoff td {{ border-bottom: 2px solid #ffd166; }}
.standings-streak-up {{ color: #4ade80; }}
.standings-streak-down {{ color: #ff5266; }}
</style>
</body>
</html>
"""


def build_standings_page(standings, date_label):
    def division_table(div):
        rows = []
        for t in div["teams"]:
            leader_cls = "standings-leader" if t["rank"] == 1 else ""
            streak_cls = "standings-streak-up" if t["streak"].startswith("W") else "standings-streak-down"
            rows.append(f"""      <tr class="{leader_cls}">
        <td class="standings-team">{esc(t['name'])}</td>
        <td>{t['wins']}</td>
        <td>{t['losses']}</td>
        <td>{t['pct']}</td>
        <td>{esc(t['games_back'])}</td>
        <td class="{streak_cls}">{esc(t['streak'])}</td>
      </tr>""")
        magic_html = ""
        if div["magic_number"] is not None:
            magic_text = "Clinched!" if div["magic_number"] == 0 else f"Magic #: {div['magic_number']}"
            magic_html = f'<span class="standings-magic">{magic_text}</span>'
        return f"""    <div class="standings-block">
      <div class="standings-block-head">
        <h3>{esc(div['division'])}</h3>
        {magic_html}
      </div>
      <table class="standings-table">
        <thead><tr><th>Team</th><th>W</th><th>L</th><th>PCT</th><th>GB</th><th>Strk</th></tr></thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
    </div>"""

    def wc_table(wc):
        rows = []
        for t in wc["teams"]:
            classes = " ".join(c for c in ["standings-leader" if t["rank"] <= 3 else "", "standings-cutoff" if t["rank"] == 3 else ""] if c)
            rows.append(f"""      <tr class="{classes}">
        <td>{t['rank']}</td>
        <td class="standings-team">{esc(t['name'])}</td>
        <td>{t['wins']}</td>
        <td>{t['losses']}</td>
        <td>{esc(t['games_back'])}</td>
      </tr>""")
        return f"""    <div class="standings-block">
      <div class="standings-block-head"><h3>{esc(wc['league'])}</h3></div>
      <table class="standings-table">
        <thead><tr><th>#</th><th>Team</th><th>W</th><th>L</th><th>GB</th></tr></thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
    </div>"""

    al_divisions = [d for d in standings["divisions"] if d["league"] == "American League"]
    nl_divisions = [d for d in standings["divisions"] if d["league"] == "National League"]

    return STANDINGS_PAGE_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        date_label=date_label,
        meta_description=f"Live MLB standings, division races, and playoff picture for the {CURRENT_SEASON} season.",
        al_divisions="\n".join(division_table(d) for d in al_divisions),
        nl_divisions="\n".join(division_table(d) for d in nl_divisions),
        wild_card="\n".join(wc_table(wc) for wc in standings["wild_card"]),
    )


WORD_GAME_FILE = "bordle.html"
WORD_GAME_EPOCH = datetime.date(2026, 8, 17)  # the first Bordle

# One guess per letter in the answer - no dictionary validation, just a
# length check, since a hand-picked baseball word list can't be checked
# against a real dictionary. word must be uppercase letters only (no
# spaces/hyphens) so the board stays a simple grid.
WORD_BANK = [
    {"word": "BALK", "category": "Baseball term", "hint": "Illegal move by a pitcher with runners on base"},
    {"word": "BUNT", "category": "Baseball term", "hint": "Softly tapping the ball to lay one down"},
    {"word": "STEAL", "category": "Baseball term", "hint": "Advancing a base without a hit or walk"},
    {"word": "ERROR", "category": "Baseball term", "hint": "A defensive misplay that lets a runner reach"},
    {"word": "SLIDER", "category": "Baseball term", "hint": "A breaking pitch that darts sideways"},
    {"word": "CURVEBALL", "category": "Baseball term", "hint": "A pitch that drops sharply with topspin"},
    {"word": "FASTBALL", "category": "Baseball term", "hint": "The hardest, most direct pitch in the arsenal"},
    {"word": "CHANGEUP", "category": "Baseball term", "hint": "An off-speed pitch thrown to disrupt timing"},
    {"word": "KNUCKLEBALL", "category": "Baseball term", "hint": "A pitch thrown with almost no spin at all"},
    {"word": "SPITBALL", "category": "Baseball term", "hint": "An illegal, doctored pitch banned since 1920"},
    {"word": "BULLPEN", "category": "Baseball term", "hint": "Where relief pitchers warm up"},
    {"word": "DUGOUT", "category": "Baseball term", "hint": "Where the team sits when not on the field"},
    {"word": "INNING", "category": "Baseball term", "hint": "One full turn at bat for each side"},
    {"word": "PENNANT", "category": "Baseball term", "hint": "The flag awarded to a league champion"},
    {"word": "ROOKIE", "category": "Baseball term", "hint": "A player in their first major league season"},
    {"word": "VETERAN", "category": "Baseball term", "hint": "A player with many seasons of experience"},
    {"word": "LINEUP", "category": "Baseball term", "hint": "The batting order for a game"},
    {"word": "ROTATION", "category": "Baseball term", "hint": "The set of starting pitchers used in turn"},
    {"word": "CLOSER", "category": "Baseball term", "hint": "The reliever who finishes off a win"},
    {"word": "STARTER", "category": "Baseball term", "hint": "The pitcher who begins the game"},
    {"word": "RELIEVER", "category": "Baseball term", "hint": "A pitcher who enters mid-game"},
    {"word": "CATCHER", "category": "Baseball term", "hint": "The player who squats behind home plate"},
    {"word": "SHORTSTOP", "category": "Baseball term", "hint": "The infielder between second and third"},
    {"word": "OUTFIELD", "category": "Baseball term", "hint": "The grass beyond the infield dirt"},
    {"word": "INFIELD", "category": "Baseball term", "hint": "The diamond inside the base paths"},
    {"word": "DIAMOND", "category": "Baseball term", "hint": "The shape of the infield"},
    {"word": "HOMER", "category": "Baseball term", "hint": "Slang for a ball hit over the fence"},
    {"word": "DINGER", "category": "Baseball term", "hint": "Another slang term for a home run"},
    {"word": "TRIPLE", "category": "Baseball term", "hint": "A three-base hit"},
    {"word": "DOUBLE", "category": "Baseball term", "hint": "A two-base hit"},
    {"word": "SINGLE", "category": "Baseball term", "hint": "A one-base hit"},
    {"word": "SHUTOUT", "category": "Baseball term", "hint": "A game where one team scores zero runs"},
    {"word": "SQUEEZE", "category": "Baseball term", "hint": "A bunt play designed to score a runner from third"},
    {"word": "PICKOFF", "category": "Baseball term", "hint": "A throw meant to catch a runner off base"},
    {"word": "WALKOFF", "category": "Baseball term", "hint": "A game-ending hit by the home team"},
    {"word": "STRIKEOUT", "category": "Baseball term", "hint": "Three strikes and you're out"},
    {"word": "GROUNDOUT", "category": "Baseball term", "hint": "An out recorded on a ball hit along the ground"},
    {"word": "FLYOUT", "category": "Baseball term", "hint": "An out recorded on a ball caught in the air"},
    {"word": "POPOUT", "category": "Baseball term", "hint": "A short, high fly ball caught for an out"},
    {"word": "SACRIFICE", "category": "Baseball term", "hint": "A bunt or fly that advances a runner but outs the batter"},
    {"word": "PLATOON", "category": "Baseball term", "hint": "Alternating players based on pitcher handedness"},
    {"word": "PROSPECT", "category": "Baseball term", "hint": "A promising player still developing in the minors"},
    {"word": "FRANCHISE", "category": "Baseball term", "hint": "A team's long-term organization"},
    {"word": "DYNASTY", "category": "Baseball term", "hint": "A team that dominates for years running"},
    {"word": "SLUGGER", "category": "Baseball term", "hint": "A power hitter known for extra-base hits"},
    {"word": "UMPIRE", "category": "Baseball term", "hint": "The official who calls balls and strikes"},
    {"word": "BALLPARK", "category": "Baseball term", "hint": "General word for a baseball stadium"},
    {"word": "PLAYOFF", "category": "Baseball term", "hint": "The postseason bracket"},
    {"word": "CYCLE", "category": "Baseball term", "hint": "Hitting a single, double, triple, and homer in one game"},
    {"word": "SINKER", "category": "Baseball term", "hint": "A fastball that drops late, inducing ground balls"},
    {"word": "CUTTER", "category": "Baseball term", "hint": "A fastball with late lateral break"},
    {"word": "SPLITTER", "category": "Baseball term", "hint": "A pitch that dives sharply as it nears the plate"},
    {"word": "FORKBALL", "category": "Baseball term", "hint": "A slow, diving pitch gripped deep between the fingers"},
    {"word": "FRAMING", "category": "Baseball term", "hint": "A catcher's skill of presenting pitches as strikes"},
    {"word": "CHOPPER", "category": "Baseball term", "hint": "A ball that bounces high after hitting the ground hard"},
    {"word": "LEADOFF", "category": "Baseball term", "hint": "The first batter in the lineup, or the start of an inning"},
    {"word": "CLEANUP", "category": "Baseball term", "hint": "The fourth spot in the batting order"},
    {"word": "FUNGO", "category": "Baseball term", "hint": "A long, thin bat used for hitting fly balls in practice"},
    {"word": "BLEACHERS", "category": "Baseball term", "hint": "Cheap, uncovered outfield seating"},
    {"word": "BATTERY", "category": "Baseball term", "hint": "The pitcher and catcher, considered as a pair"},
    {"word": "MITT", "category": "Baseball term", "hint": "A fielder's glove, especially the catcher's"},
    {"word": "CLEATS", "category": "Baseball term", "hint": "Spiked shoes worn for traction"},
    {"word": "JERSEY", "category": "Baseball term", "hint": "The uniform shirt worn on the field"},
    {"word": "STADIUM", "category": "Baseball term", "hint": "The full ballpark structure"},
    {"word": "ACE", "category": "Baseball term", "hint": "A team's best starting pitcher"},
    {"word": "SOUVENIR", "category": "Baseball term", "hint": "What a fan hopes to catch in the stands"},
    {"word": "YANKEES", "category": "MLB team", "hint": "New York's American League team, 27 titles and counting"},
    {"word": "METS", "category": "MLB team", "hint": "New York's National League team"},
    {"word": "DODGERS", "category": "MLB team", "hint": "Los Angeles's National League powerhouse"},
    {"word": "GIANTS", "category": "MLB team", "hint": "San Francisco's National League club"},
    {"word": "ASTROS", "category": "MLB team", "hint": "Houston's American League team"},
    {"word": "RANGERS", "category": "MLB team", "hint": "Texas's American League team"},
    {"word": "MARINERS", "category": "MLB team", "hint": "Seattle's American League team"},
    {"word": "ANGELS", "category": "MLB team", "hint": "Anaheim's American League team"},
    {"word": "ATHLETICS", "category": "MLB team", "hint": "A storied American League club, on the move"},
    {"word": "ROYALS", "category": "MLB team", "hint": "Kansas City's American League team"},
    {"word": "TWINS", "category": "MLB team", "hint": "Minnesota's American League team"},
    {"word": "GUARDIANS", "category": "MLB team", "hint": "Cleveland's American League team"},
    {"word": "TIGERS", "category": "MLB team", "hint": "Detroit's American League team"},
    {"word": "WHITESOX", "category": "MLB team", "hint": "Chicago's American League team"},
    {"word": "CUBS", "category": "MLB team", "hint": "Chicago's National League team"},
    {"word": "CARDINALS", "category": "MLB team", "hint": "St. Louis's National League team"},
    {"word": "BREWERS", "category": "MLB team", "hint": "Milwaukee's National League team"},
    {"word": "PIRATES", "category": "MLB team", "hint": "Pittsburgh's National League team"},
    {"word": "REDS", "category": "MLB team", "hint": "Cincinnati's National League team"},
    {"word": "BRAVES", "category": "MLB team", "hint": "Atlanta's National League team"},
    {"word": "MARLINS", "category": "MLB team", "hint": "Miami's National League team"},
    {"word": "PHILLIES", "category": "MLB team", "hint": "Philadelphia's National League team"},
    {"word": "NATIONALS", "category": "MLB team", "hint": "Washington's National League team"},
    {"word": "ORIOLES", "category": "MLB team", "hint": "Baltimore's American League team"},
    {"word": "BLUEJAYS", "category": "MLB team", "hint": "Toronto's American League team"},
    {"word": "REDSOX", "category": "MLB team", "hint": "Boston's American League team"},
    {"word": "RAYS", "category": "MLB team", "hint": "Tampa Bay's American League team"},
    {"word": "PADRES", "category": "MLB team", "hint": "San Diego's National League team"},
    {"word": "ROCKIES", "category": "MLB team", "hint": "Colorado's National League team"},
    {"word": "DIAMONDBACKS", "category": "MLB team", "hint": "Arizona's National League team"},
    {"word": "OHTANI", "category": "MLB player", "hint": "Two-way global superstar, ace and slugger in one"},
    {"word": "JUDGE", "category": "MLB player", "hint": "Towering Yankees right fielder known for majestic homers"},
    {"word": "TROUT", "category": "MLB player", "hint": "Longtime Angels center fielder, three-time MVP"},
    {"word": "BETTS", "category": "MLB player", "hint": "Dodgers outfielder who also stars at second base"},
    {"word": "FREEMAN", "category": "MLB player", "hint": "Smooth-swinging Dodgers first baseman"},
    {"word": "SOTO", "category": "MLB player", "hint": "Patient slugger famous for his shuffle at the plate"},
    {"word": "ALONSO", "category": "MLB player", "hint": "Mets first baseman nicknamed 'Polar Bear'"},
    {"word": "HARPER", "category": "MLB player", "hint": "Phillies slugger and former No. 1 overall pick"},
    {"word": "TATIS", "category": "MLB player", "hint": "Flashy Padres shortstop turned outfielder"},
    {"word": "GUERRERO", "category": "MLB player", "hint": "Blue Jays first baseman, son of a Hall of Famer"},
    {"word": "SEAGER", "category": "MLB player", "hint": "Rangers shortstop and World Series MVP"},
    {"word": "LINDOR", "category": "MLB player", "hint": "Mets shortstop nicknamed 'Mr. Smile'"},
    {"word": "DEVERS", "category": "MLB player", "hint": "Power-hitting infielder, formerly of Boston"},
    {"word": "BREGMAN", "category": "MLB player", "hint": "Third baseman known for his eye at the plate"},
    {"word": "CARROLL", "category": "MLB player", "hint": "Diamondbacks speedster and Rookie of the Year"},
    {"word": "WITT", "category": "MLB player", "hint": "Royals shortstop with elite speed and power"},
    {"word": "RAMIREZ", "category": "MLB player", "hint": "Guardians third baseman, a perennial All-Star"},
    {"word": "CORREA", "category": "MLB player", "hint": "Twins shortstop, former Astros star"},
    {"word": "MACHADO", "category": "MLB player", "hint": "Padres third baseman known for his smooth defense"},
    {"word": "ARENADO", "category": "MLB player", "hint": "Gold Glove third baseman, formerly of Colorado"},
    {"word": "YELICH", "category": "MLB player", "hint": "Brewers outfielder and former NL MVP"},
    {"word": "BUXTON", "category": "MLB player", "hint": "Twins center fielder with elite speed"},
    {"word": "CHAPMAN", "category": "MLB player", "hint": "Slick-fielding third baseman, a defensive standout"},
    {"word": "SCHERZER", "category": "MLB player", "hint": "Fiery veteran ace with three Cy Young Awards"},
    {"word": "VERLANDER", "category": "MLB player", "hint": "Future Hall of Fame ace with multiple no-hitters"},
    {"word": "COLE", "category": "MLB player", "hint": "Yankees ace known for his fastball and strikeouts"},
    {"word": "DEGROM", "category": "MLB player", "hint": "Dominant ace battling injuries throughout his career"},
    {"word": "SNELL", "category": "MLB player", "hint": "Left-handed Cy Young-winning starter"},
    {"word": "BURNES", "category": "MLB player", "hint": "Cutter-heavy ace, a former Brewers Cy Young winner"},
    {"word": "ALCANTARA", "category": "MLB player", "hint": "Marlins ace with a devastating changeup"},
    {"word": "STRIDER", "category": "MLB player", "hint": "Hard-throwing Braves strikeout machine"},
    {"word": "SKENES", "category": "MLB player", "hint": "Pirates phenom with a triple-digit fastball"},
    {"word": "KERSHAW", "category": "MLB player", "hint": "Longtime Dodgers ace, a three-time Cy Young winner"},
    {"word": "BICHETTE", "category": "MLB player", "hint": "Blue Jays shortstop known for his batting average"},
    {"word": "FENWAY", "category": "Ballpark", "hint": "Boston's ballpark, home to the Green Monster"},
    {"word": "WRIGLEY", "category": "Ballpark", "hint": "Chicago's ivy-covered ballpark on the North Side"},
    {"word": "CAMDEN", "category": "Ballpark", "hint": "Baltimore's retro-style ballpark downtown"},
    {"word": "TROPICANA", "category": "Ballpark", "hint": "Tampa Bay's domed ballpark"},
    {"word": "COORS", "category": "Ballpark", "hint": "Colorado's high-altitude, hitter-friendly park"},
    {"word": "CHASE", "category": "Ballpark", "hint": "Arizona's ballpark with a retractable roof and a pool"},
    {"word": "PETCO", "category": "Ballpark", "hint": "San Diego's downtown ballpark"},
    {"word": "ORACLE", "category": "Ballpark", "hint": "San Francisco's ballpark on the bay"},
    {"word": "TARGET", "category": "Ballpark", "hint": "Minnesota's open-air ballpark downtown"},
    {"word": "COMERICA", "category": "Ballpark", "hint": "Detroit's ballpark with a Ferris wheel and tigers"},
    {"word": "KAUFFMAN", "category": "Ballpark", "hint": "Kansas City's ballpark with a famous fountain display"},
    {"word": "PROGRESSIVE", "category": "Ballpark", "hint": "Cleveland's downtown ballpark"},
    {"word": "CITIZENS", "category": "Ballpark", "hint": "Philadelphia's ballpark, named for a bank"},
    {"word": "TRUIST", "category": "Ballpark", "hint": "Atlanta's ballpark in Cobb County"},
    {"word": "BUSCH", "category": "Ballpark", "hint": "St. Louis's ballpark, named for a beer baron"},
    {"word": "ANGEL", "category": "Ballpark", "hint": "Anaheim's ballpark, home of the Big A"},
    {"word": "DODGER", "category": "Ballpark", "hint": "Los Angeles's ballpark nestled in Chavez Ravine"},
    {"word": "YANKEE", "category": "Ballpark", "hint": "The Bronx's cathedral of baseball"},
    {"word": "CITIFIELD", "category": "Ballpark", "hint": "Queens's ballpark, home of the Mets"},
    {"word": "GLOBELIFE", "category": "Ballpark", "hint": "Texas's ballpark with a retractable roof"},
    {"word": "LOANDEPOT", "category": "Ballpark", "hint": "Miami's ballpark with a home run sculpture in play"},
    {"word": "TAG", "category": "Baseball term", "hint": "Touching a runner with the ball to record an out"},
    {"word": "OUT", "category": "Baseball term", "hint": "A defense needs 27 of these to win"},
    {"word": "SAFE", "category": "Baseball term", "hint": "The call when a runner beats the tag or the throw"},
    {"word": "BASE", "category": "Baseball term", "hint": "One of four stations a runner must touch to score"},
    {"word": "PLATE", "category": "Baseball term", "hint": "Where the batter stands and runs must cross to score"},
    {"word": "MOUND", "category": "Baseball term", "hint": "The raised dirt circle a pitcher throws from"},
    {"word": "DIRT", "category": "Baseball term", "hint": "The infield surface, as opposed to the grass"},
    {"word": "TURF", "category": "Baseball term", "hint": "Artificial playing surface used in some ballparks"},
    {"word": "RALLY", "category": "Baseball term", "hint": "A sudden burst of scoring late in a game"},
    {"word": "SLUMP", "category": "Baseball term", "hint": "A frustrating stretch of poor performance at the plate"},
    {"word": "STREAK", "category": "Baseball term", "hint": "A run of consecutive games with the same outcome"},
    {"word": "FORFEIT", "category": "Baseball term", "hint": "A loss awarded when a team can't or won't continue"},
    {"word": "PROTEST", "category": "Baseball term", "hint": "A formal complaint about an umpire's ruling"},
    {"word": "REPLAY", "category": "Baseball term", "hint": "Video review used to overturn a close call"},
    {"word": "CHALLENGE", "category": "Baseball term", "hint": "A manager's request for video review"},
    {"word": "ROBBERY", "category": "Baseball term", "hint": "Slang for a spectacular catch that steals a hit away"},
    {"word": "ASSIST", "category": "Baseball term", "hint": "Defensive credit for helping record an out"},
    {"word": "PUTOUT", "category": "Baseball term", "hint": "Defensive credit for making the final play on an out"},
    {"word": "RUNDOWN", "category": "Baseball term", "hint": "A defensive play trapping a runner between bases"},
    {"word": "OBSTRUCTION", "category": "Baseball term", "hint": "Illegally hindering a runner's path on the bases"},
    {"word": "INTERFERENCE", "category": "Baseball term", "hint": "Illegally hindering a play, by a fielder or a batter"},
    {"word": "PALMBALL", "category": "Baseball term", "hint": "A rare off-speed pitch gripped deep in the palm"},
    {"word": "SCREWBALL", "category": "Baseball term", "hint": "A pitch that breaks the opposite way of a curve"},
    {"word": "EPHUS", "category": "Baseball term", "hint": "A famously slow, looping junk pitch"},
    {"word": "RAINOUT", "category": "Baseball term", "hint": "A game canceled or delayed by weather"},
    {"word": "DOUBLEHEADER", "category": "Baseball term", "hint": "Two games between the same teams in one day"},
    {"word": "NIGHTCAP", "category": "Baseball term", "hint": "The second game of a same-day twin bill"},
    {"word": "HOMESTAND", "category": "Baseball term", "hint": "A stretch of consecutive games at home"},
    {"word": "CALLUP", "category": "Baseball term", "hint": "A promotion from the minor leagues"},
    {"word": "UTILITY", "category": "Baseball term", "hint": "A player who fills in at multiple positions"},
    {"word": "WORKHORSE", "category": "Baseball term", "hint": "A durable starter who eats up innings"},
    {"word": "MASCOT", "category": "Baseball term", "hint": "A costumed character who entertains the crowd"},
    {"word": "CONCESSIONS", "category": "Baseball term", "hint": "Where fans buy hot dogs and peanuts"},
    {"word": "CRACKERJACK", "category": "Baseball term", "hint": "The classic snack sung about at the seventh inning stretch"},
    {"word": "STRETCH", "category": "Baseball term", "hint": "The traditional pause for fans midway through the 7th"},
    {"word": "ANTHEM", "category": "Baseball term", "hint": "Sung before the first pitch of every game"},
    {"word": "AUTOGRAPH", "category": "Baseball term", "hint": "What fans wait by the dugout to collect"},
    {"word": "GROUNDSKEEPER", "category": "Baseball term", "hint": "Maintains the field, mound, and basepaths"},
    {"word": "BATBOY", "category": "Baseball term", "hint": "A young clubhouse helper who retrieves bats"},
    {"word": "TARP", "category": "Baseball term", "hint": "Rolled onto the field to protect it from rain"},
    {"word": "CURFEW", "category": "Baseball term", "hint": "A rule that can suspend a game late at night"},
    {"word": "MATINEE", "category": "Baseball term", "hint": "An afternoon game"},
    {"word": "CONTRERAS", "category": "MLB player", "hint": "Veteran catcher known for his arm and power"},
    {"word": "OLSON", "category": "MLB player", "hint": "Power-hitting first baseman, an Atlanta Gold Glover"},
    {"word": "RILEY", "category": "MLB player", "hint": "Braves third baseman with a smooth left-handed swing"},
    {"word": "ALBIES", "category": "MLB player", "hint": "Braves second baseman known for his speed and bat"},
    {"word": "ACUNA", "category": "MLB player", "hint": "Explosive Braves outfielder, a 40-40 club member"},
    {"word": "WHEELER", "category": "MLB player", "hint": "Phillies ace with a blistering fastball"},
    {"word": "NOLA", "category": "MLB player", "hint": "Steady Phillies workhorse starter"},
    {"word": "GAUSMAN", "category": "MLB player", "hint": "Splitter-throwing Blue Jays ace"},
    {"word": "CEASE", "category": "MLB player", "hint": "Strikeout artist known for a wipeout slider"},
    {"word": "GALLEN", "category": "MLB player", "hint": "Diamondbacks ace with pinpoint control"},
    {"word": "FRIED", "category": "MLB player", "hint": "Braves left-hander known for his curveball"},
    {"word": "VALDEZ", "category": "MLB player", "hint": "Astros sinkerball specialist"},
    {"word": "EOVALDI", "category": "MLB player", "hint": "Hard-throwing veteran starter, a World Series stalwart"},
    {"word": "MONTGOMERY", "category": "MLB player", "hint": "Left-handed starter who's changed teams a few times"},
    {"word": "RODON", "category": "MLB player", "hint": "Power lefty who's pitched a no-hitter"},
    {"word": "BAZ", "category": "MLB player", "hint": "Young Rays starter with a big fastball-slider combo"},
    {"word": "RALEIGH", "category": "MLB player", "hint": "Switch-hitting Mariners catcher nicknamed 'Big Dumper'"},
    {"word": "SUAREZ", "category": "MLB player", "hint": "Power-hitting infielder known for chasing 40 homers"},
    {"word": "TURNER", "category": "MLB player", "hint": "Speedy shortstop known for his sprint speed"},
    {"word": "STORY", "category": "MLB player", "hint": "Shortstop whose name doubles as a baseball pun"},
    {"word": "HENDERSON", "category": "MLB player", "hint": "Rising Orioles shortstop star"},
    {"word": "MOUNTCASTLE", "category": "MLB player", "hint": "Orioles first baseman with raw power"},
    {"word": "OZUNA", "category": "MLB player", "hint": "Braves slugger known for his prodigious power"},
]

for _entry in WORD_BANK:
    assert _entry["word"].isalpha() and _entry["word"] == _entry["word"].upper(), (
        f"WORD_BANK entry {_entry['word']!r} must be uppercase letters only"
    )

assert len({_entry["word"] for _entry in WORD_BANK}) == len(WORD_BANK), "duplicate word in WORD_BANK"

# Normal mode stays quick to guess (3-6 letters); Hard mode is everything
# longer. Each cycles independently through its own pool.
NORMAL_WORD_BANK = [e for e in WORD_BANK if 3 <= len(e["word"]) <= 6]
HARD_WORD_BANK = [e for e in WORD_BANK if len(e["word"]) >= 7]


def get_daily_word(bank, today):
    """Deterministic pick so every visitor sees the same puzzle on a given
    date, without repeating a word until the whole bank has cycled through
    (then reshuffles, seeded by cycle number, so the order changes each
    time around). August 17, 2026 (WORD_GAME_EPOCH) is day one."""
    day_index = (today - WORD_GAME_EPOCH).days
    cycle_length = len(bank)
    cycle_number = day_index // cycle_length
    position = day_index % cycle_length
    shuffled = bank[:]
    random.Random(cycle_number).shuffle(shuffled)
    return shuffled[position]


def build_word_archive(bank, today):
    """Every day's answer from WORD_GAME_EPOCH through today, so the page can
    let a visitor page backward/forward through past puzzles without a
    server - the whole archive ships client-side as JSON."""
    num_days = (today - WORD_GAME_EPOCH).days + 1
    archive = []
    for i in range(num_days):
        day = WORD_GAME_EPOCH + datetime.timedelta(days=i)
        entry = get_daily_word(bank, day)
        archive.append({
            "date_key": day.isoformat(),
            "answer": entry["word"],
            "category": entry["category"],
            "hint": entry["hint"],
        })
    return archive


# Plain strings (not passed through .format) so JS/CSS braces stay literal -
# only WORD_GAME_TEMPLATE itself goes through .format, and it just drops
# these in as-is via {game_js}/{game_css}.
WORD_GAME_JS = """
const wordData = JSON.parse(document.getElementById('word-data').textContent);

const board = document.getElementById('board');
const message = document.getElementById('message');
const keyboardEl = document.getElementById('keyboard');
const resultEl = document.getElementById('result');
const resultText = document.getElementById('result-text');
const resultHint = document.getElementById('result-hint');
const dateEl = document.getElementById('word-date');
const categoryEl = document.getElementById('word-category');
const lengthEl = document.getElementById('word-length');
const prevBtn = document.getElementById('prev-day');
const nextBtn = document.getElementById('next-day');
const modeNormalBtn = document.getElementById('mode-normal');
const modeHardBtn = document.getElementById('mode-hard');

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function formatDateKey(dateKey) {
  const [y, m, d] = dateKey.split('-').map(Number);
  return MONTH_NAMES[m - 1] + ' ' + d + ', ' + y;
}

function currentModeData() {
  return wordData[currentMode];
}

let currentMode = 'normal';
let currentIndex = wordData.normal.todayIndex;
let ANSWER = '';
let CATEGORY = '';
let HINT = '';
let LENGTH = 0;
let STORAGE_KEY = '';
let guesses = [];
let currentGuess = '';
let gameOver = false;
const keyStatus = {};

function loadState() {
  guesses = [];
  gameOver = false;
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (raw && Array.isArray(raw.guesses)) {
      guesses = raw.guesses;
      gameOver = !!raw.gameOver;
    }
  } catch (e) {}
}

function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ guesses, gameOver }));
  } catch (e) {}
}

function evaluateGuess(guess) {
  const result = new Array(guess.length).fill('absent');
  const answerArr = ANSWER.split('');
  const used = new Array(ANSWER.length).fill(false);
  for (let i = 0; i < guess.length; i++) {
    if (guess[i] === answerArr[i]) {
      result[i] = 'correct';
      used[i] = true;
    }
  }
  for (let i = 0; i < guess.length; i++) {
    if (result[i] === 'correct') continue;
    const idx = answerArr.findIndex((c, j) => c === guess[i] && !used[j]);
    if (idx !== -1) {
      result[i] = 'present';
      used[idx] = true;
    }
  }
  return result;
}

function buildBoard() {
  board.innerHTML = '';
  board.style.setProperty('--cols', LENGTH);
  for (let r = 0; r < LENGTH; r++) {
    const row = document.createElement('div');
    row.className = 'word-row';
    for (let c = 0; c < LENGTH; c++) {
      const tile = document.createElement('div');
      tile.className = 'word-tile';
      row.appendChild(tile);
    }
    board.appendChild(row);
  }
}

function renderGuessRow(rowIndex, guess) {
  const row = board.children[rowIndex];
  const evalResult = evaluateGuess(guess);
  for (let c = 0; c < LENGTH; c++) {
    const tile = row.children[c];
    tile.textContent = guess[c];
    tile.classList.add('filled', evalResult[c]);
  }
  return evalResult;
}

function renderCurrentRow() {
  const rowIndex = guesses.length;
  if (rowIndex >= LENGTH) return;
  const row = board.children[rowIndex];
  for (let c = 0; c < LENGTH; c++) {
    const tile = row.children[c];
    tile.textContent = currentGuess[c] || '';
    tile.classList.toggle('filled', !!currentGuess[c]);
  }
}

const KEY_ROWS = [
  'QWERTYUIOP'.split(''),
  'ASDFGHJKL'.split(''),
  ['ENTER', ...'ZXCVBNM'.split(''), 'BACK'],
];

function buildKeyboard() {
  keyboardEl.innerHTML = '';
  KEY_ROWS.forEach((keys) => {
    const row = document.createElement('div');
    row.className = 'keyboard-row';
    keys.forEach((key) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'key';
      if (key === 'ENTER' || key === 'BACK') btn.classList.add('key-wide');
      btn.textContent = key === 'BACK' ? '\\u232B' : (key === 'ENTER' ? 'Enter' : key);
      btn.dataset.key = key;
      btn.addEventListener('click', () => handleKey(key));
      row.appendChild(btn);
    });
    keyboardEl.appendChild(row);
  });
}

function resetKeyboardColors() {
  Object.keys(keyStatus).forEach((k) => delete keyStatus[k]);
  keyboardEl.querySelectorAll('.key').forEach((btn) => {
    btn.classList.remove('correct', 'present', 'absent');
  });
}

function updateKeyboardColors(guess, evalResult) {
  const priority = { absent: 0, present: 1, correct: 2 };
  for (let i = 0; i < guess.length; i++) {
    const letter = guess[i];
    const status = evalResult[i];
    if (!keyStatus[letter] || priority[status] > priority[keyStatus[letter]]) {
      keyStatus[letter] = status;
    }
  }
  keyboardEl.querySelectorAll('.key').forEach((btn) => {
    const k = btn.dataset.key;
    btn.classList.remove('correct', 'present', 'absent');
    if (keyStatus[k]) btn.classList.add(keyStatus[k]);
  });
}

function showMessage(text) {
  message.textContent = text;
  clearTimeout(showMessage._t);
  if (text) {
    showMessage._t = setTimeout(() => { message.textContent = ''; }, 1800);
  }
}

function showResult(won) {
  resultEl.hidden = false;
  resultText.textContent = won
    ? `You got it in ${guesses.length} / ${LENGTH}!`
    : `Out of guesses. The answer was ${ANSWER}.`;
  resultHint.textContent = `${CATEGORY}: ${HINT}`;
}

function endGame(won) {
  gameOver = true;
  saveState();
  showResult(won);
}

function submitGuess() {
  if (gameOver) return;
  if (currentGuess.length !== LENGTH) {
    showMessage(`Answer is ${LENGTH} letters`);
    return;
  }
  const evalResult = renderGuessRow(guesses.length, currentGuess);
  updateKeyboardColors(currentGuess, evalResult);
  guesses.push(currentGuess);
  const won = currentGuess === ANSWER;
  currentGuess = '';
  saveState();
  if (won) {
    endGame(true);
  } else if (guesses.length >= LENGTH) {
    endGame(false);
  }
}

function handleKey(key) {
  if (gameOver) return;
  if (key === 'ENTER') {
    submitGuess();
  } else if (key === 'BACK') {
    currentGuess = currentGuess.slice(0, -1);
    renderCurrentRow();
  } else if (/^[A-Z]$/.test(key) && currentGuess.length < LENGTH) {
    currentGuess += key;
    renderCurrentRow();
  }
}

document.addEventListener('keydown', (e) => {
  if (gameOver) return;
  if (e.key === 'Enter') { handleKey('ENTER'); return; }
  if (e.key === 'Backspace') { handleKey('BACK'); return; }
  const letter = e.key.toUpperCase();
  if (/^[A-Z]$/.test(letter)) handleKey(letter);
});

function updateNavButtons() {
  const todayIndex = currentModeData().todayIndex;
  prevBtn.disabled = currentIndex <= 0;
  nextBtn.disabled = currentIndex >= todayIndex;
}

function loadDay(index) {
  const modeData = currentModeData();
  index = Math.max(0, Math.min(modeData.todayIndex, index));
  currentIndex = index;
  const entry = modeData.archive[index];
  ANSWER = entry.answer;
  CATEGORY = entry.category;
  HINT = entry.hint;
  LENGTH = ANSWER.length;
  STORAGE_KEY = 'bordle-' + currentMode + '-' + entry.date_key;
  currentGuess = '';
  message.textContent = '';
  resultEl.hidden = true;

  dateEl.textContent = formatDateKey(entry.date_key) + (index === modeData.todayIndex ? ' (Today)' : '');
  categoryEl.textContent = CATEGORY;
  lengthEl.textContent = `${LENGTH} letters \\u00b7 ${LENGTH} guesses`;

  buildBoard();
  resetKeyboardColors();
  loadState();
  guesses.forEach((g, i) => {
    const evalResult = renderGuessRow(i, g);
    updateKeyboardColors(g, evalResult);
  });
  if (gameOver) {
    const won = guesses.length > 0 && guesses[guesses.length - 1] === ANSWER;
    showResult(won);
  }
  updateNavButtons();
}

function setMode(mode) {
  if (mode === currentMode) return;
  currentMode = mode;
  modeNormalBtn.classList.toggle('active', mode === 'normal');
  modeHardBtn.classList.toggle('active', mode === 'hard');
  loadDay(currentIndex);
}

prevBtn.addEventListener('click', () => { loadDay(currentIndex - 1); prevBtn.blur(); });
nextBtn.addEventListener('click', () => { loadDay(currentIndex + 1); nextBtn.blur(); });
modeNormalBtn.addEventListener('click', () => { setMode('normal'); modeNormalBtn.blur(); });
modeHardBtn.addEventListener('click', () => { setMode('hard'); modeHardBtn.blur(); });

buildKeyboard();
loadDay(currentIndex);
"""

WORD_GAME_CSS = """
:root { color-scheme: dark; }
body {
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.site-header {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}
.brand { display: flex; align-items: center; gap: 8px; min-width: 0; }
.site-header h1 { font-size: 1.15rem; margin: 0; white-space: nowrap; }
.site-header .date { color: #999; font-size: 0.9rem; white-space: nowrap; }
.header-link {
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}
.header-link:hover { border-color: #ff5266; color: #ff5266; }

.disclaimer {
  max-width: 480px; margin: 1.25rem auto 0; padding: 0.75rem 1rem;
  color: #aaa; font-size: 0.85rem; line-height: 1.5;
  background: rgba(255,82,102,0.06); border-left: 3px solid #ff5266; border-radius: 0 8px 8px 0;
}

.word-main {
  max-width: 480px; margin: 0 auto; padding: 1rem 1.25rem 2rem;
  display: flex; flex-direction: column; align-items: center; gap: 1rem;
}
.mode-toggle { display: flex; gap: 6px; }
.mode-btn {
  background: #1a1a1a; border: 1px solid #333; color: #ccc;
  padding: 0.4rem 1rem; border-radius: 999px; font-size: 0.8rem; font-weight: 600;
  cursor: pointer; transition: all 0.15s ease;
}
.mode-btn:hover { border-color: #ff5266; color: #ff5266; }
.mode-btn.active { background: rgba(255,82,102,0.15); border-color: #ff5266; color: #ff5266; }
.word-nav { display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 0.5rem; }
.word-nav-info { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 2px; flex: 1; min-width: 0; }
.word-date { font-size: 0.85rem; font-weight: 700; color: #f2f2f2; white-space: nowrap; }
.word-category { font-size: 0.75rem; color: #999; }
.nav-btn {
  background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.4rem 0.8rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
  cursor: pointer; white-space: nowrap; flex-shrink: 0;
  transition: border-color 0.15s ease, color 0.15s ease, opacity 0.15s ease;
}
.nav-btn:hover:not(:disabled) { border-color: #ff5266; color: #ff5266; }
.nav-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.word-length { font-size: 0.78rem; color: #999; }
.word-board { display: flex; flex-direction: column; gap: 6px; width: min(100%, calc(var(--cols) * 3rem)); }
.word-row { display: grid; grid-template-columns: repeat(var(--cols), 1fr); gap: 6px; }
.word-tile {
  aspect-ratio: 1; border: 2px solid #333; border-radius: 6px;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: clamp(0.6rem, calc(2.2rem - var(--cols) * 0.12rem), 1.4rem);
  text-transform: uppercase; color: #f2f2f2; font-family: ui-monospace, monospace;
}
.word-tile.filled { border-color: #555; }
.word-tile.correct { background: #4ade80; border-color: #4ade80; color: #0d0d0d; }
.word-tile.present { background: #ffd166; border-color: #ffd166; color: #0d0d0d; }
.word-tile.absent { background: #262626; border-color: #262626; color: #777; }
.word-message { min-height: 1.2rem; font-size: 0.85rem; color: #ff5266; font-weight: 600; margin: 0; }

.keyboard { display: flex; flex-direction: column; gap: 6px; width: 100%; max-width: 480px; }
.keyboard-row { display: flex; justify-content: center; gap: 5px; }
.key {
  flex: 1; max-width: 40px; padding: 0.7rem 0; border: none; border-radius: 6px;
  background: #2a2a2a; color: #f2f2f2; font-weight: 700; font-size: 0.8rem; cursor: pointer;
  transition: background 0.15s ease;
}
.key:hover { background: #3a3a3a; }
.key-wide { max-width: 62px; font-size: 0.7rem; }
.key.correct { background: #4ade80; color: #0d0d0d; }
.key.present { background: #ffd166; color: #0d0d0d; }
.key.absent { background: #1a1a1a; color: #555; }

.word-result {
  text-align: center; background: #14141a; border: 1px solid #262626; border-radius: 10px;
  padding: 1rem; width: 100%;
}
#result-text { margin: 0 0 0.4rem; font-weight: 700; font-size: 1rem; }
#result-hint { margin: 0; color: #999; font-size: 0.85rem; }
"""

WORD_GAME_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Bordle - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Bordle: a daily baseball word guessing game, MLB-themed and free to play.">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>Bordle</h1>
  </div>
  <span class="date">{date_label}</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<p class="disclaimer">
  A new baseball word every day - players, teams, ballparks, and terminology, all mixed together.
  Guesses aren't checked against a dictionary, just letter count. You get one guess per letter in
  the word, so longer answers come with more tries. Normal mode keeps it to 3-6 letter words; Hard
  mode is 7 letters and up. Use Prev/Next to replay past puzzles.
</p>
<main class="word-main">
  <div class="mode-toggle">
    <button type="button" id="mode-normal" class="mode-btn active">Normal</button>
    <button type="button" id="mode-hard" class="mode-btn">Hard</button>
  </div>
  <div class="word-nav">
    <button type="button" id="prev-day" class="nav-btn">&larr; Prev</button>
    <div class="word-nav-info">
      <span id="word-date" class="word-date"></span>
      <span id="word-category" class="word-category"></span>
    </div>
    <button type="button" id="next-day" class="nav-btn">Next &rarr;</button>
  </div>
  <span id="word-length" class="word-length"></span>
  <div id="board" class="word-board"></div>
  <p id="message" class="word-message" aria-live="polite"></p>
  <div id="keyboard" class="keyboard"></div>
  <div id="result" class="word-result" hidden>
    <p id="result-text"></p>
    <p id="result-hint"></p>
  </div>
</main>

<script id="word-data" type="application/json">{word_data_json}</script>
<script>{game_js}</script>

<style>{game_css}</style>
</body>
</html>
"""


def build_word_game_page(today):
    normal_archive = build_word_archive(NORMAL_WORD_BANK, today)
    hard_archive = build_word_archive(HARD_WORD_BANK, today)
    word_data_json = json.dumps({
        "normal": {"archive": normal_archive, "todayIndex": len(normal_archive) - 1},
        "hard": {"archive": hard_archive, "todayIndex": len(hard_archive) - 1},
    }).replace("</", "<\\/")
    return WORD_GAME_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        date_label=today.strftime("%A, %B %-d, %Y"),
        word_data_json=word_data_json,
        game_js=WORD_GAME_JS,
        game_css=WORD_GAME_CSS,
    )


GRID_GAME_FILE = "diamond-grid.html"
GRID_GAME_EPOCH = datetime.date(2026, 8, 17)  # same "day one" as Bordle

# key -> display label. Only includes achievements I'm confident are
# accurate for every tagged player below (well-documented career facts -
# awards, milestones, single-season/career thresholds).
GRID_ACHIEVEMENTS = {
    "hof": "Hall of Fame",
    "mvp": "MVP Award",
    "cy_young": "Cy Young Award",
    "roy": "Rookie of the Year",
    "ws_champ": "World Series Champion",
    "ws_mvp": "World Series MVP",
    "triple_crown": "Triple Crown Season",
    "500hr": "500+ Career Home Runs",
    "3000hits": "3,000+ Career Hits",
    "300wins": "300+ Career Wins",
    "3000k": "3,000+ Career Strikeouts",
    "40hr_season": "40+ HR in a Season",
    "50hr_season": "50+ HR in a Season",
    "20win_season": "20+ Wins in a Season",
    "no_hitter": "Threw a No-Hitter",
    "batting_title": "Won a Batting Title",
    "gold_glove": "Gold Glove Winner",
    "allstar5": "5+ Time All-Star",
    "200hits_season": "200+ Hits in a Season",
    "40sb_season": "40+ Stolen Bases in a Season",
}

# Curated player pool for the grid - teams use current-franchise names
# (matching TEAM_ABBR) so a historical team maps to its present-day
# successor (e.g. Brooklyn/LA Dodgers, Montreal Expos -> Washington
# Nationals, Oakland/KC/Philadelphia Athletics -> Athletics).
GRID_PLAYERS = [
    # --- Modern superstars (active or recently retired) ---
    {"name": "Shohei Ohtani", "teams": ["Los Angeles Angels", "Los Angeles Dodgers"],
     "tags": ["mvp", "allstar5", "40hr_season", "roy"]},
    {"name": "Mike Trout", "teams": ["Los Angeles Angels"],
     "tags": ["mvp", "allstar5", "roy", "40hr_season"]},
    {"name": "Aaron Judge", "teams": ["New York Yankees"],
     "tags": ["mvp", "allstar5", "roy", "50hr_season", "40hr_season"]},
    {"name": "Mookie Betts", "teams": ["Boston Red Sox", "Los Angeles Dodgers"],
     "tags": ["mvp", "allstar5", "ws_champ", "gold_glove", "batting_title"]},
    {"name": "Freddie Freeman", "teams": ["Atlanta Braves", "Los Angeles Dodgers"],
     "tags": ["mvp", "allstar5", "ws_champ", "ws_mvp", "batting_title"]},
    {"name": "Bryce Harper", "teams": ["Washington Nationals", "Philadelphia Phillies"],
     "tags": ["mvp", "allstar5", "roy", "40hr_season"]},
    {"name": "Clayton Kershaw", "teams": ["Los Angeles Dodgers"],
     "tags": ["mvp", "cy_young", "allstar5", "ws_champ", "20win_season", "no_hitter"]},
    {"name": "Justin Verlander", "teams": ["Detroit Tigers", "Houston Astros"],
     "tags": ["mvp", "cy_young", "roy", "allstar5", "ws_champ", "20win_season", "no_hitter", "3000k"]},
    {"name": "Max Scherzer", "teams": ["Detroit Tigers", "Washington Nationals", "New York Mets"],
     "tags": ["cy_young", "allstar5", "ws_champ", "20win_season", "no_hitter", "3000k"]},
    {"name": "Jacob deGrom", "teams": ["New York Mets", "Texas Rangers"],
     "tags": ["cy_young", "roy", "allstar5"]},
    {"name": "Corbin Burnes", "teams": ["Milwaukee Brewers", "Baltimore Orioles", "Arizona Diamondbacks"],
     "tags": ["cy_young", "allstar5"]},
    {"name": "Paul Skenes", "teams": ["Pittsburgh Pirates"], "tags": ["roy", "allstar5"]},
    {"name": "Gerrit Cole", "teams": ["Pittsburgh Pirates", "Houston Astros", "New York Yankees"],
     "tags": ["cy_young", "allstar5", "ws_champ"]},
    {"name": "Jose Altuve", "teams": ["Houston Astros"],
     "tags": ["mvp", "allstar5", "ws_champ", "batting_title", "200hits_season"]},
    {"name": "Manny Machado", "teams": ["Baltimore Orioles", "San Diego Padres"],
     "tags": ["allstar5", "gold_glove", "40hr_season"]},
    {"name": "Nolan Arenado", "teams": ["Colorado Rockies", "St. Louis Cardinals"],
     "tags": ["allstar5", "gold_glove", "40hr_season"]},
    {"name": "Francisco Lindor", "teams": ["Cleveland Guardians", "New York Mets"],
     "tags": ["allstar5", "gold_glove"]},
    {"name": "Jose Ramirez", "teams": ["Cleveland Guardians"], "tags": ["allstar5", "40sb_season"]},
    {"name": "Juan Soto", "teams": ["Washington Nationals", "San Diego Padres", "New York Yankees", "New York Mets"],
     "tags": ["allstar5", "ws_champ", "batting_title"]},
    {"name": "Vladimir Guerrero Jr.", "teams": ["Toronto Blue Jays"], "tags": ["allstar5", "40hr_season"]},
    {"name": "Ronald Acuna Jr.", "teams": ["Atlanta Braves"],
     "tags": ["mvp", "roy", "allstar5", "ws_champ", "40hr_season", "40sb_season"]},
    {"name": "Kyle Schwarber", "teams": ["Chicago Cubs", "Philadelphia Phillies"],
     "tags": ["ws_champ", "allstar5", "50hr_season", "40hr_season"]},
    {"name": "Pete Alonso", "teams": ["New York Mets"], "tags": ["roy", "allstar5", "40hr_season"]},
    {"name": "Yordan Alvarez", "teams": ["Houston Astros"], "tags": ["roy", "allstar5", "ws_champ", "40hr_season"]},
    {"name": "Corey Seager", "teams": ["Los Angeles Dodgers", "Texas Rangers"],
     "tags": ["roy", "allstar5", "ws_champ", "ws_mvp"]},
    {"name": "Buster Posey", "teams": ["San Francisco Giants"],
     "tags": ["mvp", "roy", "allstar5", "ws_champ", "batting_title", "gold_glove"]},
    {"name": "Madison Bumgarner", "teams": ["San Francisco Giants", "Arizona Diamondbacks"],
     "tags": ["ws_champ", "ws_mvp", "allstar5"]},
    {"name": "Yadier Molina", "teams": ["St. Louis Cardinals"],
     "tags": ["ws_champ", "allstar5", "gold_glove"]},
    {"name": "Albert Pujols", "teams": ["St. Louis Cardinals", "Los Angeles Angels", "Los Angeles Dodgers"],
     "tags": ["mvp", "roy", "allstar5", "ws_champ", "hof", "500hr", "3000hits", "batting_title", "40hr_season", "gold_glove"]},
    {"name": "David Ortiz", "teams": ["Minnesota Twins", "Boston Red Sox"],
     "tags": ["hof", "allstar5", "ws_champ", "ws_mvp", "500hr", "40hr_season"]},
    {"name": "Ichiro Suzuki", "teams": ["Seattle Mariners", "New York Yankees", "Miami Marlins"],
     "tags": ["hof", "mvp", "roy", "allstar5", "3000hits", "batting_title", "gold_glove", "200hits_season"]},
    {"name": "Chipper Jones", "teams": ["Atlanta Braves"],
     "tags": ["hof", "mvp", "roy", "allstar5", "ws_champ", "batting_title"]},
    {"name": "Derek Jeter", "teams": ["New York Yankees"],
     "tags": ["hof", "roy", "allstar5", "ws_champ", "3000hits", "gold_glove", "200hits_season"]},
    {"name": "Mariano Rivera", "teams": ["New York Yankees"], "tags": ["hof", "allstar5", "ws_champ", "ws_mvp"]},
    {"name": "Andy Pettitte", "teams": ["New York Yankees", "Houston Astros"],
     "tags": ["allstar5", "ws_champ", "20win_season"]},
    {"name": "Alex Rodriguez", "teams": ["Seattle Mariners", "Texas Rangers", "New York Yankees"],
     "tags": ["mvp", "roy", "allstar5", "ws_champ", "500hr", "3000hits", "40hr_season", "50hr_season", "batting_title", "gold_glove"]},
    {"name": "Ken Griffey Jr.", "teams": ["Seattle Mariners", "Cincinnati Reds", "Chicago White Sox"],
     "tags": ["hof", "mvp", "allstar5", "500hr", "40hr_season", "50hr_season", "gold_glove"]},
    {"name": "Randy Johnson", "teams": ["Seattle Mariners", "Houston Astros", "Arizona Diamondbacks",
                                          "New York Yankees", "San Francisco Giants"],
     "tags": ["hof", "cy_young", "allstar5", "ws_champ", "ws_mvp", "20win_season", "no_hitter", "3000k", "300wins"]},
    {"name": "Pedro Martinez", "teams": ["Los Angeles Dodgers", "Washington Nationals", "Boston Red Sox", "New York Mets"],
     "tags": ["hof", "cy_young", "allstar5", "20win_season", "3000k"]},
    {"name": "Greg Maddux", "teams": ["Chicago Cubs", "Atlanta Braves", "Los Angeles Dodgers", "San Diego Padres"],
     "tags": ["hof", "cy_young", "allstar5", "ws_champ", "20win_season", "gold_glove", "300wins"]},
    {"name": "Tom Glavine", "teams": ["Atlanta Braves", "New York Mets"],
     "tags": ["hof", "cy_young", "allstar5", "ws_champ", "ws_mvp", "20win_season", "300wins"]},
    {"name": "John Smoltz", "teams": ["Atlanta Braves"],
     "tags": ["hof", "cy_young", "allstar5", "ws_champ", "20win_season", "3000k"]},
    {"name": "Roger Clemens", "teams": ["Boston Red Sox", "Toronto Blue Jays", "New York Yankees", "Houston Astros"],
     "tags": ["mvp", "cy_young", "allstar5", "ws_champ", "20win_season", "3000k", "300wins"]},
    {"name": "Frank Thomas", "teams": ["Chicago White Sox", "Toronto Blue Jays", "Athletics"],
     "tags": ["hof", "mvp", "allstar5", "500hr", "batting_title", "40hr_season"]},
    {"name": "Jim Thome", "teams": ["Cleveland Guardians", "Philadelphia Phillies", "Chicago White Sox",
                                     "Los Angeles Dodgers", "Minnesota Twins", "Baltimore Orioles"],
     "tags": ["hof", "allstar5", "500hr", "40hr_season"]},
    {"name": "Manny Ramirez", "teams": ["Cleveland Guardians", "Boston Red Sox", "Los Angeles Dodgers",
                                          "Chicago White Sox", "Tampa Bay Rays"],
     "tags": ["mvp", "allstar5", "ws_champ", "ws_mvp", "500hr", "40hr_season", "batting_title"]},
    {"name": "Vladimir Guerrero", "teams": ["Washington Nationals", "Los Angeles Angels", "Texas Rangers", "Baltimore Orioles"],
     "tags": ["hof", "mvp", "allstar5", "batting_title", "40hr_season"]},
    {"name": "Jeff Bagwell", "teams": ["Houston Astros"], "tags": ["hof", "mvp", "roy", "allstar5", "40hr_season"]},
    {"name": "Craig Biggio", "teams": ["Houston Astros"], "tags": ["hof", "allstar5", "3000hits", "gold_glove", "200hits_season"]},
    {"name": "Trevor Hoffman", "teams": ["Miami Marlins", "San Diego Padres", "Milwaukee Brewers"],
     "tags": ["hof", "allstar5"]},
    {"name": "Lee Smith", "teams": ["Chicago Cubs", "Boston Red Sox", "St. Louis Cardinals", "New York Yankees",
                                     "Baltimore Orioles", "Cincinnati Reds", "Washington Nationals"],
     "tags": ["hof", "allstar5"]},
    {"name": "Ivan Rodriguez", "teams": ["Texas Rangers", "Miami Marlins", "Detroit Tigers", "Houston Astros",
                                           "New York Yankees", "Washington Nationals"],
     "tags": ["hof", "mvp", "allstar5", "ws_champ", "gold_glove", "batting_title"]},
    {"name": "Mike Piazza", "teams": ["Los Angeles Dodgers", "New York Mets", "San Diego Padres", "Athletics"],
     "tags": ["hof", "roy", "allstar5"]},
    {"name": "Barry Larkin", "teams": ["Cincinnati Reds"], "tags": ["hof", "mvp", "allstar5", "ws_champ", "gold_glove"]},
    {"name": "Roberto Alomar", "teams": ["San Diego Padres", "Toronto Blue Jays", "Baltimore Orioles",
                                           "Cleveland Guardians", "New York Mets", "Chicago White Sox", "Arizona Diamondbacks"],
     "tags": ["hof", "allstar5", "ws_champ", "gold_glove", "batting_title"]},
    {"name": "Cal Ripken Jr.", "teams": ["Baltimore Orioles"],
     "tags": ["hof", "mvp", "roy", "allstar5", "gold_glove", "200hits_season"]},
    {"name": "Eddie Murray", "teams": ["Baltimore Orioles", "Los Angeles Dodgers", "New York Mets",
                                         "Cleveland Guardians", "Los Angeles Angels"],
     "tags": ["hof", "roy", "allstar5", "500hr", "3000hits"]},
    {"name": "Wade Boggs", "teams": ["Boston Red Sox", "New York Yankees", "Tampa Bay Rays"],
     "tags": ["hof", "allstar5", "ws_champ", "3000hits", "batting_title", "200hits_season"]},
    {"name": "Rickey Henderson", "teams": ["Athletics", "New York Yankees", "Toronto Blue Jays",
                                             "San Diego Padres", "Boston Red Sox", "Los Angeles Dodgers"],
     "tags": ["hof", "mvp", "allstar5", "ws_champ", "3000hits", "40sb_season"]},
    {"name": "Tony Gwynn", "teams": ["San Diego Padres"],
     "tags": ["hof", "allstar5", "3000hits", "batting_title", "gold_glove", "200hits_season"]},
    {"name": "Ozzie Smith", "teams": ["San Diego Padres", "St. Louis Cardinals"],
     "tags": ["hof", "allstar5", "ws_champ", "gold_glove"]},
    {"name": "Kirby Puckett", "teams": ["Minnesota Twins"],
     "tags": ["hof", "allstar5", "ws_champ", "batting_title", "gold_glove", "200hits_season"]},
    {"name": "Paul Molitor", "teams": ["Milwaukee Brewers", "Toronto Blue Jays", "Minnesota Twins"],
     "tags": ["hof", "allstar5", "ws_champ", "3000hits", "batting_title", "200hits_season"]},
    {"name": "Dennis Eckersley", "teams": ["Cleveland Guardians", "Boston Red Sox", "Chicago Cubs",
                                             "Athletics", "St. Louis Cardinals"],
     "tags": ["hof", "mvp", "cy_young", "allstar5", "ws_champ", "20win_season", "no_hitter"]},
    {"name": "Nolan Ryan", "teams": ["New York Mets", "Los Angeles Angels", "Houston Astros", "Texas Rangers"],
     "tags": ["hof", "allstar5", "3000k", "no_hitter"]},
    {"name": "Steve Carlton", "teams": ["St. Louis Cardinals", "Philadelphia Phillies", "San Francisco Giants",
                                          "Chicago White Sox", "Cleveland Guardians", "Minnesota Twins"],
     "tags": ["hof", "cy_young", "allstar5", "ws_champ", "20win_season", "3000k", "300wins"]},
    {"name": "Rich Gossage", "teams": ["Chicago White Sox", "Pittsburgh Pirates", "New York Yankees",
                                         "San Diego Padres", "Chicago Cubs", "San Francisco Giants",
                                         "Texas Rangers", "Athletics", "Seattle Mariners"],
     "tags": ["hof", "allstar5"]},
    {"name": "Ryne Sandberg", "teams": ["Philadelphia Phillies", "Chicago Cubs"],
     "tags": ["hof", "mvp", "allstar5", "gold_glove", "200hits_season"]},
    {"name": "Andre Dawson", "teams": ["Washington Nationals", "Chicago Cubs", "Boston Red Sox", "Miami Marlins"],
     "tags": ["hof", "mvp", "roy", "allstar5", "40hr_season", "gold_glove"]},
    {"name": "Gary Carter", "teams": ["Washington Nationals", "New York Mets", "San Francisco Giants", "Los Angeles Dodgers"],
     "tags": ["hof", "allstar5", "ws_champ", "gold_glove"]},
    {"name": "Bruce Sutter", "teams": ["Chicago Cubs", "St. Louis Cardinals", "Atlanta Braves"],
     "tags": ["hof", "cy_young", "allstar5", "ws_champ"]},
    {"name": "Ozzie Guillen", "teams": ["Chicago White Sox", "Baltimore Orioles", "Atlanta Braves", "Tampa Bay Rays"],
     "tags": ["roy", "allstar5"]},
    {"name": "Fernando Valenzuela", "teams": ["Los Angeles Dodgers", "Los Angeles Angels", "Baltimore Orioles",
                                                "Philadelphia Phillies", "San Diego Padres"],
     "tags": ["cy_young", "roy", "allstar5", "ws_champ"]},
    {"name": "George Brett", "teams": ["Kansas City Royals"],
     "tags": ["hof", "mvp", "allstar5", "ws_champ", "3000hits", "batting_title", "200hits_season"]},
    {"name": "Robin Yount", "teams": ["Milwaukee Brewers"],
     "tags": ["hof", "mvp", "allstar5", "3000hits", "gold_glove", "200hits_season"]},
    {"name": "Mike Schmidt", "teams": ["Philadelphia Phillies"],
     "tags": ["hof", "mvp", "allstar5", "ws_champ", "ws_mvp", "500hr", "40hr_season", "gold_glove"]},
    {"name": "Steve Garvey", "teams": ["Los Angeles Dodgers", "San Diego Padres"],
     "tags": ["mvp", "allstar5", "ws_champ", "gold_glove", "200hits_season"]},
    {"name": "Dave Winfield", "teams": ["San Diego Padres", "New York Yankees", "Los Angeles Angels",
                                          "Toronto Blue Jays", "Minnesota Twins", "Cleveland Guardians"],
     "tags": ["hof", "allstar5", "ws_champ", "3000hits", "gold_glove"]},
    {"name": "Dave Parker", "teams": ["Pittsburgh Pirates", "Cincinnati Reds", "Athletics",
                                        "Milwaukee Brewers", "Los Angeles Angels", "Toronto Blue Jays"],
     "tags": ["hof", "mvp", "allstar5", "ws_champ", "batting_title", "gold_glove", "200hits_season"]},
    {"name": "Willie Stargell", "teams": ["Pittsburgh Pirates"],
     "tags": ["hof", "mvp", "allstar5", "ws_champ", "ws_mvp", "40hr_season"]},
    {"name": "Reggie Jackson", "teams": ["Athletics", "Baltimore Orioles", "New York Yankees",
                                           "Los Angeles Angels"],
     "tags": ["hof", "mvp", "allstar5", "ws_champ", "ws_mvp", "500hr", "40hr_season"]},
    {"name": "Catfish Hunter", "teams": ["Athletics", "New York Yankees"],
     "tags": ["hof", "cy_young", "allstar5", "ws_champ", "20win_season"]},
    {"name": "Rollie Fingers", "teams": ["Athletics", "San Diego Padres", "Milwaukee Brewers"],
     "tags": ["hof", "mvp", "cy_young", "allstar5", "ws_champ", "ws_mvp"]},
    {"name": "Rod Carew", "teams": ["Minnesota Twins", "Los Angeles Angels"],
     "tags": ["hof", "mvp", "roy", "allstar5", "3000hits", "batting_title", "200hits_season"]},
    {"name": "Harmon Killebrew", "teams": ["Minnesota Twins", "Kansas City Royals"],
     "tags": ["hof", "mvp", "allstar5", "500hr", "40hr_season"]},
    {"name": "Bert Blyleven", "teams": ["Minnesota Twins", "Texas Rangers", "Pittsburgh Pirates",
                                          "Cleveland Guardians", "Los Angeles Angels"],
     "tags": ["hof", "allstar5", "ws_champ", "20win_season", "3000k", "no_hitter"]},
    {"name": "Kirk Gibson", "teams": ["Detroit Tigers", "Los Angeles Dodgers", "Pittsburgh Pirates",
                                        "Kansas City Royals"],
     "tags": ["mvp", "ws_champ"]},
    {"name": "Alan Trammell", "teams": ["Detroit Tigers"], "tags": ["hof", "allstar5", "ws_champ", "gold_glove"]},
    {"name": "Jack Morris", "teams": ["Detroit Tigers", "Minnesota Twins", "Toronto Blue Jays", "Cleveland Guardians"],
     "tags": ["hof", "allstar5", "ws_champ", "ws_mvp", "20win_season"]},
    {"name": "Al Kaline", "teams": ["Detroit Tigers"], "tags": ["hof", "allstar5", "ws_champ", "batting_title", "gold_glove", "3000hits"]},
    {"name": "Bob Feller", "teams": ["Cleveland Guardians"], "tags": ["hof", "allstar5", "ws_champ", "20win_season", "no_hitter", "3000k"]},
    {"name": "Jim Palmer", "teams": ["Baltimore Orioles"], "tags": ["hof", "cy_young", "allstar5", "ws_champ", "20win_season"]},
    {"name": "Brooks Robinson", "teams": ["Baltimore Orioles"], "tags": ["hof", "mvp", "allstar5", "ws_champ", "ws_mvp", "gold_glove"]},
    {"name": "Frank Robinson", "teams": ["Cincinnati Reds", "Baltimore Orioles", "Los Angeles Dodgers",
                                           "Los Angeles Angels", "Cleveland Guardians"],
     "tags": ["hof", "mvp", "roy", "allstar5", "ws_champ", "ws_mvp", "triple_crown", "500hr", "40hr_season", "batting_title"]},
    {"name": "Willie Mays", "teams": ["San Francisco Giants", "New York Mets"],
     "tags": ["hof", "mvp", "roy", "allstar5", "ws_champ", "500hr", "40hr_season", "50hr_season", "gold_glove", "batting_title", "40sb_season"]},
    {"name": "Willie McCovey", "teams": ["San Francisco Giants", "San Diego Padres", "Athletics"],
     "tags": ["hof", "mvp", "roy", "allstar5", "500hr", "40hr_season"]},
    {"name": "Juan Marichal", "teams": ["San Francisco Giants", "Boston Red Sox", "Los Angeles Dodgers"],
     "tags": ["hof", "allstar5", "20win_season", "no_hitter"]},
    {"name": "Orlando Cepeda", "teams": ["San Francisco Giants", "St. Louis Cardinals", "Atlanta Braves",
                                           "Athletics", "Boston Red Sox", "Kansas City Royals"],
     "tags": ["hof", "mvp", "roy", "allstar5", "batting_title", "40hr_season"]},
    {"name": "Barry Bonds", "teams": ["Pittsburgh Pirates", "San Francisco Giants"],
     "tags": ["mvp", "allstar5", "500hr", "40hr_season", "50hr_season", "gold_glove", "batting_title", "40sb_season"]},
    {"name": "Sandy Koufax", "teams": ["Los Angeles Dodgers"],
     "tags": ["hof", "mvp", "cy_young", "allstar5", "ws_champ", "ws_mvp", "20win_season", "no_hitter", "3000k"]},
    {"name": "Don Drysdale", "teams": ["Los Angeles Dodgers"], "tags": ["hof", "cy_young", "allstar5", "ws_champ", "20win_season", "no_hitter", "3000k"]},
    {"name": "Duke Snider", "teams": ["Los Angeles Dodgers", "New York Mets", "San Francisco Giants"],
     "tags": ["hof", "allstar5", "ws_champ", "40hr_season"]},
    {"name": "Pee Wee Reese", "teams": ["Los Angeles Dodgers"], "tags": ["hof", "allstar5", "ws_champ"]},
    {"name": "Jackie Robinson", "teams": ["Los Angeles Dodgers"], "tags": ["hof", "mvp", "roy", "allstar5", "ws_champ", "batting_title", "40sb_season"]},
    {"name": "Roy Campanella", "teams": ["Los Angeles Dodgers"], "tags": ["hof", "mvp", "allstar5", "ws_champ"]},
    {"name": "Hank Aaron", "teams": ["Atlanta Braves", "Milwaukee Brewers"],
     "tags": ["hof", "mvp", "allstar5", "ws_champ", "500hr", "3000hits", "40hr_season", "batting_title", "gold_glove"]},
    {"name": "Eddie Mathews", "teams": ["Atlanta Braves", "Houston Astros", "Detroit Tigers"],
     "tags": ["hof", "allstar5", "ws_champ", "500hr", "40hr_season"]},
    {"name": "Warren Spahn", "teams": ["Atlanta Braves", "New York Mets", "San Francisco Giants"],
     "tags": ["hof", "cy_young", "allstar5", "ws_champ", "20win_season", "no_hitter", "300wins"]},
    {"name": "Phil Niekro", "teams": ["Atlanta Braves", "New York Yankees", "Cleveland Guardians", "Toronto Blue Jays"],
     "tags": ["hof", "allstar5", "20win_season", "3000k", "300wins"]},
    {"name": "Ernie Banks", "teams": ["Chicago Cubs"], "tags": ["hof", "mvp", "allstar5", "500hr", "40hr_season", "gold_glove"]},
    {"name": "Billy Williams", "teams": ["Chicago Cubs", "Athletics"],
     "tags": ["hof", "roy", "allstar5", "batting_title", "40hr_season", "200hits_season"]},
    {"name": "Ferguson Jenkins", "teams": ["Chicago Cubs", "Texas Rangers", "Boston Red Sox"],
     "tags": ["hof", "cy_young", "allstar5", "20win_season", "3000k", "300wins"]},
    {"name": "Lou Brock", "teams": ["Chicago Cubs", "St. Louis Cardinals"],
     "tags": ["hof", "allstar5", "ws_champ", "3000hits", "40sb_season"]},
    {"name": "Bob Gibson", "teams": ["St. Louis Cardinals"], "tags": ["hof", "mvp", "cy_young", "allstar5", "ws_champ", "ws_mvp", "20win_season", "no_hitter", "3000k"]},
    {"name": "Stan Musial", "teams": ["St. Louis Cardinals"], "tags": ["hof", "mvp", "allstar5", "ws_champ", "3000hits", "batting_title", "200hits_season"]},
    {"name": "Ted Williams", "teams": ["Boston Red Sox"], "tags": ["hof", "mvp", "allstar5", "triple_crown", "batting_title", "40hr_season"]},
    {"name": "Carl Yastrzemski", "teams": ["Boston Red Sox"], "tags": ["hof", "mvp", "allstar5", "triple_crown", "3000hits", "batting_title", "gold_glove"]},
    {"name": "Jim Rice", "teams": ["Boston Red Sox"], "tags": ["hof", "mvp", "allstar5", "40hr_season", "200hits_season"]},
    {"name": "Pedro Guerrero", "teams": ["Los Angeles Dodgers", "St. Louis Cardinals"], "tags": ["allstar5", "ws_champ", "ws_mvp"]},
    {"name": "Yogi Berra", "teams": ["New York Yankees", "New York Mets"], "tags": ["hof", "mvp", "allstar5", "ws_champ"]},
    {"name": "Whitey Ford", "teams": ["New York Yankees"], "tags": ["hof", "cy_young", "allstar5", "ws_champ", "ws_mvp", "20win_season"]},
    {"name": "Mickey Mantle", "teams": ["New York Yankees"], "tags": ["hof", "mvp", "allstar5", "ws_champ", "triple_crown", "500hr", "40hr_season", "gold_glove"]},
    {"name": "Joe DiMaggio", "teams": ["New York Yankees"], "tags": ["hof", "mvp", "allstar5", "ws_champ", "batting_title"]},
    {"name": "Lou Gehrig", "teams": ["New York Yankees"], "tags": ["hof", "mvp", "ws_champ", "triple_crown", "batting_title", "200hits_season", "40hr_season"]},
    {"name": "Babe Ruth", "teams": ["Boston Red Sox", "New York Yankees", "Atlanta Braves"],
     "tags": ["hof", "mvp", "ws_champ", "triple_crown", "500hr", "40hr_season", "batting_title"]},
    {"name": "Roy Halladay", "teams": ["Toronto Blue Jays", "Philadelphia Phillies"],
     "tags": ["hof", "cy_young", "allstar5", "20win_season", "no_hitter"]},
    {"name": "Curt Schilling", "teams": ["Baltimore Orioles", "Houston Astros", "Philadelphia Phillies",
                                           "Arizona Diamondbacks", "Boston Red Sox"],
     "tags": ["allstar5", "ws_champ", "ws_mvp", "20win_season", "3000k"]},
    {"name": "Larry Walker", "teams": ["Washington Nationals", "Colorado Rockies", "St. Louis Cardinals"],
     "tags": ["hof", "mvp", "allstar5", "batting_title", "gold_glove", "40hr_season"]},
    {"name": "Todd Helton", "teams": ["Colorado Rockies"], "tags": ["hof", "allstar5", "batting_title", "gold_glove", "200hits_season"]},
    {"name": "Scott Rolen", "teams": ["Philadelphia Phillies", "St. Louis Cardinals", "Toronto Blue Jays",
                                        "Cincinnati Reds"],
     "tags": ["hof", "roy", "allstar5", "ws_champ", "gold_glove"]},
]

for _p in GRID_PLAYERS:
    for _t in _p["tags"]:
        assert _t in GRID_ACHIEVEMENTS, f"GRID_PLAYERS entry {_p['name']!r} has unknown tag {_t!r}"
assert len({_p["name"] for _p in GRID_PLAYERS}) == len(GRID_PLAYERS), "duplicate name in GRID_PLAYERS"


def _grid_player_cats(player):
    """A player's full category-key set: 'team:<Full Team Name>' for every
    team they played for, plus 'achv:<key>' for every achievement tag."""
    return [f"team:{t}" for t in player["teams"]] + [f"achv:{k}" for k in player["tags"]]


def _grid_all_categories():
    """Every category key in play: one per achievement, plus one per team
    that actually appears in GRID_PLAYERS (not just the full 30, so a
    category is never picked with zero possible players to begin with)."""
    teams = sorted({t for p in GRID_PLAYERS for t in p["teams"]})
    cats = [f"achv:{k}" for k in GRID_ACHIEVEMENTS] + [f"team:{t}" for t in teams]
    return cats


def _grid_category_label(cat):
    kind, _, key = cat.partition(":")
    return GRID_ACHIEVEMENTS[key] if kind == "achv" else key


def _grid_pair_counts(categories):
    """Precomputes, for every pair of categories, how many curated players
    satisfy both - so daily grid selection can guarantee every one of the 9
    cells has at least one valid answer before it's shown to a visitor."""
    cats_for_player = [set(_grid_player_cats(p)) for p in GRID_PLAYERS]
    counts = {}
    for a, b in itertools.combinations(categories, 2):
        n = sum(1 for cats in cats_for_player if a in cats and b in cats)
        counts[(a, b)] = n
        counts[(b, a)] = n
    return counts


def _grid_full_assignment_exists(rows, cols):
    """Checks that all 9 cells can be filled SIMULTANEOUSLY with 9 distinct
    players - a stronger guarantee than every cell merely having >=1 answer
    in isolation, since two cells can each have an answer that turns out to
    be the same one or only player available, leaving no valid player for
    whichever cell is filled last. Backtracks cell-by-cell (9 cells, small
    per-cell candidate pools) trying every candidate before declaring a
    grid unsolvable."""
    candidates_by_cell = [
        [p["name"] for p in GRID_PLAYERS if r in _grid_player_cats(p) and c in _grid_player_cats(p)]
        for r in rows for c in cols
    ]
    if any(not names for names in candidates_by_cell):
        return False

    used = set()

    def backtrack(cell_index):
        if cell_index == len(candidates_by_cell):
            return True
        for name in candidates_by_cell[cell_index]:
            if name in used:
                continue
            used.add(name)
            if backtrack(cell_index + 1):
                return True
            used.discard(name)
        return False

    return backtrack(0)


def _select_daily_grid(day_index, categories, pair_counts):
    """Deterministically picks 3 row categories + 3 column categories for
    a given day, guaranteeing the whole 3x3 grid can be filled with 9
    distinct players - searches a day-seeded shuffle of the category pool
    for a working 3-and-3 split, same seeded-deterministic approach as the
    word game's daily pick. Checks the cheap pairwise-count filter first,
    then confirms a real full assignment exists before accepting."""
    rng = random.Random(day_index)
    pool = categories[:]
    for _attempt in range(400):
        rng.shuffle(pool)
        candidates = pool[:8]
        for rows in itertools.combinations(candidates, 3):
            remaining = [c for c in candidates if c not in rows]
            for cols in itertools.combinations(remaining, 3):
                if not all(pair_counts.get((r, c), 0) >= 1 for r in rows for c in cols):
                    continue
                if _grid_full_assignment_exists(rows, cols):
                    return list(rows), list(cols)
    # Dataset is validated dense enough that this shouldn't trigger, but
    # fall back to the first 6 categories rather than crash a site build.
    return categories[:3], categories[3:6]


def build_grid_archive(today):
    """Every day's row/column category picks from GRID_GAME_EPOCH through
    today, shipped client-side so Prev/Next can replay past grids without a
    server round-trip."""
    categories = _grid_all_categories()
    pair_counts = _grid_pair_counts(categories)
    num_days = (today - GRID_GAME_EPOCH).days + 1
    archive = []
    for i in range(num_days):
        day = GRID_GAME_EPOCH + datetime.timedelta(days=i)
        rows, cols = _select_daily_grid(i, categories, pair_counts)
        archive.append({"date_key": day.isoformat(), "rows": rows, "cols": cols})
    return archive


GRID_GAME_JS = """

const gridData = JSON.parse(document.getElementById('grid-data').textContent);

const gridEl = document.getElementById('grid');
const dateEl = document.getElementById('grid-date');
const prevBtn = document.getElementById('prev-day');
const nextBtn = document.getElementById('next-day');
const revealBtn = document.getElementById('reveal-btn');
const statusEl = document.getElementById('grid-status');

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function formatDateKey(dateKey) {
  const [y, m, d] = dateKey.split('-').map(Number);
  return MONTH_NAMES[m - 1] + ' ' + d + ', ' + y;
}

let currentIndex = gridData.todayIndex;
let ROWS = [];
let COLS = [];
let STORAGE_KEY = '';
let cellState = {};

function loadState() {
  cellState = {};
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (raw && typeof raw === 'object') cellState = raw;
  } catch (e) {}
}

function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cellState));
  } catch (e) {}
}

function usedNames() {
  return Object.values(cellState).filter((c) => c && c.solved).map((c) => c.name.toLowerCase());
}

function findPlayer(rawName) {
  const name = rawName.trim().toLowerCase();
  if (!name) return null;
  return gridData.players.find((p) => p.name.toLowerCase() === name) || null;
}

function cellId(r, c) { return r + '-' + c; }

function buildGrid() {
  gridEl.innerHTML = '';
  gridEl.style.setProperty('--size', 4);

  const corner = document.createElement('div');
  corner.className = 'grid-cell grid-corner';
  gridEl.appendChild(corner);

  COLS.forEach((col) => {
    const head = document.createElement('div');
    head.className = 'grid-cell grid-head';
    head.textContent = gridData.categoryLabels[col];
    gridEl.appendChild(head);
  });

  ROWS.forEach((row, r) => {
    const head = document.createElement('div');
    head.className = 'grid-cell grid-head';
    head.textContent = gridData.categoryLabels[row];
    gridEl.appendChild(head);

    COLS.forEach((col, c) => {
      const cell = document.createElement('div');
      cell.className = 'grid-cell grid-answer';
      cell.id = 'cell-' + cellId(r, c);
      gridEl.appendChild(cell);
      renderCell(r, c);
    });
  });
}

function renderCell(r, c) {
  const cell = document.getElementById('cell-' + cellId(r, c));
  const key = cellId(r, c);
  const state = cellState[key];
  cell.innerHTML = '';

  if (state && state.solved) {
    cell.classList.add('solved');
    const nameEl = document.createElement('span');
    nameEl.className = 'grid-answer-name';
    nameEl.textContent = state.name;
    cell.appendChild(nameEl);
    const poolEl = document.createElement('span');
    poolEl.className = 'grid-answer-pool';
    const tries = state.tries || 1;
    poolEl.textContent = tries === 1 ? 'First try' : tries + ' tries';
    cell.appendChild(poolEl);
    return;
  }

  cell.classList.remove('solved');
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'grid-input';
  input.placeholder = 'Player name';
  input.autocomplete = 'off';
  input.value = (state && state.draft) || '';
  input.addEventListener('input', () => {
    showSuggestions(input, r, c);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      submitCell(r, c, input.value);
    }
  });
  input.addEventListener('blur', () => {
    setTimeout(() => hideSuggestions(r, c), 150);
  });
  cell.appendChild(input);

  const suggestBox = document.createElement('div');
  suggestBox.className = 'grid-suggest';
  suggestBox.id = 'suggest-' + key;
  suggestBox.hidden = true;
  cell.appendChild(suggestBox);
}

function showSuggestions(input, r, c) {
  const key = cellId(r, c);
  const box = document.getElementById('suggest-' + key);
  const q = input.value.trim().toLowerCase();
  if (q.length < 2) { box.hidden = true; return; }
  const used = usedNames();
  const matches = gridData.players
    .filter((p) => p.name.toLowerCase().includes(q) && !used.includes(p.name.toLowerCase()))
    .slice(0, 6);
  if (!matches.length) { box.hidden = true; return; }
  box.innerHTML = '';
  matches.forEach((p) => {
    const item = document.createElement('div');
    item.className = 'grid-suggest-item';
    item.textContent = p.name;
    item.addEventListener('mousedown', (e) => {
      e.preventDefault();
      submitCell(r, c, p.name);
    });
    box.appendChild(item);
  });
  box.hidden = false;
}

function hideSuggestions(r, c) {
  const box = document.getElementById('suggest-' + cellId(r, c));
  if (box) box.hidden = true;
}

function flashInvalid(r, c, msg) {
  const cell = document.getElementById('cell-' + cellId(r, c));
  cell.classList.add('shake');
  setTimeout(() => cell.classList.remove('shake'), 400);
  statusEl.textContent = msg;
  statusEl.classList.add('status-error');
  setTimeout(() => statusEl.classList.remove('status-error'), 1200);
}

function submitCell(r, c, rawName) {
  const key = cellId(r, c);
  if (!rawName || !rawName.trim()) return;
  const player = findPlayer(rawName);
  if (!player) {
    flashInvalid(r, c, 'Not in our player database - check spelling');
    return;
  }
  if (usedNames().includes(player.name.toLowerCase())) {
    flashInvalid(r, c, player.name + ' is already used elsewhere in this grid');
    return;
  }
  const rowCat = ROWS[r];
  const colCat = COLS[c];
  if (player.cats.includes(rowCat) && player.cats.includes(colCat)) {
    const prevTries = (cellState[key] && cellState[key].tries) || 0;
    cellState[key] = { solved: true, name: player.name, tries: prevTries + 1 };
    saveState();
    renderCell(r, c);
    statusEl.textContent = '';
    checkCompletion();
  } else {
    cellState[key] = { solved: false, draft: '', tries: ((cellState[key] && cellState[key].tries) || 0) + 1 };
    saveState();
    flashInvalid(r, c, player.name + " doesn't fit both categories");
    const input = document.querySelector('#cell-' + key + ' .grid-input');
    if (input) input.value = '';
  }
}

function checkCompletion() {
  const total = ROWS.length * COLS.length;
  const solved = Object.values(cellState).filter((s) => s && s.solved).length;
  if (solved === total) {
    const totalTries = Object.values(cellState).reduce((sum, s) => sum + (s.tries || 1), 0);
    const perfect = totalTries === total;
    statusEl.textContent = perfect
      ? 'Immaculate! All 9 solved on the first try.'
      : 'Grid complete! ' + totalTries + ' total guesses across 9 squares.';
    statusEl.classList.add('status-success');
  }
}

function findFullAssignment(openCells, alreadyUsed) {
  // Backtracks over just the still-open cells so "Reveal" can't paint
  // itself into a corner the way a naive greedy fill can: two open cells
  // can each have exactly one candidate, and if it's the same player for
  // both, filling the first one greedily leaves the second unsolvable even
  // though a full assignment (using a different, non-greedy pick) exists.
  const candidatesByCell = openCells.map(({ rowCat, colCat }) =>
    gridData.players
      .map((p) => p.name)
      .filter((name) => !alreadyUsed.has(name.toLowerCase()))
      .filter((name) => {
        const p = gridData.players.find((pl) => pl.name === name);
        return p.cats.includes(rowCat) && p.cats.includes(colCat);
      })
  );
  const assignment = new Array(openCells.length).fill(null);
  const used = new Set();
  function backtrack(i) {
    if (i === openCells.length) return true;
    for (const name of candidatesByCell[i]) {
      const key = name.toLowerCase();
      if (used.has(key)) continue;
      used.add(key);
      assignment[i] = name;
      if (backtrack(i + 1)) return true;
      used.delete(key);
    }
    return false;
  }
  return backtrack(0) ? assignment : null;
}

revealBtn.addEventListener('click', () => {
  const openCells = [];
  for (let r = 0; r < ROWS.length; r++) {
    for (let c = 0; c < COLS.length; c++) {
      const key = cellId(r, c);
      if (cellState[key] && cellState[key].solved) continue;
      openCells.push({ r, c, key, rowCat: ROWS[r], colCat: COLS[c] });
    }
  }
  const alreadyUsed = new Set(usedNames());
  const assignment = findFullAssignment(openCells, alreadyUsed);
  openCells.forEach((cell, i) => {
    const name = assignment ? assignment[i] : null;
    if (!name) return;
    cellState[cell.key] = { solved: true, name, tries: (cellState[cell.key] && cellState[cell.key].tries) || 1, revealed: true };
    renderCell(cell.r, cell.c);
  });
  saveState();
  statusEl.textContent = 'Answers revealed.';
  checkCompletion();
});

function updateNavButtons() {
  prevBtn.disabled = currentIndex <= 0;
  nextBtn.disabled = currentIndex >= gridData.todayIndex;
}

function loadDay(index) {
  index = Math.max(0, Math.min(gridData.todayIndex, index));
  currentIndex = index;
  const entry = gridData.archive[index];
  ROWS = entry.rows;
  COLS = entry.cols;
  STORAGE_KEY = 'diamond-grid-' + entry.date_key;
  statusEl.textContent = '';
  statusEl.classList.remove('status-success', 'status-error');

  dateEl.textContent = formatDateKey(entry.date_key) + (index === gridData.todayIndex ? ' (Today)' : '');

  loadState();
  buildGrid();
  checkCompletion();
  updateNavButtons();
}

prevBtn.addEventListener('click', () => { loadDay(currentIndex - 1); prevBtn.blur(); });
nextBtn.addEventListener('click', () => { loadDay(currentIndex + 1); nextBtn.blur(); });

loadDay(currentIndex);
"""

GRID_GAME_CSS = """

:root { color-scheme: dark; }
body {
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.site-header {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}
.brand { display: flex; align-items: center; gap: 8px; min-width: 0; }
.site-header h1 { font-size: 1.15rem; margin: 0; white-space: nowrap; }
.site-header .date { color: #999; font-size: 0.9rem; white-space: nowrap; }
.header-link {
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}
.header-link:hover { border-color: #ff5266; color: #ff5266; }

.disclaimer {
  max-width: 620px; margin: 1.25rem auto 0; padding: 0.75rem 1rem;
  color: #aaa; font-size: 0.85rem; line-height: 1.5;
  background: rgba(255,82,102,0.06); border-left: 3px solid #ff5266; border-radius: 0 8px 8px 0;
}

.grid-main {
  max-width: 620px; margin: 0 auto; padding: 1rem 1.25rem 2rem;
  display: flex; flex-direction: column; align-items: center; gap: 1rem;
}
.grid-nav { display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 0.5rem; }
.grid-nav-info { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 2px; flex: 1; min-width: 0; }
.grid-date { font-size: 0.85rem; font-weight: 700; color: #f2f2f2; white-space: nowrap; }
.nav-btn {
  background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.4rem 0.8rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
  cursor: pointer; white-space: nowrap; flex-shrink: 0;
  transition: border-color 0.15s ease, color 0.15s ease, opacity 0.15s ease;
}
.nav-btn:hover:not(:disabled) { border-color: #ff5266; color: #ff5266; }
.nav-btn:disabled { opacity: 0.35; cursor: not-allowed; }

.grid {
  display: grid;
  grid-template-columns: minmax(80px, 0.9fr) repeat(3, 1fr);
  gap: 4px;
  width: 100%;
}
.grid-cell {
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  text-align: center; padding: 6px; min-height: 78px;
  position: relative;
}
.grid-corner { background: transparent; }
.grid-head {
  background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  font-size: 0.72rem; font-weight: 700; line-height: 1.25;
}
.grid-answer { background: #14141a; border: 1px solid #262626; flex-direction: column; gap: 4px; }
.grid-answer.solved { background: rgba(74, 222, 128, 0.12); border-color: #4ade80; }
.grid-answer.shake { animation: grid-shake 0.4s; border-color: #ff5266; }
@keyframes grid-shake {
  0%, 100% { transform: translateX(0); }
  20%, 60% { transform: translateX(-4px); }
  40%, 80% { transform: translateX(4px); }
}
.grid-input {
  width: 100%; box-sizing: border-box; background: #0d0d0d; border: 1px solid #333; color: #f2f2f2;
  border-radius: 6px; padding: 6px 4px; font-size: 0.7rem; text-align: center;
}
.grid-input:focus { outline: none; border-color: #ff5266; }
.grid-answer-name { font-size: 0.72rem; font-weight: 700; color: #4ade80; line-height: 1.2; }
.grid-answer-pool { font-size: 0.62rem; color: #999; }
.grid-suggest {
  position: absolute; top: 100%; left: 0; right: 0; z-index: 20; margin-top: 2px;
  background: #1a1a1a; border: 1px solid #333; border-radius: 8px; overflow: hidden;
  box-shadow: 0 6px 16px rgba(0,0,0,0.5);
}
.grid-suggest-item {
  padding: 0.45rem 0.6rem; font-size: 0.75rem; text-align: left; cursor: pointer; color: #f2f2f2;
}
.grid-suggest-item:hover { background: #2a2a2a; }

.grid-status { min-height: 1.4rem; font-size: 0.85rem; font-weight: 600; margin: 0; text-align: center; color: #999; }
.grid-status.status-error { color: #ff5266; }
.grid-status.status-success { color: #4ade80; }

.grid-actions { display: flex; justify-content: center; }
.reveal-btn {
  background: #1a1a1a; border: 1px solid #333; color: #ccc;
  padding: 0.45rem 1rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
  cursor: pointer; transition: all 0.15s ease;
}
.reveal-btn:hover { border-color: #ff5266; color: #ff5266; }

@media (max-width: 480px) {
  .grid-cell { min-height: 66px; padding: 4px; }
  .grid-head { font-size: 0.62rem; }
  .grid-input { font-size: 0.62rem; padding: 4px 2px; }
}
"""


GRID_GAME_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Diamond Grid - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Diamond Grid: a daily baseball trivia game - name a player who fits both the team and the achievement.">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>Diamond Grid</h1>
  </div>
  <span class="date">{date_label}</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<p class="disclaimer">
  Every row and column is a team or career achievement - name one player who fits both, without
  repeating a name across the grid. Type a player and hit Enter, or pick from the suggestions.
  No hard limit on guesses per square, so argue it out. Use Prev/Next to replay past grids.
</p>
<main class="grid-main">
  <div class="grid-nav">
    <button type="button" id="prev-day" class="nav-btn">&larr; Prev</button>
    <div class="grid-nav-info">
      <span id="grid-date" class="grid-date"></span>
    </div>
    <button type="button" id="next-day" class="nav-btn">Next &rarr;</button>
  </div>
  <div id="grid" class="grid"></div>
  <p id="grid-status" class="grid-status" aria-live="polite"></p>
  <div class="grid-actions">
    <button type="button" id="reveal-btn" class="reveal-btn">Reveal remaining answers</button>
  </div>
</main>

<script id="grid-data" type="application/json">{grid_data_json}</script>
<script>{game_js}</script>

<style>{game_css}</style>
</body>
</html>
"""


def build_grid_game_page(today):
    archive = build_grid_archive(today)
    categories = _grid_all_categories()
    category_labels = {c: _grid_category_label(c) for c in categories}
    players_json = [{"name": p["name"], "cats": _grid_player_cats(p)} for p in GRID_PLAYERS]
    grid_data_json = json.dumps({
        "players": players_json,
        "categoryLabels": category_labels,
        "archive": archive,
        "todayIndex": len(archive) - 1,
    }).replace("</", "<\\/")
    return GRID_GAME_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        date_label=today.strftime("%A, %B %-d, %Y"),
        grid_data_json=grid_data_json,
        game_js=GRID_GAME_JS,
        game_css=GRID_GAME_CSS,
    )


def _grid_badge_svg():
    """The Diamond Grid app-icon badge: a rounded-square tile with a
    plus-shaped 3x3 grid inside (green edge cells, cream center cell
    marked with a red baseball-seam stitch, dark corners blending into
    the background) - matches the game's logo art."""
    return """<svg class="game-art grid-art" viewBox="0 0 220 220" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Diamond Grid">
  <rect x="4" y="4" width="212" height="212" rx="42" fill="#0f1626"/>
  <rect x="86" y="28" width="48" height="48" rx="12" fill="#4ade80"/>
  <rect x="28" y="86" width="48" height="48" rx="12" fill="#4ade80"/>
  <rect x="86" y="86" width="48" height="48" rx="12" fill="#f5efe0"/>
  <rect x="144" y="86" width="48" height="48" rx="12" fill="#4ade80"/>
  <rect x="86" y="144" width="48" height="48" rx="12" fill="#4ade80"/>
  <path d="M 106 94 Q 96 110 106 126" fill="none" stroke="#ff5266" stroke-width="3.5" stroke-linecap="round"/>
  <path d="M 114 94 Q 124 110 114 126" fill="none" stroke="#ff5266" stroke-width="3.5" stroke-linecap="round"/>
</svg>"""


WHOAMI_FILE = "guess-the-legend.html"
WHOAMI_EPOCH = datetime.date(2026, 8, 17)  # same "day one" as Bordle

# Clues ordered obscure -> obvious (index 0 = hardest, last = giveaway).
# Only well-documented, widely-cited public facts about each Hall of Famer.
HOF_PLAYERS = [
    {"name": "Babe Ruth", "clues": [
        "Grew up at a Baltimore reform school, St. Mary's Industrial School for Boys, where he learned to play ball under Brother Matthias.",
        "Broke into the majors as a pitcher for the Boston Red Sox in 1914.",
        "Sold to the New York Yankees in 1919, in a deal fans later blamed for decades of Red Sox misfortune.",
        "Hit 60 home runs in a single season in 1927, a record that stood for 34 years.",
        "Nicknamed 'The Sultan of Swat,' finished with 714 career home runs and is considered the greatest slugger in baseball history.",
    ]},
    {"name": "Lou Gehrig", "clues": [
        "Played football and baseball at Columbia University before turning pro.",
        "Debuted with the New York Yankees in 1923, playing first base for his entire career.",
        "Nicknamed 'The Iron Horse' for his remarkable durability.",
        "Played in 2,130 consecutive games, a record that stood until 1995.",
        "Diagnosed with ALS in 1939 and gave the famous 'Luckiest Man' farewell speech; the disease is still often called by his name today.",
    ]},
    {"name": "Jackie Robinson", "clues": [
        "Was a four-sport star at UCLA - football, basketball, track, and baseball.",
        "Played for the Kansas City Monarchs of the Negro Leagues before signing with Brooklyn.",
        "Wore uniform number 42, later retired across every team in the league.",
        "Won the first-ever Rookie of the Year award in 1947 and the NL MVP in 1949.",
        "Broke Major League Baseball's color barrier in 1947 with the Brooklyn Dodgers.",
    ]},
    {"name": "Willie Mays", "clues": [
        "Began his pro career as a teenager with the Birmingham Black Barons of the Negro American League.",
        "Debuted with the New York Giants in 1951.",
        "Made an over-the-shoulder catch in the 1954 World Series remembered simply as 'The Catch.'",
        "Won 12 Gold Gloves and two NL MVP awards.",
        "Nicknamed the 'Say Hey Kid,' finished with 660 home runs and is regarded as one of the greatest all-around players ever.",
    ]},
    {"name": "Hank Aaron", "clues": [
        "Started his pro career batting cross-handed for the Indianapolis Clowns of the Negro American League.",
        "Debuted with the Milwaukee Braves in 1954.",
        "Won the 1957 NL MVP and helped the Braves win the World Series that year.",
        "Received death threats through the early 1970s while chasing baseball's most famous career record.",
        "Broke Babe Ruth's career home run record with his 715th homer in 1974; nicknamed 'Hammerin' Hank.'",
    ]},
    {"name": "Mickey Mantle", "clues": [
        "Nearly had his career derailed as a teenager by osteomyelitis, a bone infection.",
        "Debuted with the New York Yankees in 1951, taking over center field from an aging Joe DiMaggio.",
        "Nicknamed 'The Commerce Comet,' after his Oklahoma hometown and blazing speed.",
        "Won the Triple Crown in 1956 and three AL MVP awards overall.",
        "Switch-hitting Yankees legend who hit 536 career home runs and played in 12 World Series.",
    ]},
    {"name": "Ted Williams", "clues": [
        "Served as a Marine Corps fighter pilot, missing nearly five seasons to military service in WWII and Korea.",
        "Debuted with the Boston Red Sox in 1939.",
        "Nicknamed 'The Splendid Splinter' and 'Teddy Ballgame.'",
        "Won two Triple Crowns and two AL MVP awards.",
        "The last player to hit .400 in a season, batting .406 in 1941.",
    ]},
    {"name": "Joe DiMaggio", "clues": [
        "Set a Pacific Coast League hitting-streak record for the minor-league San Francisco Seals before turning pro.",
        "Debuted with the New York Yankees in 1936.",
        "Nicknamed 'Joltin' Joe' and 'The Yankee Clipper.'",
        "Won three AL MVP awards and nine World Series titles.",
        "Hit safely in 56 consecutive games in 1941, a record that still stands.",
    ]},
    {"name": "Sandy Koufax", "clues": [
        "Played college basketball at the University of Cincinnati before signing with the Dodgers.",
        "Debuted with the Brooklyn Dodgers in 1955 and struggled with his control early on.",
        "Threw four career no-hitters, including a perfect game in 1965.",
        "Won three Cy Young awards and two World Series MVP awards.",
        "Retired at age 30 due to arthritis in his pitching elbow, at the absolute peak of his dominance.",
    ]},
    {"name": "Nolan Ryan", "clues": [
        "Drafted in the 12th round of the 1965 MLB Draft by the New York Mets.",
        "Debuted with the Mets in 1966, before being traded to the California Angels in 1972.",
        "Threw a record seven career no-hitters.",
        "Led his league in strikeouts 11 times over his career.",
        "MLB's all-time strikeout leader with 5,714, nicknamed 'The Ryan Express.'",
    ]},
    {"name": "Cal Ripken Jr.", "clues": [
        "Drafted in the 2nd round of the 1978 MLB Draft by the Baltimore Orioles, the team his father managed.",
        "Debuted with the Orioles in 1981 and spent his entire 21-year career there.",
        "Won the 1982 AL Rookie of the Year award.",
        "Won two AL MVP awards while playing shortstop, a position rarely known for power.",
        "Broke Lou Gehrig's consecutive-games streak, finishing with 2,632 straight games played, nicknamed 'The Iron Man.'",
    ]},
    {"name": "Derek Jeter", "clues": [
        "Drafted 6th overall in the 1992 MLB Draft by the New York Yankees.",
        "Debuted with the Yankees in 1995 and spent his entire 20-year career there.",
        "Nicknamed 'Mr. November' after a walk-off homer in the first World Series game ever played in that month.",
        "Won five World Series titles and five Gold Gloves as Yankees captain.",
        "Retired with 3,465 career hits, the Yankees' all-time hits leader and a first-ballot Hall of Famer.",
    ]},
    {"name": "Mariano Rivera", "clues": [
        "Signed by the Yankees as an amateur free agent out of Panama in 1990, originally as a starting pitcher.",
        "Debuted with the Yankees in 1995 and spent his entire 19-year career there.",
        "Built his dominance almost entirely around a single pitch, the cut fastball.",
        "Won five World Series titles as the Yankees' closer.",
        "MLB's all-time saves leader with 652, and the first player ever elected to the Hall of Fame unanimously.",
    ]},
    {"name": "Ken Griffey Jr.", "clues": [
        "Drafted 1st overall in the 1987 MLB Draft by the Seattle Mariners; his father was also an MLB player.",
        "Debuted with the Mariners in 1989 at age 19.",
        "Nicknamed 'The Kid' for his youthful smile and famous backwards cap.",
        "Won 10 Gold Gloves and the 1997 AL MVP award.",
        "Hit 630 career home runs and was elected to the Hall of Fame with the highest vote percentage in history at the time.",
    ]},
    {"name": "Randy Johnson", "clues": [
        "Drafted in the 2nd round of the 1985 MLB Draft by the Montreal Expos.",
        "Debuted with the Expos in 1988 before being traded to the Seattle Mariners.",
        "Nicknamed 'The Big Unit' for his towering 6-foot-10 frame.",
        "Won five Cy Young awards and threw a perfect game in 2004 at age 40.",
        "Struck out 4,875 career batters, second all-time, and shared World Series MVP honors with the 2001 Diamondbacks.",
    ]},
    {"name": "Greg Maddux", "clues": [
        "Drafted in the 2nd round of the 1984 MLB Draft by the Chicago Cubs.",
        "Debuted with the Cubs in 1986.",
        "Nicknamed 'The Professor' for his pinpoint control rather than velocity.",
        "Won four consecutive Cy Young awards from 1992 to 1995.",
        "Won 355 career games and 18 Gold Gloves, more than any player at any position in MLB history.",
    ]},
    {"name": "Pedro Martinez", "clues": [
        "Signed by the Los Angeles Dodgers as an amateur free agent out of the Dominican Republic in 1988.",
        "Debuted with the Dodgers in 1992 before being traded to the Montreal Expos.",
        "Posted a 1.74 ERA in 2000 with Boston, one of the most dominant seasons in modern history.",
        "Won three career Cy Young awards.",
        "Helped end Boston's 86-year championship drought with the 2004 Red Sox World Series title.",
    ]},
    {"name": "Tony Gwynn", "clues": [
        "Was also drafted by the NBA's San Diego Clippers the same year the Padres picked him.",
        "Debuted with the San Diego Padres in 1982 and spent his entire 20-year career there.",
        "Nicknamed 'Mr. Padre.'",
        "Won eight NL batting titles, tied for the most in league history.",
        "Finished with a .338 career average and 3,141 hits, regarded as one of the purest hitters ever.",
    ]},
    {"name": "Rickey Henderson", "clues": [
        "Drafted in the 4th round of the 1976 MLB Draft by the Oakland Athletics.",
        "Debuted with the Athletics in 1979.",
        "Known for his signature 'snatch' catch on routine fly balls.",
        "Won the 1990 AL MVP award.",
        "MLB's all-time leader in stolen bases (1,406) and runs scored, nicknamed 'The Man of Steal.'",
    ]},
    {"name": "Cy Young", "clues": [
        "Got his nickname, short for 'Cyclone,' after reportedly wrecking a wooden fence with his fastball at a tryout.",
        "Debuted with the Cleveland Spiders in 1890.",
        "Routinely finished what he started, throwing complete games almost every time out.",
        "Threw three career no-hitters, including a perfect game in 1904.",
        "Won 511 career games, the most in MLB history; the annual award for each league's best pitcher now bears his name.",
    ]},
    {"name": "Yogi Berra", "clues": [
        "Served in the U.S. Navy at Normandy on D-Day before his MLB career began.",
        "Debuted with the New York Yankees in 1946.",
        "Famous for offbeat sayings, or 'Yogi-isms,' like 'It ain't over till it's over.'",
        "Won three AL MVP awards as a catcher.",
        "Won a record 10 World Series championships as a player, more than anyone in MLB history.",
    ]},
    {"name": "Roberto Clemente", "clues": [
        "Signed by the Brooklyn Dodgers but left unprotected, letting the Pittsburgh Pirates grab him in a 1954 minor-league draft.",
        "Debuted with the Pirates in 1955 and spent his entire 18-year career there.",
        "Won 12 consecutive Gold Gloves patrolling right field.",
        "Won the 1966 NL MVP award and two World Series titles.",
        "Recorded exactly 3,000 career hits before dying in a plane crash on a New Year's Eve humanitarian mission; MLB's community-service award now bears his name.",
    ]},
    {"name": "Bob Gibson", "clues": [
        "Played basketball for the Harlem Globetrotters before focusing full-time on baseball.",
        "Debuted with the St. Louis Cardinals in 1959 and spent his entire 17-year career there.",
        "Won two World Series MVP awards.",
        "Posted a 1.12 ERA in 1968, one of the lowest single-season marks in modern history.",
        "Won the NL MVP and Cy Young in 1968, a season so dominant MLB lowered the pitcher's mound the following year.",
    ]},
    {"name": "Reggie Jackson", "clues": [
        "Drafted 2nd overall in the 1966 MLB Draft by the Kansas City Athletics.",
        "Debuted with the Athletics in 1967, moving with the franchise to Oakland.",
        "Nicknamed 'Mr. October' for his postseason heroics.",
        "Won the 1973 AL MVP award and five World Series titles.",
        "Hit three home runs on three consecutive pitches in Game 6 of the 1977 World Series for the Yankees.",
    ]},
    {"name": "George Brett", "clues": [
        "Drafted in the 2nd round of the 1971 MLB Draft by the Kansas City Royals.",
        "Debuted with the Royals in 1973 and spent his entire 21-year career there.",
        "At the center of the infamous 1983 'Pine Tar Incident,' when a home run was briefly disallowed.",
        "Won the 1980 AL MVP award while flirting with a .400 average most of the season.",
        "Finished with 3,154 career hits and a .305 average, one of the greatest third basemen ever.",
    ]},
    {"name": "Ozzie Smith", "clues": [
        "Drafted in the 4th round of the 1977 MLB Draft by the San Diego Padres.",
        "Debuted with the Padres in 1978 before being traded to the St. Louis Cardinals.",
        "Nicknamed 'The Wizard of Oz' for his acrobatic defense, including a signature backflip.",
        "Won 13 consecutive Gold Gloves at shortstop.",
        "Widely regarded as the greatest defensive shortstop in MLB history, elected to the Hall of Fame primarily on his glove.",
    ]},
    {"name": "Frank Robinson", "clues": [
        "Grew up playing ball in Oakland before signing with the Cincinnati Reds in 1953.",
        "Debuted with the Reds in 1956, winning NL Rookie of the Year.",
        "Traded to the Baltimore Orioles in 1966, a deal Cincinnati's own GM later called his worst.",
        "Won the Triple Crown and AL MVP in 1966 - the same year he was traded away.",
        "The only player in MLB history to win MVP in both leagues, and MLB's first Black manager, starting in 1975.",
    ]},
    {"name": "Stan Musial", "clues": [
        "Began his pro career as a pitcher before a shoulder injury forced a move to the outfield.",
        "Debuted with the St. Louis Cardinals in 1941 and spent his entire 22-year career there.",
        "Nicknamed 'Stan the Man' by opposing Brooklyn fans for his consistent hitting against them.",
        "Won seven NL batting titles and three NL MVP awards.",
        "Finished with 3,630 career hits, split exactly 1,815 at home and 1,815 on the road.",
    ]},
    {"name": "Warren Spahn", "clues": [
        "Missed nearly three seasons serving in the Army during WWII, earning a Purple Heart and Bronze Star at the Battle of the Bulge.",
        "Debuted with the Boston Braves in 1942, before his military service interrupted his career.",
        "Didn't win his first MLB game until age 25, after returning from the war.",
        "Won the 1957 NL Cy Young award, when it was still a single award for all of MLB, plus a World Series title.",
        "Won 363 career games, the most by any left-handed pitcher in MLB history.",
    ]},
    {"name": "Johnny Bench", "clues": [
        "Drafted in the 2nd round of the 1965 MLB Draft by the Cincinnati Reds.",
        "Debuted with the Reds in 1967 and spent his entire 17-year career there.",
        "Anchored the 'Big Red Machine' dynasty of the 1970s as catcher.",
        "Won two NL MVP awards and 10 Gold Gloves.",
        "Widely regarded as the greatest catcher in MLB history, revolutionizing the position with his one-handed catching style.",
    ]},
    {"name": "Rod Carew", "clues": [
        "Reportedly born aboard a train in the Panama Canal Zone.",
        "Debuted with the Minnesota Twins in 1967, winning AL Rookie of the Year.",
        "Stole home seven times in the 1969 season alone, an unusually high total.",
        "Won seven AL batting titles and the 1977 AL MVP award.",
        "Finished his career with 3,053 hits and a .328 average, one of the purest contact hitters ever.",
    ]},
    {"name": "Carl Yastrzemski", "clues": [
        "Signed by the Boston Red Sox in 1958 after the Yankees reportedly balked at his bonus demands.",
        "Debuted with the Red Sox in 1961, taking over left field from the retiring Ted Williams.",
        "Nicknamed 'Yaz' for short.",
        "Won the Triple Crown and AL MVP in 1967, powering Boston's 'Impossible Dream' pennant run.",
        "Finished with 3,419 career hits and 452 home runs, spending his entire 23-year career with the Red Sox.",
    ]},
    {"name": "Steve Carlton", "clues": [
        "Signed by the St. Louis Cardinals as an amateur free agent in 1963.",
        "Debuted with the Cardinals in 1965 before being traded to the Philadelphia Phillies for a fraction of his value.",
        "Nicknamed 'Lefty,' and famously refused to speak to reporters for most of his career.",
        "Won four Cy Young awards, a record at the time for any pitcher.",
        "Won 329 career games and struck out over 4,000 batters, anchoring Philadelphia's only 20th-century title in 1980.",
    ]},
    {"name": "Ernie Banks", "clues": [
        "Began his pro career with the Kansas City Monarchs of the Negro American League.",
        "Debuted with the Chicago Cubs in 1953 and spent his entire 19-year career there.",
        "Known for his relentlessly upbeat catchphrase, 'Let's play two!'",
        "Won back-to-back NL MVP awards in 1958 and 1959, unusual for a player on a losing team.",
        "Nicknamed 'Mr. Cub,' hit 512 career home runs but never once appeared in the postseason.",
    ]},
    {"name": "Jim Palmer", "clues": [
        "Signed by the Baltimore Orioles as an amateur free agent in 1963.",
        "Debuted with the Orioles in 1965 and spent his entire 19-year career there.",
        "Later became a longtime broadcaster and a well-known underwear model.",
        "Won three Cy Young awards and three World Series titles.",
        "Never allowed a single career grand slam in over 3,900 innings pitched, a remarkable feat for a Hall of Fame pitcher.",
    ]},
]

for _p in HOF_PLAYERS:
    assert len(_p["clues"]) == 5, f"{_p['name']!r} must have exactly 5 clues"
assert len({_p["name"] for _p in HOF_PLAYERS}) == len(HOF_PLAYERS), "duplicate name in HOF_PLAYERS"


def get_daily_hof(today):
    """Deterministic pick so every visitor sees the same Hall of Famer on a
    given date, without repeating anyone until the whole roster has cycled
    through - same seeded-reshuffle-per-cycle approach as the word game's
    daily pick. August 17, 2026 is day one, matching Bordle."""
    day_index = (today - WHOAMI_EPOCH).days
    cycle_length = len(HOF_PLAYERS)
    cycle_number = day_index // cycle_length
    position = day_index % cycle_length
    shuffled = HOF_PLAYERS[:]
    random.Random(cycle_number).shuffle(shuffled)
    return shuffled[position]


def build_whoami_archive(today):
    """Every day's Hall of Famer from WHOAMI_EPOCH through today, so the
    page can page backward/forward through past puzzles client-side."""
    num_days = (today - WHOAMI_EPOCH).days + 1
    archive = []
    for i in range(num_days):
        day = WHOAMI_EPOCH + datetime.timedelta(days=i)
        entry = get_daily_hof(day)
        archive.append({"date_key": day.isoformat(), "name": entry["name"], "clues": entry["clues"]})
    return archive


WHOAMI_JS = """

const whoamiData = JSON.parse(document.getElementById('whoami-data').textContent);

const clueList = document.getElementById('clue-list');
const guessInput = document.getElementById('guess-input');
const guessForm = document.getElementById('guess-form');
const message = document.getElementById('message');
const dateEl = document.getElementById('whoami-date');
const prevBtn = document.getElementById('prev-day');
const nextBtn = document.getElementById('next-day');
const giveUpBtn = document.getElementById('give-up-btn');
const resultEl = document.getElementById('result');
const resultText = document.getElementById('result-text');

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function formatDateKey(dateKey) {
  const [y, m, d] = dateKey.split('-').map(Number);
  return MONTH_NAMES[m - 1] + ' ' + d + ', ' + y;
}

let currentIndex = whoamiData.todayIndex;
let ANSWER = '';
let CLUES = [];
let STORAGE_KEY = '';
let cluesShown = 1;
let guesses = [];
let solved = false;
let gaveUp = false;

function loadState() {
  cluesShown = 1;
  guesses = [];
  solved = false;
  gaveUp = false;
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (raw) {
      cluesShown = raw.cluesShown || 1;
      guesses = raw.guesses || [];
      solved = !!raw.solved;
      gaveUp = !!raw.gaveUp;
    }
  } catch (e) {}
}

function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ cluesShown, guesses, solved, gaveUp }));
  } catch (e) {}
}

function normalize(s) {
  return s.trim().toLowerCase().replace(/[^a-z ]/g, '');
}

function isCorrect(guess) {
  const g = normalize(guess);
  if (!g) return false;
  const full = normalize(ANSWER);
  const last = normalize(ANSWER.split(' ').pop());
  return g === full || g === last;
}

function renderClues() {
  clueList.innerHTML = '';
  for (let i = 0; i < cluesShown; i++) {
    const li = document.createElement('li');
    li.className = 'clue-item';
    li.textContent = CLUES[i];
    clueList.appendChild(li);
  }
}

function endGame(won) {
  solved = won;
  saveState();
  resultEl.hidden = false;
  guessInput.disabled = true;
  giveUpBtn.disabled = true;
  if (won) {
    const used = cluesShown;
    resultText.textContent = 'Correct! ' + ANSWER + ' - solved with ' + used + ' clue' + (used === 1 ? '' : 's') + '.';
  } else {
    resultText.textContent = 'Out of clues - the answer was ' + ANSWER + '.';
  }
  cluesShown = CLUES.length;
  renderClues();
}

function submitGuess(rawGuess) {
  if (solved || gaveUp) return;
  const guess = rawGuess.trim();
  if (!guess) return;
  if (isCorrect(guess)) {
    guesses.push(guess);
    endGame(true);
    return;
  }
  guesses.push(guess);
  message.textContent = 'Not ' + guess + " - here's another clue.";
  if (cluesShown < CLUES.length) {
    cluesShown += 1;
    saveState();
    renderClues();
  } else {
    saveState();
    endGame(false);
  }
  guessInput.value = '';
}

guessForm.addEventListener('submit', (e) => {
  e.preventDefault();
  submitGuess(guessInput.value);
});

giveUpBtn.addEventListener('click', () => {
  gaveUp = true;
  endGame(false);
});

function updateNavButtons() {
  prevBtn.disabled = currentIndex <= 0;
  nextBtn.disabled = currentIndex >= whoamiData.todayIndex;
}

function loadDay(index) {
  index = Math.max(0, Math.min(whoamiData.todayIndex, index));
  currentIndex = index;
  const entry = whoamiData.archive[index];
  ANSWER = entry.name;
  CLUES = entry.clues;
  STORAGE_KEY = 'guess-the-legend-' + entry.date_key;
  message.textContent = '';
  resultEl.hidden = true;
  guessInput.disabled = false;
  giveUpBtn.disabled = false;
  guessInput.value = '';

  dateEl.textContent = formatDateKey(entry.date_key) + (index === whoamiData.todayIndex ? ' (Today)' : '');

  loadState();
  if (solved || gaveUp) {
    endGame(solved);
  } else {
    renderClues();
  }
  updateNavButtons();
}

prevBtn.addEventListener('click', () => { loadDay(currentIndex - 1); prevBtn.blur(); });
nextBtn.addEventListener('click', () => { loadDay(currentIndex + 1); nextBtn.blur(); });

loadDay(currentIndex);
"""

WHOAMI_CSS = """

:root { color-scheme: dark; }
body {
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.site-header {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  background: #151515; box-shadow: 0 4px 14px rgba(0,0,0,0.45);
  padding: 0.9rem 1.25rem;
}
.brand { display: flex; align-items: center; gap: 8px; min-width: 0; }
.site-header h1 { font-size: 1.15rem; margin: 0; white-space: nowrap; }
.site-header .date { color: #999; font-size: 0.9rem; white-space: nowrap; }
.header-link {
  margin-left: auto; background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.45rem 0.9rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600;
  text-decoration: none; white-space: nowrap; flex-shrink: 0;
  display: inline-flex; align-items: center; gap: 6px;
  transition: border-color 0.15s ease, color 0.15s ease;
}
.header-link:hover { border-color: #ff5266; color: #ff5266; }

.disclaimer {
  max-width: 560px; margin: 1.25rem auto 0; padding: 0.75rem 1rem;
  color: #aaa; font-size: 0.85rem; line-height: 1.5;
  background: rgba(255,82,102,0.06); border-left: 3px solid #ff5266; border-radius: 0 8px 8px 0;
}

.whoami-main {
  max-width: 560px; margin: 0 auto; padding: 1rem 1.25rem 2rem;
  display: flex; flex-direction: column; align-items: center; gap: 1rem;
}
.whoami-nav { display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 0.5rem; }
.whoami-nav-info { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 2px; flex: 1; min-width: 0; }
.whoami-date { font-size: 0.85rem; font-weight: 700; color: #f2f2f2; white-space: nowrap; }
.nav-btn {
  background: #1a1a1a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.4rem 0.8rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
  cursor: pointer; white-space: nowrap; flex-shrink: 0;
  transition: border-color 0.15s ease, color 0.15s ease, opacity 0.15s ease;
}
.nav-btn:hover:not(:disabled) { border-color: #ff5266; color: #ff5266; }
.nav-btn:disabled { opacity: 0.35; cursor: not-allowed; }

.clue-list { list-style: none; margin: 0; padding: 0; width: 100%; display: flex; flex-direction: column; gap: 8px; }
.clue-item {
  background: #14141a; border: 1px solid #262626; border-left: 3px solid #ff5266;
  border-radius: 0 8px 8px 0; padding: 0.75rem 1rem; font-size: 0.9rem; line-height: 1.5;
  animation: clue-in 0.3s ease;
}
.clue-item:nth-child(1) { border-left-color: #ff5266; }
.clue-item:nth-child(2) { border-left-color: #ff8c42; }
.clue-item:nth-child(3) { border-left-color: #ffd166; }
.clue-item:nth-child(4) { border-left-color: #a3e635; }
.clue-item:nth-child(5) { border-left-color: #4ade80; }
@keyframes clue-in {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}

#guess-form { width: 100%; display: flex; gap: 8px; }
.guess-input {
  flex: 1; box-sizing: border-box; background: #14141a; border: 1px solid #333; color: #f2f2f2;
  border-radius: 8px; padding: 0.65rem 0.9rem; font-size: 0.9rem;
}
.guess-input:focus { outline: none; border-color: #ff5266; }
.guess-input:disabled { opacity: 0.5; }
.guess-submit {
  background: #ff5266; border: none; color: #fff; font-weight: 700;
  padding: 0.65rem 1.1rem; border-radius: 8px; cursor: pointer; white-space: nowrap;
}
.guess-submit:hover { background: #e6435a; }

.whoami-message { min-height: 1.3rem; font-size: 0.85rem; color: #ffd166; font-weight: 600; margin: 0; text-align: center; }

.give-up-btn {
  background: transparent; border: none; color: #777; font-size: 0.78rem;
  text-decoration: underline; cursor: pointer; padding: 0;
}
.give-up-btn:hover { color: #ff5266; }
.give-up-btn:disabled { opacity: 0.4; cursor: not-allowed; text-decoration: none; }

.whoami-result {
  text-align: center; background: #14141a; border: 1px solid #262626; border-radius: 10px;
  padding: 1rem; width: 100%;
}
#result-text { margin: 0; font-weight: 700; font-size: 1rem; }
"""


WHOAMI_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Guess The Legend - MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Guess The Legend: a daily Hall of Fame trivia game - guess the legend from progressively easier clues.">
</head>
<body>
<header class="site-header">
  <div class="brand">
    {brand_icon}
    <h1>Guess The Legend</h1>
  </div>
  <span class="date">{date_label}</span>
  <a class="header-link" href="index.html">{home_icon}Home</a>
</header>
<p class="disclaimer">
  A new Hall of Famer every day. Clues start obscure - draft slot, first team, an odd fact - and
  get more obvious with each wrong guess, ending with the thing everyone knows them for. Guess
  the full name or just the last name. Use Prev/Next to replay past puzzles.
</p>
<main class="whoami-main">
  <div class="whoami-nav">
    <button type="button" id="prev-day" class="nav-btn">&larr; Prev</button>
    <div class="whoami-nav-info">
      <span id="whoami-date" class="whoami-date"></span>
    </div>
    <button type="button" id="next-day" class="nav-btn">Next &rarr;</button>
  </div>
  <ul id="clue-list" class="clue-list"></ul>
  <form id="guess-form">
    <input id="guess-input" class="guess-input" type="text" placeholder="Who is it?" autocomplete="off">
    <button type="submit" class="guess-submit">Guess</button>
  </form>
  <p id="message" class="whoami-message" aria-live="polite"></p>
  <button type="button" id="give-up-btn" class="give-up-btn">Give up and reveal the answer</button>
  <div id="result" class="whoami-result" hidden>
    <p id="result-text"></p>
  </div>
</main>

<script id="whoami-data" type="application/json">{whoami_data_json}</script>
<script>{game_js}</script>

<style>{game_css}</style>
</body>
</html>
"""


def build_whoami_page(today):
    archive = build_whoami_archive(today)
    whoami_data_json = json.dumps({
        "archive": archive,
        "todayIndex": len(archive) - 1,
    }).replace("</", "<\\/")
    return WHOAMI_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        date_label=today.strftime("%A, %B %-d, %Y"),
        whoami_data_json=whoami_data_json,
        game_js=WHOAMI_JS,
        game_css=WHOAMI_CSS,
    )


def _whoami_badge_svg():
    """A magnifying-glass-over-plaque badge for the Hall of Fame trivia game."""
    return """<svg class="game-art whoami-art" viewBox="0 0 220 220" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Guess The Legend">
  <rect x="30" y="24" width="160" height="120" rx="8" fill="#3a2a14" stroke="#d9ae42" stroke-width="3"/>
  <rect x="42" y="36" width="136" height="96" rx="4" fill="#1a1408"/>
  <line x1="54" y1="56" x2="166" y2="56" stroke="#d9ae42" stroke-width="3"/>
  <line x1="54" y1="76" x2="166" y2="76" stroke="#8a6a2a" stroke-width="3"/>
  <line x1="54" y1="96" x2="140" y2="96" stroke="#8a6a2a" stroke-width="3"/>
  <line x1="54" y1="116" x2="150" y2="116" stroke="#8a6a2a" stroke-width="3"/>
  <circle cx="150" cy="150" r="34" fill="#0f1626" stroke="#ff5266" stroke-width="6"/>
  <line x1="174" y1="174" x2="200" y2="200" stroke="#ff5266" stroke-width="8" stroke-linecap="round"/>
  <text x="150" y="159" text-anchor="middle" font-family="Georgia, serif" font-weight="700" font-size="26" fill="#f5efe0">?</text>
</svg>"""


def _bordle_badge_svg():
    """Recreates the Bordle tile logo as SVG (rather than a static asset) so
    it renders crisply at any size and reuses the game's own tile colors."""
    letters = "BORDLE"
    colors = ["#ff5266", "#ffd166", "#4ade80", "#4ade80", "#ffd166", "#ff5266"]
    text_colors = ["#fff", "#1a1a1a", "#0d0d0d", "#0d0d0d", "#1a1a1a", "#fff"]
    tile, gap = 64, 8
    width = len(letters) * tile + (len(letters) - 1) * gap
    tiles = []
    for i, (ch, bg, fg) in enumerate(zip(letters, colors, text_colors)):
        x = i * (tile + gap)
        tiles.append(
            f'<rect x="{x}" y="0" width="{tile}" height="{tile}" rx="10" fill="{bg}"/>'
            f'<text x="{x + tile / 2}" y="45" fill="{fg}">{ch}</text>'
        )
    return (
        f'<svg class="game-art bordle-art" viewBox="0 0 {width} {tile}" '
        'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Bordle">'
        '<g font-family="ui-monospace, monospace" font-weight="800" '
        'font-size="32" text-anchor="middle">' + "".join(tiles) + "</g></svg>"
    )


def _challenge_badge_svg():
    """Recreates the 162-0 Challenge coin badge as SVG."""
    return """<svg class="game-art challenge-art" viewBox="0 0 220 220" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="162-0 The Challenge">
  <circle cx="110" cy="110" r="103" fill="#0f1626" stroke="#d9ae42" stroke-width="5"/>
  <circle cx="110" cy="110" r="87" fill="none" stroke="#5a6274" stroke-width="1.5" stroke-dasharray="3 5"/>
  <text x="112" y="121" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-weight="700" font-size="52" fill="#ff5266">162-0</text>
  <text x="110" y="119" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-weight="700" font-size="52"><tspan fill="#f5efe0">162-</tspan><tspan fill="#d9ae42">0</tspan></text>
  <text x="110" y="153" text-anchor="middle" font-family="Georgia, serif" font-size="14" letter-spacing="3" fill="#d9ae42">THE CHALLENGE</text>
</svg>"""


TOOL_ICONS = {
    "dashboard": (
        '<path d="M16 4 L28 16 L16 28 L4 16 Z" fill="none" stroke="currentColor" stroke-width="2"/>'
        '<circle cx="16" cy="4" r="2.2" fill="currentColor"/><circle cx="28" cy="16" r="2.2" fill="currentColor"/>'
        '<circle cx="16" cy="28" r="2.2" fill="currentColor"/><circle cx="4" cy="16" r="2.2" fill="currentColor"/>'
    ),
    "hr_picks": (
        '<path d="M4 24 L12 14 L18 19 L28 6" fill="none" stroke="currentColor" stroke-width="2.5" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M20 6 H28 V14" fill="none" stroke="currentColor" stroke-width="2.5" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    "roster_moves": (
        '<path d="M6 10 H24 M24 10 L19 5 M24 10 L19 15" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M26 22 H8 M8 22 L13 17 M8 22 L13 27" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    "park_factors": (
        '<path d="M6 8 H26 V18 L16 27 L6 18 Z" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linejoin="round"/>'
    ),
    "streaks": (
        '<path d="M16 3 C16 3 9 11 9 18 A7 7 0 0 0 23 18 C23 14 20 12 20 12 '
        'C20 16 17 17 17 17 C18 12 14 9 16 3 Z" fill="currentColor"/>'
    ),
    "weather": (
        '<path d="M4 10 H20 A4 4 0 1 0 16 6" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round"/>'
        '<path d="M4 16 H24 A4 4 0 1 1 20 20" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round"/>'
        '<path d="M4 22 H14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>'
    ),
    "leaders": (
        '<path d="M9 6 H23 V10 A7 7 0 0 1 9 10 Z" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linejoin="round"/>'
        '<path d="M16 17 V22 M11 22 H21" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round"/>'
        '<path d="M9 7 H5 A4 4 0 0 0 9 13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
        '<path d="M23 7 H27 A4 4 0 0 1 23 13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
    ),
    "standings": (
        '<path d="M6 26 V17 H12 V26 Z" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round"/>'
        '<path d="M13 26 V6 H19 V26 Z" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round"/>'
        '<path d="M20 26 V12 H26 V26 Z" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round"/>'
    ),
}


def _tool_icon_svg(name):
    return f'<svg class="tool-icon" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">{TOOL_ICONS[name]}</svg>'


HOME_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>MLB Matchup Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Daily MLB matchups, HR picks, and games - PlateDuel brings live baseball stats and daily games together.">
<script type="application/ld+json">{{"@context": "https://schema.org", "@type": "WebSite", "name": "PlateDuel", "url": "{site_base_url}/"}}</script>
</head>
<body>
<header class="home-header">
  <div class="home-brand">
    {brand_icon_lg}
    <h1 class="brand-wordmark"><span class="brand-word-plate">Plate</span><span class="brand-word-duel">Duel</span></h1>
  </div>
  <p class="home-date">{date_label}</p>
  <p class="home-tagline">Daily MLB matchups, HR picks, and games</p>
  <div class="home-search-wrap">
    <input id="site-search" type="text" placeholder="Jump to a player or team..." autocomplete="off">
    <div id="site-search-results" class="site-search-results" hidden></div>
  </div>
</header>

<main class="home-main">
  <section class="home-section">
    <h2 class="home-section-title">Games</h2>
    <div class="game-grid">
      <a class="game-card" href="{word_game_page}">
        {bordle_svg}
        <span class="game-card-title">Bordle</span>
        <span class="game-card-desc">Daily baseball word game &middot; Normal and Hard modes</span>
      </a>
      <a class="game-card" href="{game_page}">
        {challenge_svg}
        <span class="game-card-title">162-0 Challenge</span>
        <span class="game-card-desc">Can you build the perfect team?</span>
      </a>
      <a class="game-card" href="{grid_game_page}">
        {grid_svg}
        <span class="game-card-title">Diamond Grid</span>
        <span class="game-card-desc">Name a player who fits both the row and column</span>
      </a>
      <a class="game-card" href="{whoami_page}">
        {whoami_svg}
        <span class="game-card-title">Guess The Legend</span>
        <span class="game-card-desc">Guess the Hall of Famer, one clue at a time</span>
      </a>
    </div>
  </section>

  <section class="home-section">
    <h2 class="home-section-title">Stats &amp; Tools</h2>
    <div class="tool-grid">
      <a class="tool-card" href="{index_page}">
        {dashboard_icon}
        <span class="tool-title">Matchup Dashboard</span>
        <span class="tool-desc">Today's slate, weather, and batter-vs-pitcher history</span>
      </a>
      <a class="tool-card" href="{hr_picks_page}">
        {hr_picks_icon}
        <span class="tool-title">HR Picks</span>
        <span class="tool-desc">Today's best home run bets, ranked by matchup</span>
      </a>
      <a class="tool-card" href="{roster_moves_page}">
        {roster_moves_icon}
        <span class="tool-title">Roster Moves</span>
        <span class="tool-desc">Call-ups, injuries, and trades from the last few days</span>
      </a>
      <a class="tool-card" href="{park_factors_page}">
        {park_factors_icon}
        <span class="tool-title">Park Factors</span>
        <span class="tool-desc">Which ballparks favor hitters or pitchers</span>
      </a>
      <a class="tool-card" href="{streaks_page}">
        {streaks_icon}
        <span class="tool-title">Streaks</span>
        <span class="tool-desc">Active hit, home run, and strikeout streaks</span>
      </a>
      <a class="tool-card" href="{weather_page}">
        {weather_icon}
        <span class="tool-title">Weather Watch</span>
        <span class="tool-desc">Today's games ranked by wind-aided to wind-suppressed</span>
      </a>
      <a class="tool-card" href="{leaders_page}">
        {leaders_icon}
        <span class="tool-title">League Leaders</span>
        <span class="tool-desc">Season leaders in HR, AVG, OPS, ERA, and strikeouts</span>
      </a>
      <a class="tool-card" href="{standings_page}">
        {standings_icon}
        <span class="tool-title">Standings</span>
        <span class="tool-desc">Division races, wild card standings, and magic numbers</span>
      </a>
    </div>
  </section>

  <section class="home-section">
    <h2 class="home-section-title">Yesterday's Results</h2>
    <div class="recap-grid">
{recap_cards}
    </div>
  </section>
</main>

<style>
:root {{ color-scheme: dark; }}
body {{
  background: #0d0d0d; color: #f2f2f2; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
.home-header {{
  text-align: center; padding: 2.2rem 1.25rem 1.5rem;
  background: radial-gradient(ellipse at top, #1a1420 0%, #0d0d0d 65%);
}}
.home-brand {{ display: flex; align-items: center; justify-content: center; gap: 14px; text-align: center; }}
.brand-wordmark {{ margin: 0; font-size: 2.4rem; font-weight: 800; letter-spacing: -0.01em; }}
.brand-word-plate {{ color: #f2f2f2; }}
.brand-word-duel {{ color: #ff5266; }}
.home-date {{ margin: 0.9rem 0 0; color: #f2f2f2; font-size: 0.95rem; font-weight: 600; text-align: center; }}
.home-tagline {{ margin: 0.2rem 0 0; color: #999; font-size: 0.95rem; text-align: center; }}

.home-search-wrap {{ position: relative; max-width: 420px; margin: 1.5rem auto 0; text-align: center; }}
#site-search {{
  width: 100%; box-sizing: border-box; background: #14141a; border: 1px solid #333; color: #f2f2f2;
  padding: 0.7rem 1rem; border-radius: 10px; font-size: 0.95rem;
}}
#site-search:focus {{ outline: none; border-color: #ff5266; }}
.site-search-results {{
  position: absolute; top: calc(100% + 6px); left: 0; right: 0; z-index: 20; text-align: left;
  background: #14141a; border: 1px solid #262626; border-radius: 10px;
  max-height: 320px; overflow-y: auto; box-shadow: 0 8px 24px rgba(0,0,0,0.5);
}}
.search-result {{
  display: flex; justify-content: space-between; align-items: center; gap: 8px;
  padding: 0.6rem 0.9rem; text-decoration: none; color: #f2f2f2; font-size: 0.88rem;
  border-bottom: 1px solid #1e1e1e;
}}
.search-result:last-child {{ border-bottom: none; }}
.search-result:hover {{ background: #1e1e24; }}
.search-sub {{ color: #888; font-size: 0.75rem; flex-shrink: 0; }}
.search-empty {{ padding: 0.7rem 0.9rem; color: #777; font-size: 0.85rem; }}

.home-main {{ max-width: 980px; margin: 0 auto; padding: 0 1.25rem 3rem; }}
.home-section {{ margin-top: 1.5rem; }}
.home-section-title {{
  font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: #888; margin: 0 0 1rem; border-bottom: 1px solid #232323; padding-bottom: 0.6rem;
}}

.recap-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; }}
.recap-card {{ background: #14141a; border: 1px solid #262626; border-radius: 12px; padding: 1rem 1.1rem; }}
.recap-score {{ display: flex; align-items: baseline; gap: 6px; font-family: ui-monospace, monospace; font-size: 0.95rem; }}
.recap-team {{ color: #999; }}
.recap-winner {{ color: #f2f2f2; font-weight: 700; }}
.recap-at {{ color: #555; font-size: 0.8rem; }}
.recap-venue {{ margin: 2px 0 0; font-size: 0.72rem; color: #666; }}
.recap-performers {{ list-style: none; margin: 0.6rem 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }}
.recap-performers li {{ font-size: 0.78rem; color: #999; line-height: 1.4; }}
.recap-performer-name {{ color: #ccc; font-weight: 600; }}
a.recap-performer-name {{ text-decoration: none; border-bottom: 1px solid transparent; }}
a.recap-performer-name:hover {{ color: #ff5266; border-bottom-color: #ff5266; }}
.recap-empty {{ color: #777; font-size: 0.85rem; font-style: italic; }}

.game-grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1.25rem;
  max-width: 600px; margin: 0 auto;
}}
.game-card {{
  display: flex; flex-direction: column; align-items: center; gap: 0.55rem;
  background: #14141a; border: 1px solid #262626; border-radius: 16px;
  padding: 1.15rem 1.5rem; text-decoration: none; text-align: center;
  transition: transform 0.15s ease, border-color 0.15s ease;
}}
.game-card:hover {{ transform: translateY(-3px); border-color: #ff5266; }}
.game-art {{ height: 48px; width: auto; max-width: 100%; }}
.game-card-title {{ font-size: 1.15rem; font-weight: 700; color: #f2f2f2; }}
.game-card-desc {{ font-size: 0.82rem; color: #999; line-height: 1.4; }}

.tool-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1rem; }}
.tool-card {{
  display: flex; flex-direction: column; align-items: flex-start; gap: 0.5rem;
  background: #14141a; border: 1px solid #262626; border-radius: 12px;
  padding: 1.25rem; text-decoration: none;
  transition: transform 0.15s ease, border-color 0.15s ease;
}}
.tool-card:hover {{ transform: translateY(-2px); border-color: #6bb3ff; }}
.tool-icon {{ width: 26px; height: 26px; color: #6bb3ff; }}
.tool-title {{ font-size: 0.95rem; font-weight: 700; color: #f2f2f2; }}
.tool-desc {{ font-size: 0.78rem; color: #999; line-height: 1.4; }}

@media (max-width: 480px) {{
  .home-header h1 {{ font-size: 1.5rem; }}
}}
</style>

<script id="search-index" type="application/json">{search_index_json}</script>
<script>{home_js}</script>
</body>
</html>
"""

HOME_JS = """
const searchIndex = JSON.parse(document.getElementById('search-index').textContent);
const searchInput = document.getElementById('site-search');
const resultsEl = document.getElementById('site-search-results');

function renderResults(query) {
  if (!query) {
    resultsEl.hidden = true;
    resultsEl.innerHTML = '';
    return;
  }
  const q = query.toLowerCase();
  const matches = searchIndex.filter((e) => e.name.toLowerCase().includes(q)).slice(0, 8);
  if (!matches.length) {
    resultsEl.innerHTML = '<div class="search-empty">No matches</div>';
    resultsEl.hidden = false;
    return;
  }
  resultsEl.innerHTML = matches.map((e) => {
    const href = (e.type === 'player' ? 'players/' : 'teams/') + e.slug + '.html';
    const sub = e.sub ? `<span class="search-sub">${e.sub}</span>` : '';
    return `<a class="search-result" href="${href}"><span class="search-name">${e.name}</span>${sub}</a>`;
  }).join('');
  resultsEl.hidden = false;
}

searchInput.addEventListener('input', () => renderResults(searchInput.value.trim()));
searchInput.addEventListener('focus', () => renderResults(searchInput.value.trim()));
document.addEventListener('click', (e) => {
  if (!e.target.closest('.home-search-wrap')) resultsEl.hidden = true;
});
"""


def build_search_index():
    """Scans already-written player/team pages for a name -> slug index, so
    the home page can offer instant site-wide search with no server. Reads each
    file's own <h1>/date line rather than reversing slugify(), since that's
    not always lossless (initials, suffixes, apostrophes). Must run after
    the player/team pages for today have been written."""
    entries = []
    for dir_path, kind in ((PLAYERS_DIR, "player"), (TEAMS_DIR, "team")):
        for fname in sorted(os.listdir(dir_path)):
            if not fname.endswith(".html"):
                continue
            slug = fname[:-5]
            with open(os.path.join(dir_path, fname)) as f:
                head = f.read(2000)
            name_match = re.search(r"<h1>(.*?)</h1>", head)
            name = html.unescape(name_match.group(1)) if name_match else slug.replace("-", " ").title()
            sub = ""
            date_match = re.search(r'<span class="date">(.*?)</span>', head)
            if date_match:
                raw = html.unescape(date_match.group(1))
                if "·" in raw:
                    sub = raw.split("·")[-1].strip()
            entries.append({"name": name, "slug": slug, "type": kind, "sub": sub})
    return entries


def build_recap_cards(player_pages):
    try:
        results = get_yesterdays_results()
    except Exception as exc:
        print(f"  SKIPPED yesterday's results: {exc!r}")
        results = []

    if not results:
        return "      <p class='recap-empty'>No completed games from yesterday.</p>"

    cards = []
    for r in results:
        away_cls = "recap-winner" if r["away_winner"] else "recap-team"
        home_cls = "recap-team" if r["away_winner"] else "recap-winner"
        performers_html = ""
        if r["top_performers"]:
            items = []
            for p in r["top_performers"]:
                if p["name"] in player_pages:
                    name_html = (
                        f"<a class='recap-performer-name' href='{PLAYERS_DIR}/{slugify(p['name'])}.html'>"
                        f"{esc(p['name'])}</a>"
                    )
                else:
                    name_html = f"<span class='recap-performer-name'>{esc(p['name'])}</span>"
                items.append(f"<li>{name_html} ({esc(p['team'])}) &mdash; {esc(p['summary'])}</li>")
            performers_html = f"<ul class='recap-performers'>{''.join(items)}</ul>"
        cards.append(f"""    <div class="recap-card">
      <div class="recap-score">
        <span class="{away_cls}">{esc(TEAM_ABBR.get(r['away'], r['away']))} {r['away_score']}</span>
        <span class="recap-at">@</span>
        <span class="{home_cls}">{esc(TEAM_ABBR.get(r['home'], r['home']))} {r['home_score']}</span>
      </div>
      <p class="recap-venue">{esc(r['venue'])}</p>
      {performers_html}
    </div>""")
    return "\n".join(cards)


def build_home_page(today, search_index, player_pages):
    search_index_json = json.dumps(search_index).replace("</", "<\\/")
    return HOME_PAGE_TEMPLATE.format(
        date_label=today.strftime("%A, %B %-d, %Y"),
        site_base_url=SITE_BASE_URL,
        brand_icon_lg=brand_icon_svg(56),
        recap_cards=build_recap_cards(player_pages),
        word_game_page=WORD_GAME_FILE,
        game_page=GAME_PAGE_FILE,
        grid_game_page=GRID_GAME_FILE,
        whoami_page=WHOAMI_FILE,
        index_page=OUTPUT_FILE,
        hr_picks_page=HR_PICKS_FILE,
        roster_moves_page=ROSTER_MOVES_FILE,
        park_factors_page=PARK_FACTORS_FILE,
        streaks_page=STREAKS_FILE,
        weather_page=WEATHER_PAGE_FILE,
        bordle_svg=_bordle_badge_svg(),
        challenge_svg=_challenge_badge_svg(),
        grid_svg=_grid_badge_svg(),
        whoami_svg=_whoami_badge_svg(),
        dashboard_icon=_tool_icon_svg("dashboard"),
        hr_picks_icon=_tool_icon_svg("hr_picks"),
        roster_moves_icon=_tool_icon_svg("roster_moves"),
        park_factors_icon=_tool_icon_svg("park_factors"),
        streaks_icon=_tool_icon_svg("streaks"),
        weather_icon=_tool_icon_svg("weather"),
        leaders_page=LEADERBOARD_PAGE_FILE,
        leaders_icon=_tool_icon_svg("leaders"),
        standings_page=STANDINGS_PAGE_FILE,
        standings_icon=_tool_icon_svg("standings"),
        search_index_json=search_index_json,
        home_js=HOME_JS,
    )


ROBOTS_TXT_FILE = "robots.txt"
SITEMAP_FILE = "sitemap.xml"

ROBOTS_TXT = f"""User-agent: *
Allow: /

Sitemap: {SITE_BASE_URL}/{SITEMAP_FILE}
"""

# Static top-level pages included in the sitemap, independent of the daily
# player/team pages. 162-0-challenge.html is a hand-authored page (not
# written by this script) but still a real page worth listing.
STATIC_SITEMAP_PAGES = [
    (HOME_PAGE_FILE, "daily", 1.0),
    (OUTPUT_FILE, "hourly", 0.9),
    (STREAKS_FILE, "hourly", 0.8),
    (LEADERBOARD_PAGE_FILE, "hourly", 0.8),
    (STANDINGS_PAGE_FILE, "hourly", 0.8),
    (HR_PICKS_FILE, "daily", 0.7),
    (ROSTER_MOVES_FILE, "daily", 0.6),
    (WEATHER_PAGE_FILE, "daily", 0.5),
    (PARK_FACTORS_FILE, "weekly", 0.4),
    (GAME_PAGE_FILE, "weekly", 0.5),
    (WORD_GAME_FILE, "daily", 0.6),
    (GRID_GAME_FILE, "daily", 0.6),
    (WHOAMI_FILE, "daily", 0.6),
]


def build_sitemap_xml(player_pages, team_pages, today):
    """A sitemap covering every page the generator produces: the static
    tool/game pages, plus one <url> per player and team page. lastmod is
    today's date for all of them since the whole site regenerates together
    every 15 minutes."""
    lastmod = today.isoformat()
    urls = [
        f"  <url><loc>{SITE_BASE_URL}/{path}</loc><lastmod>{lastmod}</lastmod>"
        f"<changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
        for path, freq, priority in STATIC_SITEMAP_PAGES
    ]
    for name in player_pages:
        urls.append(
            f"  <url><loc>{SITE_BASE_URL}/{PLAYERS_DIR}/{slugify(name)}.html</loc>"
            f"<lastmod>{lastmod}</lastmod><changefreq>daily</changefreq><priority>0.5</priority></url>"
        )
    for name in team_pages:
        urls.append(
            f"  <url><loc>{SITE_BASE_URL}/{TEAMS_DIR}/{slugify(name)}.html</loc>"
            f"<lastmod>{lastmod}</lastmod><changefreq>daily</changefreq><priority>0.6</priority></url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n"
        "</urlset>\n"
    )


def main():
    today = datetime.date.today()
    games = get_all_games(today.isoformat())
    if not games:
        print(f"No games found for {today.isoformat()}")
        return

    stadiums_by_team = load_stadiums_by_team()

    master_rows = []
    hr_pick_rows = []
    player_pages = {}
    team_pages = {}
    pitcher_infos = {}
    weather_rows = []
    cards = []
    built_count = 0
    finished_game_labels = set()
    for i, game in enumerate(games, start=1):
        away = game["teams"]["away"]["team"]["name"]
        home = game["teams"]["home"]["team"]["name"]
        print(f"[{i}/{len(games)}] {away} @ {home}")
        try:
            card_html = build_game_card(game, stadiums_by_team, master_rows, hr_pick_rows, player_pages, team_pages, pitcher_infos, weather_rows)
        except Exception as exc:
            # A single game shouldn't be able to take down the whole run - an
            # exhausted-retries network error here should skip that game and
            # still publish the rest, not leave the whole site stale.
            print(f"  SKIPPED ({away} @ {home}): {exc!r}")
            continue

        built_count += 1
        # Once a game is Final there's no more "today's at-bat" left to
        # preview, so it (and its players) drop off the dashboard - the next
        # 15-minute regeneration picks this up automatically as games end.
        # Other pages (HR picks, streaks, team/player pages) are unaffected.
        if game.get("status", {}).get("abstractGameState") == "Final":
            finished_game_labels.add(f"{TEAM_ABBR.get(away, away)} @ {TEAM_ABBR.get(home, home)}")
        else:
            cards.append(card_html)

    if built_count == 0:
        print("Every game failed to build - leaving the existing page in place.")
        return

    dashboard_rows = [r for r in master_rows if r["game"] not in finished_game_labels]
    matchup_data_json = json.dumps(dashboard_rows).replace("</", "<\\/")
    cards_html = "\n".join(cards) if cards else (
        "<p class='empty-state'>All of today's games have ended. "
        "Check back tomorrow, or see <a href='index.html'>Yesterday's Results</a> on the home page.</p>"
    )

    page = PAGE_TEMPLATE.format(
        brand_icon=brand_icon_svg(),
        home_icon=brand_icon_svg(16),
        date_label=today.strftime("%A, %B %-d, %Y"),
        meta_description=(
            f"Today's MLB probable pitchers, starting lineups, and batter-vs-pitcher matchup history "
            f"for all {len(games)} games on {today.strftime('%B %-d, %Y')}."
        ),
        sports_events_jsonld=build_sports_events_jsonld(games, finished_game_labels),
        cards=cards_html,
        matchup_data_json=matchup_data_json,
        roster_moves_page=ROSTER_MOVES_FILE,
        game_page=GAME_PAGE_FILE,
        hr_picks_page=HR_PICKS_FILE,
        park_factors_page=PARK_FACTORS_FILE,
        streaks_page=STREAKS_FILE,
        weather_page=WEATHER_PAGE_FILE,
        leaders_page=LEADERBOARD_PAGE_FILE,
        standings_page=STANDINGS_PAGE_FILE,
        word_game_page=WORD_GAME_FILE,
        home_page=HOME_PAGE_FILE,
        season=CURRENT_SEASON,
        season_short=CURRENT_SEASON[-2:],
    )

    with open(OUTPUT_FILE, "w") as f:
        f.write(page)

    print(f"Wrote {OUTPUT_FILE} ({len(games)} games)")

    print("Fetching roster moves...")
    roster_moves = get_roster_moves()
    roster_moves_page = build_roster_moves_page(roster_moves)
    with open(ROSTER_MOVES_FILE, "w") as f:
        f.write(roster_moves_page)

    print(f"Wrote {ROSTER_MOVES_FILE}")

    hr_pick_rows.sort(key=lambda r: r["score"], reverse=True)
    hr_picks_page = build_hr_picks_page(hr_pick_rows[:HR_PICKS_TOP_N], today.strftime("%A, %B %-d, %Y"))
    with open(HR_PICKS_FILE, "w") as f:
        f.write(hr_picks_page)

    print(f"Wrote {HR_PICKS_FILE} ({len(hr_pick_rows)} eligible batters, top {HR_PICKS_TOP_N} shown)")

    park_factors_page = build_park_factors_page(stadiums_by_team, today.strftime("%A, %B %-d, %Y"))
    with open(PARK_FACTORS_FILE, "w") as f:
        f.write(park_factors_page)

    print(f"Wrote {PARK_FACTORS_FILE}")

    weather_page = build_weather_digest_page(weather_rows, today.strftime("%A, %B %-d, %Y"))
    with open(WEATHER_PAGE_FILE, "w") as f:
        f.write(weather_page)

    print(f"Wrote {WEATHER_PAGE_FILE} ({len(weather_rows)} games)")

    print("Fetching league leaders...")
    try:
        leaderboards_page = build_leaderboards_page(player_pages, today.strftime("%A, %B %-d, %Y"))
        with open(LEADERBOARD_PAGE_FILE, "w") as f:
            f.write(leaderboards_page)
        print(f"Wrote {LEADERBOARD_PAGE_FILE}")
    except Exception as exc:
        print(f"  SKIPPED league leaders: {exc!r}")

    print("Fetching standings...")
    try:
        standings = get_standings()
        standings_page = build_standings_page(standings, today.strftime("%A, %B %-d, %Y"))
        with open(STANDINGS_PAGE_FILE, "w") as f:
            f.write(standings_page)
        print(f"Wrote {STANDINGS_PAGE_FILE}")
    except Exception as exc:
        print(f"  SKIPPED standings: {exc!r}")

    print(f"Fetching pitcher streaks ({len(pitcher_infos)} probable starters)...")
    pitcher_streaks = []
    for info in pitcher_infos.values():
        try:
            streaks = get_pitcher_streaks(info["id"], k_thresholds=K_STREAK_THRESHOLDS)
        except Exception as exc:
            print(f"  SKIPPED pitcher streaks for {info['name']}: {exc!r}")
            continue
        pitcher_streaks.append({**info, **streaks})

    print("Expanding streaks league-wide (beyond today's slate)...")
    all_batters, all_pitcher_streaks = gather_league_wide_streaks(player_pages, pitcher_streaks)
    print(f"  {len(all_batters)} batters, {len(all_pitcher_streaks)} pitchers tracked for streaks")

    streaks_page = build_streaks_page(all_batters, all_pitcher_streaks, today.strftime("%A, %B %-d, %Y"))
    with open(STREAKS_FILE, "w") as f:
        f.write(streaks_page)

    print(f"Wrote {STREAKS_FILE}")

    word_game_page = build_word_game_page(today)
    with open(WORD_GAME_FILE, "w") as f:
        f.write(word_game_page)

    print(f"Wrote {WORD_GAME_FILE}")

    grid_game_page = build_grid_game_page(today)
    with open(GRID_GAME_FILE, "w") as f:
        f.write(grid_game_page)

    print(f"Wrote {GRID_GAME_FILE}")

    whoami_page = build_whoami_page(today)
    with open(WHOAMI_FILE, "w") as f:
        f.write(whoami_page)

    print(f"Wrote {WHOAMI_FILE}")

    # Cross-reference HR pick ranks into the per-player/team pages, and pull
    # each team's two most recent roster moves (roster_moves is already
    # newest-first, so the first two matches per team are the most recent).
    for rank, pick in enumerate(hr_pick_rows, start=1):
        name = pick["player"]
        if name in player_pages:
            player_pages[name]["hr_pick_rank"] = rank
            player_pages[name]["hr_pick_score"] = pick["score"]
        team = pick["team"]
        if team in team_pages:
            team_pages[team]["hr_picks"].append({"player": name, "rank": rank, "score": pick["score"]})

    for move in roster_moves:
        for team in move["teams"]:
            if team in team_pages and len(team_pages[team]["recent_moves"]) < 2:
                team_pages[team]["recent_moves"].append(move)

    os.makedirs(PLAYERS_DIR, exist_ok=True)
    os.makedirs(TEAMS_DIR, exist_ok=True)

    for name, data in player_pages.items():
        with open(os.path.join(PLAYERS_DIR, f"{slugify(name)}.html"), "w") as f:
            f.write(build_player_page(data))
    print(f"Wrote {len(player_pages)} player pages to {PLAYERS_DIR}/")

    print("Fetching 40-man rosters and upcoming schedules...")
    try:
        team_id_map = get_team_id_map()
    except Exception as exc:
        print(f"  SKIPPED team id lookup, rosters/schedules unavailable: {exc!r}")
        team_id_map = {}

    for name, data in team_pages.items():
        team_id = team_id_map.get(name)
        if team_id is None:
            data["roster"], data["schedule"] = [], []
            continue
        try:
            data["roster"] = get_team_roster(team_id)
        except Exception as exc:
            print(f"  SKIPPED roster for {name}: {exc!r}")
            data["roster"] = []
        try:
            data["schedule"] = get_team_upcoming_schedule(team_id)
        except Exception as exc:
            print(f"  SKIPPED schedule for {name}: {exc!r}")
            data["schedule"] = []

    for name, data in team_pages.items():
        with open(os.path.join(TEAMS_DIR, f"{slugify(name)}.html"), "w") as f:
            f.write(build_team_page(data, player_pages))
    print(f"Wrote {len(team_pages)} team pages to {TEAMS_DIR}/")

    search_index = build_search_index()
    home_page = build_home_page(today, search_index, player_pages)
    with open(HOME_PAGE_FILE, "w") as f:
        f.write(home_page)

    print(f"Wrote {HOME_PAGE_FILE} (search index: {len(search_index)} entries)")

    with open(ROBOTS_TXT_FILE, "w") as f:
        f.write(ROBOTS_TXT)
    print(f"Wrote {ROBOTS_TXT_FILE}")

    sitemap = build_sitemap_xml(player_pages, team_pages, today)
    with open(SITEMAP_FILE, "w") as f:
        f.write(sitemap)
    print(f"Wrote {SITEMAP_FILE} ({len(STATIC_SITEMAP_PAGES) + len(player_pages) + len(team_pages)} URLs)")


if __name__ == "__main__":
    main()
