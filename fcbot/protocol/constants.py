"""Compile-time constants from freeciv 3.2 headers that the wire format needs.

Array sizes and bitvector widths are baked into the C structs, so they are
part of the protocol. Values verified against the freeciv 3.2.5 sources
(tag R3_2_5): common/fc_types.h, common/city.h, common/actions.h,
common/networking/connection.h and the flag specenums.
"""

MAX_NUM_ITEMS = 200
MAX_NUM_ADVANCES = 400
MAX_NUM_BUILDINGS = 200
MAX_NUM_UNITS = 300
MAX_NUM_PLAYER_SLOTS = 512
MAX_EXTRA_TYPES = 250
MAX_NUM_TERRAINS = 96
MAX_CITY_SIZE = 0xFF
ACTION_COUNT = 125

CONSTANTS = {
    "MAX_NUM_ITEMS": MAX_NUM_ITEMS,
    "MAX_NUM_ADVANCES": MAX_NUM_ADVANCES,
    "A_LAST": MAX_NUM_ADVANCES + 1,
    "MAX_NUM_BUILDINGS": MAX_NUM_BUILDINGS,
    "B_LAST": MAX_NUM_BUILDINGS,
    "MAX_NUM_UNITS": MAX_NUM_UNITS,
    "U_LAST": MAX_NUM_UNITS,
    "O_LAST": 6,
    "SP_MAX": 20,
    "FEELING_LAST": 6,
    "CITIZEN_LAST": 4,
    "ACTION_COUNT": ACTION_COUNT,
    "MAX_NUM_ACTIONS": ACTION_COUNT,
    "ATTRIBUTE_CHUNK_SIZE": 1400,
    "MAX_CALENDAR_FRAGMENTS": 52,
    "MAX_CITY_SIZE": MAX_CITY_SIZE,
    "MAX_CITY_NATIONALITIES": min(MAX_NUM_PLAYER_SLOTS, MAX_CITY_SIZE),
    "MAX_CITY_TILES": 91,
    "MAX_COUNTERS": 20,
    "MAX_GRANARY_INIS": 24,
    "MAX_LEN_ADDR": 256,
    "MAX_LEN_CAPSTR": 512,
    "MAX_LEN_CITYNAME": 120,
    "MAX_LEN_ENUM": 64,
    "MAX_LEN_MAP_LABEL": 64,
    "MAX_LEN_MSG": 1536,
    "MAX_LEN_NAME": 48,
    "MAX_LEN_PACKET": 4096,
    "MAX_LEN_CONTENT": 4096 - 20,
    "MAX_LEN_PASSWORD": 512,
    "MAX_LEN_ROUTE": 2000,
    "MAX_NUM_BUILDING_LIST": 10,
    "MAX_NUM_TECH_LIST": 10,
    "MAX_NUM_UNIT_LIST": 10,
    "MAX_NUM_CONNECTIONS": 2 * MAX_NUM_PLAYER_SLOTS,
    "MAX_NUM_LEADERS": MAX_NUM_ITEMS,
    "MAX_NUM_MULTIPLIERS": 50,
    "MAX_NUM_NATIONS": 65535,
    "MAX_NUM_NATION_GROUPS": 128,
    "MAX_NUM_NATION_SETS": 32,
    "MAX_NUM_PLAYER_SLOTS": MAX_NUM_PLAYER_SLOTS,
    "MAX_NUM_REQS": 40,
    "MAX_NUM_RESOURCES": MAX_NUM_TERRAINS // 2,
    "MAX_NUM_RULESETS": 63,
    "MAX_RULESET_NAME_LENGTH": 64,
    "MAX_VET_LEVELS": 20,
    "MAX_EXTRA_TYPES": MAX_EXTRA_TYPES,
    "MAX_NUM_STARTPOS_NATIONS": 1024,
    "UCL_LAST": 32,
    "L_MAX": 64,
    "NUM_SS_STRUCTURALS": 32,
}

# Bit widths of each bv_* type; wire size is ceil(bits / 8) raw bytes.
# Flag vectors take their width from the number of values in the matching
# specenum, user flags included.
BITVECTOR_BITS = {
    "bv_extras": MAX_EXTRA_TYPES,
    "bv_max_extras": MAX_EXTRA_TYPES,
    "bv_imprs": MAX_NUM_BUILDINGS,
    "bv_utypes": MAX_NUM_UNITS,
    "bv_player": MAX_NUM_PLAYER_SLOTS,
    "bv_startpos_nations": 1024,
    "bv_unit_classes": 32,
    "bv_unit_type_roles": 64,
    "bv_spaceship_structure": 32,
    "bv_actions": ACTION_COUNT,
    "bv_action_sub_results": 4,
    "bv_causes": 9,
    "bv_city_options": 3,
    "bv_disaster_effects": 7,
    "bv_extra_flags": 22,
    "bv_goods_flags": 3,
    "bv_impr_flags": 12,
    "bv_plr_flags": 3,
    "bv_rmcauses": 4,
    "bv_road_flags": 4,
    "bv_tech_flags": 13,
    "bv_terrain_flags": 20,
    "bv_unit_class_flags": 38,
    "bv_unit_type_flags": 80,
}

NETWORK_CAPSTRING = "+Freeciv-3.2-network ownernull16 unignoresync tu32 hap2clnt"
MAJOR_VERSION, MINOR_VERSION, PATCH_VERSION = 3, 2, 5


def bv_bytes(public_type):
    bits = BITVECTOR_BITS[public_type]
    return (bits - 1) // 8 + 1


def resolve_size(expr):
    """Evaluate an array-size expression like 'A_LAST+1' or 'MAX_LEN_PACKET / 3'."""
    expr = expr.strip()
    if expr.isdigit():
        return int(expr)
    return int(eval(expr, {"__builtins__": {}}, CONSTANTS))
