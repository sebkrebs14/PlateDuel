"""
Builds stadiums.json from the live MLB Stats API (no key required).

Pulls team -> venue mapping and venue location/field data from statsapi.mlb.com,
then overlays home_plate_orientation_degrees and wall heights, which MLB's API
does not expose and so are tracked here manually.
"""

import json
import urllib.request

API_BASE = "https://statsapi.mlb.com/api/v1"

# Not available from the MLB Stats API - hand-compiled from published sources.
MANUAL_DATA = {
    "Baltimore Orioles": {"orientation": 30, "wall_heights": (7, 7, 7)},
    "Boston Red Sox": {"orientation": 45, "wall_heights": (37, 17, 5)},
    "New York Yankees": {"orientation": 75, "wall_heights": (8, 8, 8)},
    "Tampa Bay Rays": {"orientation": 45, "wall_heights": (8, 8, 8)},
    "Toronto Blue Jays": {"orientation": 0, "wall_heights": (10, 10, 10)},
    "Chicago White Sox": {"orientation": 135, "wall_heights": (8, 8, 8)},
    "Cleveland Guardians": {"orientation": 0, "wall_heights": (19, 8, 8)},
    "Detroit Tigers": {"orientation": 150, "wall_heights": (8, 7, 7)},
    "Kansas City Royals": {"orientation": 45, "wall_heights": (9.5, 9.5, 9.5)},
    "Minnesota Twins": {"orientation": 90, "wall_heights": (8, 8, 8)},
    "Athletics": {"orientation": 20, "wall_heights": (8, 8, 8)},
    "Houston Astros": {"orientation": 345, "wall_heights": (19, 24, 7)},
    "Los Angeles Angels": {"orientation": 45, "wall_heights": (8, 8, 8)},
    "Seattle Mariners": {"orientation": 45, "wall_heights": (8, 8, 8)},
    "Texas Rangers": {"orientation": 60, "wall_heights": (8, 8, 8)},
    "Atlanta Braves": {"orientation": 135, "wall_heights": (8, 16, 8)},
    "Miami Marlins": {"orientation": 75, "wall_heights": (8, 8, 8)},
    "New York Mets": {"orientation": 30, "wall_heights": (8, 8, 8)},
    "Philadelphia Phillies": {"orientation": 15, "wall_heights": (13, 10, 14)},
    "Washington Nationals": {"orientation": 30, "wall_heights": (8, 8, 8)},
    "Chicago Cubs": {"orientation": 30, "wall_heights": (15, 11.5, 15)},
    "Cincinnati Reds": {"orientation": 120, "wall_heights": (8, 8, 8)},
    "Milwaukee Brewers": {"orientation": 135, "wall_heights": (8, 8, 8)},
    "Pittsburgh Pirates": {"orientation": 120, "wall_heights": (6, 10, 21)},
    "St. Louis Cardinals": {"orientation": 60, "wall_heights": (8, 8, 8)},
    "Arizona Diamondbacks": {"orientation": 0, "wall_heights": (8, 8, 8)},
    "Colorado Rockies": {"orientation": 0, "wall_heights": (8, 8, 8)},
    "Los Angeles Dodgers": {"orientation": 30, "wall_heights": (8, 8, 8)},
    "San Diego Padres": {"orientation": 0, "wall_heights": (8, 8, 8)},
    "San Francisco Giants": {"orientation": 90, "wall_heights": (8, 8, 8)},
}


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "stadiums-json-builder"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def main():
    teams = fetch_json(f"{API_BASE}/teams?sportId=1&activeStatus=Yes")["teams"]

    stadiums = []
    for team in teams:
        team_name = team["name"]
        venue_id = team["venue"]["id"]

        manual = MANUAL_DATA.get(team_name)
        if manual is None:
            print(f"WARNING: no manual data for '{team_name}', skipping")
            continue

        venue = fetch_json(f"{API_BASE}/venues/{venue_id}?hydrate=location,fieldInfo")["venues"][0]
        location = venue["location"]
        coordinates = location["defaultCoordinates"]
        field = venue["fieldInfo"]
        lf_h, cf_h, rf_h = manual["wall_heights"]

        stadiums.append({
            "team": team_name,
            "stadium": venue["name"],
            "latitude": coordinates["latitude"],
            "longitude": coordinates["longitude"],
            "home_plate_orientation_degrees": manual["orientation"],
            "left_field_distance_feet": field["leftLine"],
            "center_field_distance_feet": field["center"],
            "right_field_distance_feet": field["rightLine"],
            "left_field_wall_height_feet": lf_h,
            "center_field_wall_height_feet": cf_h,
            "right_field_wall_height_feet": rf_h,
        })

    stadiums.sort(key=lambda s: s["team"])

    with open("stadiums.json", "w") as f:
        json.dump(stadiums, f, indent=2)
        f.write("\n")

    print(f"Wrote {len(stadiums)} stadiums to stadiums.json")


if __name__ == "__main__":
    main()
