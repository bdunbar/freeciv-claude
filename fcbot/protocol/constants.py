"""Compile-time constants from freeciv 2.6 headers that the wire format needs.

Array sizes and bitvector widths are baked into the C structs, so they are
part of the protocol. Values verified against freeciv-2.6.6 sources.
"""

MAX_NUM_ITEMS = 200
MAX_NUM_PLAYER_SLOTS = 160
MAX_EXTRA_TYPES = 64
MAX_NUM_TERRAINS = 96

CONSTANTS = {
    "MAX_NUM_ITEMS": MAX_NUM_ITEMS,
    "A_LAST": MAX_NUM_ITEMS,
    "B_LAST": MAX_NUM_ITEMS,
    "U_LAST": MAX_NUM_ITEMS,
    "O_LAST": 6,
    "SP_MAX": 20,
    "FEELING_LAST": 6,
    "CITIZEN_LAST": 4,
    "ACTION_COUNT": 14,
    "ATTRIBUTE_CHUNK_SIZE": 1400,
    "MAX_CALENDAR_FRAGMENTS": 52,
    "MAX_GRANARY_INIS": 24,
    "MAX_LEN_ADDR": 256,
    "MAX_LEN_CAPSTR": 512,
    "MAX_LEN_ENUM": 64,
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
    "MAX_NUM_MULTIPLIERS": 15,
    "MAX_NUM_NATIONS": 65535,
    "MAX_NUM_NATION_GROUPS": 128,
    "MAX_NUM_NATION_SETS": 32,
    "MAX_NUM_PLAYER_SLOTS": MAX_NUM_PLAYER_SLOTS,
    "MAX_NUM_REQS": 20,
    "MAX_NUM_RESOURCES": MAX_NUM_TERRAINS // 2,
    "MAX_NUM_RULESETS": 16,
    "MAX_RULESET_NAME_LENGTH": 64,
    "MAX_TRADE_ROUTES": 5,
    "MAX_VET_LEVELS": 20,
    "MAX_EXTRA_TYPES": MAX_EXTRA_TYPES,
    "MAX_BASE_TYPES": MAX_EXTRA_TYPES,
    "MAX_ROAD_TYPES": MAX_EXTRA_TYPES,
    "MAX_NUM_STARTPOS_NATIONS": 1024,
    "UCL_LAST": 32,
    "L_MAX": 64,
    "NUM_SS_STRUCTURALS": 32,
}

# Bit widths of each bv_* type; wire size is ceil(bits / 8) raw bytes.
BITVECTOR_BITS = {
    "bv_extras": MAX_EXTRA_TYPES,
    "bv_special": MAX_EXTRA_TYPES,
    "bv_bases": MAX_EXTRA_TYPES,
    "bv_roads": MAX_EXTRA_TYPES,
    "bv_imprs": MAX_NUM_ITEMS,
    "bv_player": MAX_NUM_PLAYER_SLOTS,
    "bv_startpos_nations": 1024,
    "bv_unit_classes": 32,
    "bv_unit_type_roles": 64,
    "bv_spaceship_structure": 32,
    "bv_base_flags": 4,
    "bv_city_options": 3,
    "bv_disaster_effects": 7,
    "bv_extra_flags": 10,
    "bv_impr_flags": 3,
    "bv_road_flags": 6,
    "bv_tech_flags": 13,
    "bv_terrain_flags": 18,
    "bv_unit_class_flags": 16,
    "bv_unit_type_flags": 67,
}

# Sentinel values terminating the *_list dataio types.
LIST_STOP = {
    "tech_list": (MAX_NUM_ITEMS, 10),      # (stop value, max entries)
    "unit_list": (MAX_NUM_ITEMS, 10),
    "building_list": (MAX_NUM_ITEMS, 10),
}

NETWORK_CAPSTRING = "+Freeciv-2.6-network techloss_forgiveness year32"
MAJOR_VERSION, MINOR_VERSION, PATCH_VERSION = 2, 6, 6


def bv_bytes(public_type):
    bits = BITVECTOR_BITS[public_type]
    return (bits - 1) // 8 + 1


def resolve_size(expr):
    """Evaluate an array-size expression like 'A_LAST+1' or 'MAX_LEN_PACKET / 3'."""
    expr = expr.strip()
    if expr.isdigit():
        return int(expr)
    return int(eval(expr, {"__builtins__": {}}, CONSTANTS))
