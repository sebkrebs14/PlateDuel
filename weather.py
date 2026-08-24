"""
Wind-vs-ballpark-orientation analysis using free weather APIs.

NWS (api.weather.gov, no key required) only covers US territory, so it can't
serve Rogers Centre in Toronto. For any stadium outside NWS coverage, we fall
back to Open-Meteo (open-meteo.com, also no key required), which is global.

Forecasts report wind direction as the compass point the wind is coming FROM
(e.g. "SW" means wind blowing out of the southwest, toward the northeast). We
convert that to the direction the wind is blowing TOWARD and compare it to a
stadium's home_plate_orientation_degrees (the direction from home plate to
straightaway center field) to flag whether the wind is blowing out over the
fence (favors hitters), in from the outfield (favors pitchers), or crossing
the field (neutral).
"""

import datetime
import json
import re
import time
import urllib.error
import urllib.request

NWS_BASE = "https://api.weather.gov"
NWS_USER_AGENT = "bvp-matchups-weather-script (contact: local-script, no-reply@example.com)"

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

OPEN_METEO_WEATHER_CODES = {
    0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing Rime Fog",
    51: "Light Drizzle", 53: "Drizzle", 55: "Dense Drizzle",
    61: "Slight Rain", 63: "Rain", 65: "Heavy Rain",
    71: "Slight Snow", 73: "Snow", 75: "Heavy Snow",
    80: "Slight Rain Showers", 81: "Rain Showers", 82: "Violent Rain Showers",
    95: "Thunderstorm",
}

COMPASS_DEGREES = {
    "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5,
    "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
    "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
    "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
}

CALM_THRESHOLD_MPH = 5

# Stadiums with a fixed (non-retractable) roof, where outdoor wind has no
# bearing on the game. Retractable-roof parks are left out on purpose - their
# roof is open more often than not, so we still want a wind reading for them.
FIXED_ROOF_STADIUMS = {"Tropicana Field"}


def fetch_nws_json(url, retries=2):
    """GETs and parses JSON, retrying once or twice on transient network
    errors (connection resets, SSL EOF) before giving up to the caller."""
    request = urllib.request.Request(
        url, headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response)
        except OSError:
            if attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))


def compass_to_degrees(compass):
    return COMPASS_DEGREES.get(compass.upper()) if compass else None


def degrees_to_compass(degrees):
    if degrees is None:
        return None
    closest = min(COMPASS_DEGREES, key=lambda c: abs((COMPASS_DEGREES[c] - degrees + 180) % 360 - 180))
    return closest


def parse_wind_speed_mph(wind_speed_str):
    """NWS gives '12 mph' or a range like '5 to 12 mph'; average the range."""
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", wind_speed_str or "")]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def describe_period(period):
    """Hourly periods have no 'name' (just a startTime); 12-hour periods do
    (e.g. 'This Afternoon'). Fall back to a formatted time when name is blank."""
    name = period.get("name")
    if name:
        return name
    start_time = period.get("startTime")
    if start_time:
        try:
            return datetime.datetime.fromisoformat(start_time).strftime("%-I %p")
        except ValueError:
            pass
    return "current"


def get_wind_forecast(latitude, longitude):
    """Returns the nearest forecast period's wind/weather data for a location,
    or None if the NWS API has no usable data (e.g. outside US coverage,
    transient outage). Tries the hourly forecast first, falls back to the
    coarser 12-hour forecast if the hourly endpoint errors out."""
    try:
        points = fetch_nws_json(f"{NWS_BASE}/points/{latitude},{longitude}")
        hourly_url = points["properties"]["forecastHourly"]
        twelve_hour_url = points["properties"]["forecast"]
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, OSError):
        return None

    for url in (hourly_url, twelve_hour_url):
        try:
            data = fetch_nws_json(url)
            period = data["properties"]["periods"][0]
            speed_mph = parse_wind_speed_mph(period.get("windSpeed"))
            compass = period.get("windDirection")
            if speed_mph is None or not compass:
                continue
            return {
                "period_name": describe_period(period),
                "temperature": period.get("temperature"),
                "temperature_unit": period.get("temperatureUnit", ""),
                "short_forecast": period.get("shortForecast", ""),
                "wind_speed_mph": speed_mph,
                "wind_speed_raw": period.get("windSpeed"),
                "wind_from_compass": compass,
                "wind_from_degrees": compass_to_degrees(compass),
            }
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, OSError):
            continue
    return None


def classify_wind_effect(wind_from_degrees, wind_speed_mph, home_plate_orientation_degrees):
    """Compares the direction the wind is blowing TOWARD to the stadium's
    home-plate-to-center-field orientation. Returns a dict with the angular
    difference and an effect of 'hitters', 'pitchers', or 'neutral'."""
    if wind_from_degrees is None or wind_speed_mph is None:
        return {"effect": "unknown", "label": "Wind data unavailable"}

    if wind_speed_mph < CALM_THRESHOLD_MPH:
        return {
            "blowing_toward_degrees": None,
            "angle_diff_degrees": None,
            "effect": "neutral",
            "label": f"Calm ({wind_speed_mph:.0f} mph) — negligible effect",
        }

    blowing_toward_degrees = (wind_from_degrees + 180) % 360
    raw_diff = abs(blowing_toward_degrees - home_plate_orientation_degrees) % 360
    angle_diff = min(raw_diff, 360 - raw_diff)

    if angle_diff <= 45:
        effect = "hitters"
        label = f"Blowing out toward CF ({angle_diff:.0f}° off), {wind_speed_mph:.0f} mph — favors hitters"
    elif angle_diff >= 135:
        effect = "pitchers"
        label = f"Blowing in from CF ({angle_diff:.0f}° off), {wind_speed_mph:.0f} mph — favors pitchers"
    else:
        effect = "neutral"
        label = f"Crosswind ({angle_diff:.0f}° off), {wind_speed_mph:.0f} mph — neutral effect"

    return {
        "blowing_toward_degrees": round(blowing_toward_degrees, 1),
        "angle_diff_degrees": round(angle_diff, 1),
        "effect": effect,
        "label": label,
    }


def get_wind_forecast_open_meteo(latitude, longitude):
    """Global fallback for stadiums outside NWS (US-only) coverage, e.g.
    Rogers Centre in Toronto. Returns the current hour's wind/weather data,
    or None on any request failure."""
    url = (
        f"{OPEN_METEO_BASE}?latitude={latitude}&longitude={longitude}"
        f"&hourly=wind_speed_10m,wind_direction_10m,temperature_2m,weather_code"
        f"&wind_speed_unit=mph&temperature_unit=fahrenheit&forecast_days=1&timezone=auto"
    )
    try:
        data = fetch_nws_json(url)

        hourly = data["hourly"]
        now = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
        times = hourly["time"]
        index = 0
        for i, t in enumerate(times):
            if datetime.datetime.fromisoformat(t) >= now:
                index = i
                break

        speed_mph = hourly["wind_speed_10m"][index]
        degrees = hourly["wind_direction_10m"][index]
        if speed_mph is None or degrees is None:
            return None

        weather_code = hourly["weather_code"][index]
        period_time = datetime.datetime.fromisoformat(times[index])
        return {
            "period_name": period_time.strftime("%-I %p"),
            "temperature": hourly["temperature_2m"][index],
            "temperature_unit": "F",
            "short_forecast": OPEN_METEO_WEATHER_CODES.get(weather_code, ""),
            "wind_speed_mph": speed_mph,
            "wind_speed_raw": f"{speed_mph:.0f} mph",
            "wind_from_compass": degrees_to_compass(degrees),
            "wind_from_degrees": degrees,
        }
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, OSError, ValueError):
        return None


def get_wind_effect_for_stadium(stadium):
    """Convenience wrapper: fetches the forecast for a stadiums.json record and
    classifies the wind effect against its home_plate_orientation_degrees."""
    if stadium["stadium"] in FIXED_ROOF_STADIUMS:
        return None, {"effect": "indoor", "label": "Fixed roof — weather does not affect the game"}

    forecast = get_wind_forecast(stadium["latitude"], stadium["longitude"])
    if forecast is None:
        forecast = get_wind_forecast_open_meteo(stadium["latitude"], stadium["longitude"])
    if forecast is None:
        return None, {"effect": "unknown", "label": "Wind data unavailable"}
    classification = classify_wind_effect(
        forecast["wind_from_degrees"],
        forecast["wind_speed_mph"],
        stadium["home_plate_orientation_degrees"],
    )
    return forecast, classification
