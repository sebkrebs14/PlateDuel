import json

with open("stadiums.json") as f:
    stadiums = json.load(f)

print(f"Loaded {len(stadiums)} stadiums")
print(json.dumps(stadiums[0], indent=2))
