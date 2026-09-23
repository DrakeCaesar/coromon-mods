"""Area names as the game shows them - GENERATED, do not edit by hand.

Regenerate with:

    python _gen_mapnames.py

`KEY_BY_MAP` is read from each map module's own `setMapName(...)` call and `NAME_BY_KEY` from
`classes.language.world_en-us`, both inside `Resources/resource.car`. `area()` is what the tabs
call; it mirrors the in-game overlay's label (`coromon-tools/ingame/area.py`) so the window and
the game agree on what a place is called.

The point of the table: the GUI used to label areas by prettifying the map FILE name, so
`volcanoCave_bf3` read "Volcano Cave Bf 3" - a name that appears nowhere in the game (the string
"Vulcano" does not exist in it at all) - while the game calls that place "Mount Muspel" and shows
that in its own popup on entering.
"""

from .text import pretty

# EIGHT of the names below are not names but PLACEHOLDER KEYS, and the game substitutes them while
# it DRAWS - so printing a name as it is read out of the localisation shows the raw key. Measured
# with the shipped archive, all three forms the game uses:
#
#   * `[monster <UID>]`         -> that Coromon's own name. `pyramid` is "Pyramid of
#                                 [monster TITAN_SAND]", which the game draws as "Pyramid of Sart",
#                                 and `ghostTown_temple` is the Monastery of Illuginn;
#   * `[world.map.<key>.name]` -> another area's name, i.e. `NAME_BY_KEY[key]`. `iceCave` is
#                                 literally "[world.map.frozenCave.name]", which is "Frozen Cavern";
#   * `[map <map file>]`       -> the area that map FILE belongs to (`mansion` names
#                                 `ghostTown_manor`, via `KEY_BY_MAP`).
#
# Without this, the window showed "Pyramid of [monster TITAN_SAND] F6" in every area column.
import re

try:
    import dex
except ImportError:                                    # pragma: no cover - only outside the tools
    dex = None

PLACEHOLDER = re.compile(r"\[([^\]]+)\]")
_MONSTER_NAMES = None


def _monster_names():
    """{UID: name} for every Coromon, read once - a placeholder names a UID, never a name."""
    global _MONSTER_NAMES
    if _MONSTER_NAMES is None:
        _MONSTER_NAMES = {}
        if dex is not None:
            for mon in dex.monsters(include_unused=True):
                _MONSTER_NAMES[mon.uid] = mon.name
    return _MONSTER_NAMES


def _one(token):
    """One placeholder's replacement, or None when it is a form this does not know."""
    parts = token.split()
    if len(parts) == 2 and parts[0] == "monster":
        return _monster_names().get(parts[1]) or pretty(parts[1])
    if len(parts) == 2 and parts[0] == "map":
        key = KEY_BY_MAP.get(parts[1])
        return NAME_BY_KEY.get(key) if key else pretty(parts[1])
    if token.startswith("world.map.") and token.endswith(".name"):
        return NAME_BY_KEY.get(token[len("world.map."):-len(".name")])
    return None


def resolve(text, _depth=0):
    """A name with the game's placeholders filled in, and the brackets gone either way.

    A name that resolves to another placeholder is resolved again, so regenerating the tables from
    a patched game cannot loop. An unknown form loses its brackets rather than being drawn as a
    key - seeing "[item X]" in an area column is what sent me looking for this in the first place.
    """
    if not text or "[" not in text:
        return text

    def swap(match):
        found = _one(match.group(1))
        if found is None or _depth > 3:
            return found or match.group(1)
        return resolve(found, _depth + 1)

    return PLACEHOLDER.sub(swap, text)

# {map file: localisation key}, from each map module's `setMapName(...)`  (199 entries)
KEY_BY_MAP = {
    "amishRoute":                "amishRoute",
    "amishRoute_mescherRealm":   "ghostTown_dimension",
    "amishTown":                 "amishTown",
    "amishTown_mescherRealm":    "ghostTown_dimension",
    "barracks":                  "desertTown",
    "battleDome":                "luxSolisRoute_battleDome",
    "battleDome_lobby":          "luxSolisRoute_battleDome",
    "cabin":                     "amishRoute",
    "coromonLab":                "luxSolisTown_coromonLab",
    "coromonLab_bf1":            "luxSolisTown_coromonLab",
    "desertRoute_1":             "desertRoute",
    "desertRoute_2":             "desertRoute",
    "desertRoute_3":             "desertRoute",
    "desertRoute_4":             "desertRoute",
    "desertRoute_5":             "desertRoute",
    "desertTown":                "desertTown",
    "developerHome_area":        "unknown",
    "developerHome_gauntlet":    "unknown",
    "developerHome_interact":    "unknown",
    "developerHome_npc":         "unknown",
    "developerHome_puzzle":      "unknown",
    "dojo":                      "dojo",
    "dojoGrounds":               "dojoGrounds",
    "dojo_leftWing":             "dojo",
    "dojo_rightWing":            "dojo",
    "electric":                  "luxSolisRoute_battleDome",
    "electricBeetleLab":         "electricBeetleLab",
    "electricCave":              "electricCave",
    "electricCave_bf1":          "electricCave",
    "electricCave_f1":           "electricCave",
    "electricCave_f1_A":         "electricCave",
    "electricCave_f1_B":         "electricCave",
    "electricCave_f2":           "electricCave",
    "electricCave_fuseRoom":     "electricCave_fuseRoom",
    "electricTown":              "electricTown",
    "fire":                      "luxSolisRoute_battleDome",
    "fireRoute":                 "fireRoute",
    "fireTown":                  "fireTown",
    "frozenCave_1":              "frozenCave",
    "frozenCave_2":              "frozenCave",
    "frozenCave_3":              "frozenCave",
    "frozenCave_3_A":            "frozenCave",
    "frozenCave_3_f1":           "frozenCave",
    "frozenCave_4":              "frozenCave",
    "frozenCave_5":              "frozenCave",
    "frozenCave_6":              "frozenCave",
    "frozenCave_7":              "frozenCave",
    "ghost":                     "luxSolisRoute_battleDome",
    "ghostTown":                 "ghostTown",
    "ghostTown_mescherRealm":    "ghostTown_dimension",
    "harbor":                    "harbor",
    "harbor_mescherRealm":       "ghostTown_dimension",
    "houseAl":                   "houseAl",
    "houseArchaeologist":        "houseArchaeologist",
    "houseArchaeologist_bf1":    "houseArchaeologist",
    "houseArchie":               "desertTown",
    "houseArchie_bf1":           "desertTown",
    "houseGunnar":               "houseGunnar",
    "houseJarl":                 "iceTown",
    "houseMary":                 "houseMary",
    "houseMason":                "houseMason",
    "houseMiners":               "houseMiners",
    "houseMiners_f1":            "houseMiners",
    "houseNoah":                 "amishTown",
    "houseNoah_bf1":             "amishTown",
    "housePele":                 "housePele",
    "housePerrin":               "amishTown",
    "housePlayer":               "housePlayer",
    "housePlayer_f1":            "housePlayer",
    "houseRecruiter":            "desertTown",
    "houseSabrina":              "houseSabrina",
    "houseSigrid":               "houseSigrid",
    "houseSwamp":                "houseSwamp",
    "houseTransfer_desertRoute": "desertRoute",
    "houseTransfer_ghostTown":   "ghostTown",
    "ice":                       "luxSolisRoute_battleDome",
    "iceMountain":               "iceMountain",
    "icePeak":                   "icePeak",
    "iceTown":                   "iceTown",
    "iceTownOld":                "iceTown",
    "introductoryForest_buck":   "harbor",
    "introductoryForest_buckAndJuniper": "harbor",
    "introductoryForest_juniper": "harbor",
    "library":                   "amishTown",
    "library_f1":                "amishTown",
    "luxSolisRoute":             "luxSolisRoute",
    "luxSolisRoute_mescherRealm": "ghostTown_dimension",
    "luxSolisTown":              "luxSolisTown",
    "luxSolisTown_mescherRealm": "ghostTown_dimension",
    "mansion":                   "ghostTown_manor",
    "mansion_bf1":               "ghostTown_manor",
    "mansion_bf1_maze_1":        "ghostTown_manor",
    "mansion_bf1_maze_2":        "ghostTown_manor",
    "mansion_bf1_maze_3":        "ghostTown_manor",
    "mansion_bf1_maze_4":        "ghostTown_manor",
    "mansion_bf1_maze_5":        "ghostTown_manor",
    "mansion_bf1_maze_6":        "ghostTown_manor",
    "mansion_bf1_maze_7":        "ghostTown_manor",
    "mansion_bf1_maze_8":        "ghostTown_manor",
    "mansion_bf1_winecellar":    "ghostTown_manor",
    "mansion_f1":                "ghostTown_manor",
    "normal":                    "luxSolisRoute_battleDome",
    "oasisCave_1":               "oasisCave",
    "oasisCave_1_A":             "oasisCave",
    "oasisCave_2":               "oasisCave",
    "oasisCave_2A":              "oasisCave",
    "oasisCave_3":               "oasisCave",
    "outpost":                   "unknown",
    "palace":                    "palace",
    "palace_alonRoom":           "palace",
    "palace_bf1":                "palace",
    "palace_hallway_1":          "palace",
    "palace_hallway_1_guardRoom": "palace",
    "palace_hallway_2":          "palace",
    "palace_hallway_2_guardRoom": "palace",
    "palace_privateQuarters":    "palace",
    "powerTower_f1":             "powerTower",
    "powerTower_f2":             "powerTower",
    "powerTower_f3":             "powerTower",
    "powerTower_f4":             "powerTower",
    "powerTower_f5":             "powerTower",
    "powerTower_f6":             "powerTower",
    "powerTower_f7":             "powerTower",
    "pyramidArea":               "pyramidArea",
    "pyramid_f1":                "pyramid",
    "pyramid_f10":               "pyramid",
    "pyramid_f11":               "pyramid",
    "pyramid_f2":                "pyramid",
    "pyramid_f3":                "pyramid",
    "pyramid_f4":                "pyramid",
    "pyramid_f5":                "pyramid",
    "pyramid_f6":                "pyramid",
    "pyramid_f7":                "pyramid",
    "pyramid_f8":                "pyramid",
    "pyramid_f9":                "pyramid",
    "reception":                 "luxSolisTown",
    "researchLab":               "luxSolisRoute_researchLab",
    "researchLab_restrictedArea": "luxSolisRoute_researchLab_restrictedArea",
    "sand":                      "luxSolisRoute_battleDome",
    "shop_electricTown":         "electricTown",
    "shop_fireTown":             "fireTown",
    "shop_ghostTown":            "ghostTown",
    "shop_iceTown":              "iceTown",
    "shop_luxSolisTown":         "luxSolisTown",
    "spa":                       "desertTown_spa",
    "spa_dressRoom":             "desertTown_spa",
    "station_homeTown":          "station",
    "station_homeTown_old":      "station",
    "station_luxSolisTown":      "luxSolisTown",
    "swamp_1":                   "swampRoute",
    "swamp_1_mescherRealm":      "ghostTown_dimension",
    "swamp_2":                   "swampRoute",
    "swamp_2_mescherRealm":      "ghostTown_dimension",
    "swamp_3":                   "swampRoute",
    "swamp_3_mescherRealm":      "ghostTown_dimension",
    "temple":                    "ghostTown_temple",
    "templeDungeon_01":          "ghostTown_templeDungeon",
    "templeDungeon_02":          "ghostTown_templeDungeon",
    "templeDungeon_03":          "ghostTown_templeDungeon",
    "templeDungeon_04":          "ghostTown_templeDungeon",
    "templeDungeon_05":          "ghostTown_templeDungeon",
    "templeDungeon_06":          "ghostTown_templeDungeon",
    "templeDungeon_07":          "ghostTown_templeDungeon",
    "templeDungeon_08":          "ghostTown_templeDungeon",
    "templeDungeon_09":          "ghostTown_templeDungeon",
    "templeDungeon_10":          "ghostTown_templeDungeon",
    "templeDungeon_11":          "ghostTown_templeDungeon",
    "temple_bf1":                "ghostTown_temple",
    "temple_mescherRealm":       "ghostTown_dimension",
    "titanArea_mescherRealm":    "ghostTown_dimension",
    "trainerHub_amishTown":      "amishTown",
    "trainerHub_desertTown":     "desertTown",
    "trainerHub_desertTown_f1":  "desertTown",
    "trainerHub_electricTown":   "electricTown",
    "trainerHub_electricTown_f1": "electricTown",
    "trainerHub_fireTown":       "fireTown",
    "trainerHub_fireTown_f1":    "fireTown",
    "trainerHub_ghostTown":      "ghostTown",
    "trainerHub_ghostTown_f1":   "ghostTown",
    "trainerHub_iceTown":        "iceTown",
    "trainerHub_luxSolisTown":   "luxSolisTown",
    "trainerHub_luxSolisTown_f1": "luxSolisTown",
    "volcanoCave":               "volcanoCave",
    "volcanoCave_bf1":           "volcanoCave",
    "volcanoCave_bf1_A":         "volcanoCave",
    "volcanoCave_bf2":           "volcanoCave",
    "volcanoCave_bf3":           "volcanoCave",
    "volcanoCave_bf4":           "volcanoCave",
    "volcanoCave_f1":            "volcanoCave",
    "volcanoCave_f1_A":          "volcanoCave",
    "water":                     "luxSolisRoute_battleDome",
    "waterJellyfishLab":         "electricTown",
    "waterRoute_1":              "waterRoute",
    "waterRoute_2":              "waterRoute",
    "waterRoute_3":              "waterRoute",
    "waterRoute_4":              "waterRoute",
    "waterRoute_5":              "waterRoute",
    "waterTown":                 "waterTown_ruins",
    "waterTown_titanTemple":     "waterTown_titanTemple",}

# {key: English name}, from `world.map.<key>.name` in the game's world localisation  (63 entries)
NAME_BY_KEY = {
    "amishRoute":                "Woodlow Forest",
    "amishTown":                 "Hayville",
    "centerOfEarth":             "Equilibrium",
    "desertRoute":               "Wostin Desert",
    "desertRoute_3":             "Scorching Sands",
    "desertTown":                "Darudic",
    "desertTown_spa":            "Sandstorm Spa",
    "dojo":                      "Light Dojo",
    "dojoGrounds":               "Dojo grounds",
    "electricBeetleLab":         "[monster ELECTRIC_BEETLE_1] Lab",
    "electricCave":              "Thunderous Cave",
    "electricCave_fuseRoom":     "Fuse Room",
    "electricTown":              "Donar Island",
    "fireRoute":                 "Vlamma Heights",
    "fireTown":                  "Vlamma",
    "frozenCave":                "Frozen Cavern",
    "ghostRoute":                "[world.map.ghostTown.name]",
    "ghostTown":                 "Pawbury",
    "ghostTown_dimension":       "Mescher Realm",
    "ghostTown_manor":           "Furclaw Manor",
    "ghostTown_temple":          "Monastery of [monster TITAN_GHOST]",
    "ghostTown_templeDungeon":   "Haunted Halls",
    "harbor":                    "Woodlow Harbor",
    "houseAl":                   "Al's house",
    "houseArchaeologist":        "Archaeologist's house",
    "houseGunnar":               "Gunnar's house",
    "houseMary":                 "Mary's house",
    "houseMason":                "Community Sculpting Center",
    "houseMiners":               "Miner's Inn",
    "housePele":                 "Pele's house",
    "housePlayer":               "Your house",
    "houseSabrina":              "Sabrina's house",
    "houseSigrid":               "Sigrid's house",
    "houseSwamp":                "Cabin",
    "iceCave":                   "[world.map.frozenCave.name]",
    "iceMountain":               "Fresia Pass",
    "icePeak":                   "Frostpeak",
    "iceRoute":                  "[world.map.iceMountain.name]",
    "iceTown":                   "Alavi",
    "luxSolisRoute":             "Radiant Park",
    "luxSolisRoute_battleDome":  "Battle Dome",
    "luxSolisRoute_researchLab": "R&D Lab",
    "luxSolisRoute_researchLab_restrictedArea": "Restricted Access Area",
    "luxSolisTown":              "Lux Solis Campus",
    "luxSolisTown_coromonLab":   "Coromon Lab",
    "mansion":                   "[map ghostTown_manor]",
    "northernMountains":         "Batavi Mountains",
    "oasisCave":                 "Vermeer Grotto",
    "palace":                    "Grand Palace",
    "powerTower":                "Power Tower",
    "pyramid":                   "Pyramid of [monster TITAN_SAND]",
    "pyramidArea":               "Dawn Valley",
    "station":                   "Station",
    "swampRoute":                "Soggy Swamp",
    "unknown":                   "?",
    "volcano":                   "[world.map.volcanoCave.name]",
    "volcanoCave":               "Mount Muspel",
    "waterRoute":                "Submerged Tunnels",
    "waterTown":                 "Ixqun",
    "waterTown_ruins":           "Ixqun Ruins",
    "waterTown_titanTemple":     "Sun Temple",
    "workerHouse1":              "Worker house A",
    "workerHouse2":              "Worker house B",}


def area(map_file):
    """The area as the game names it: ``volcanoCave_bf3`` -> "Mount Muspel BF3".

    Three cases, and the third is why this is not a plain lookup:

      * the file starts with its own key -> the name plus the file's tail, which is the floor:
        ``volcanoCave_bf3`` -> "Mount Muspel BF3";
      * the file IS the key -> the name alone;
      * the file is named after something else (``library_f1`` inside ``amishTown``) -> the tail is
        not part of the name, so the name comes first and the file identifies it in brackets:
        "Amish Town (Library F1)". Two zones the game calls the same thing stay distinguishable,
        which a list of tickable areas needs.
    """
    key = KEY_BY_MAP.get(map_file)
    name = resolve(NAME_BY_KEY.get(key)) if key else None
    # A placeholder is not a name: the debug maps localise to "?" (`world.map.unknown.name`), and
    # "? (Developer Home Area)" reads worse than the file name does.
    if not name or name in ("?", "???"):
        return pretty(map_file)              # unmapped map: the old behaviour, not an error
    if key == map_file:
        return name
    prefix = key + "_"
    if map_file.startswith(prefix):
        tail = map_file[len(prefix):]
        if tail:
            return "%s %s" % (name, tail.replace("_", " ").upper())
    return "%s (%s)" % (name, pretty(map_file))
