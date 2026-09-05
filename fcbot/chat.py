"""Chat and server events.

Freeciv sends chat and every in-game notification down the same packet
(PACKET_CHAT_MSG), distinguished by an event type. We separate the two:
messages a *person* typed are banter to respond to; the rest are the game
telling us what happened.
"""

import re

# Freeciv wraps text in its own colour markup: [c fg="#006400"]...[/c]
_MARKUP = re.compile(r"\[/?c[^\]]*\]")
# Players speak as "<Leader> text"; the leader name is who to answer.
_SPEAKER = re.compile(r"^<([^>]{1,48})>\s*(.*)$", re.S)

E_CHAT_MSG = 95
E_CHAT_ERROR = 97

# Event types that carry human conversation rather than game notifications.
CHAT_EVENTS = frozenset([E_CHAT_MSG, E_CHAT_ERROR])

EVENT_NAMES = {
    0: "E_CITY_CANTBUILD",
    1: "E_CITY_LOST",
    2: "E_CITY_LOVE",
    3: "E_CITY_DISORDER",
    4: "E_CITY_FAMINE",
    5: "E_CITY_FAMINE_FEARED",
    6: "E_CITY_GROWTH",
    7: "E_CITY_MAY_SOON_GROW",
    8: "E_CITY_AQUEDUCT",
    9: "E_CITY_AQ_BUILDING",
    10: "E_CITY_NORMAL",
    11: "E_CITY_NUKED",
    12: "E_CITY_CMA_RELEASE",
    13: "E_CITY_GRAN_THROTTLE",
    14: "E_CITY_TRANSFER",
    15: "E_CITY_BUILD",
    16: "E_CITY_PRODUCTION_CHANGED",
    17: "E_WORKLIST",
    18: "E_UPRISING",
    19: "E_CIVIL_WAR",
    20: "E_ANARCHY",
    21: "E_FIRST_CONTACT",
    22: "E_NEW_GOVERNMENT",
    23: "E_LOW_ON_FUNDS",
    24: "E_POLLUTION",
    25: "E_REVOLT_DONE",
    26: "E_REVOLT_START",
    27: "E_SPACESHIP",
    28: "E_MY_DIPLOMAT_BRIBE",
    29: "E_DIPLOMATIC_INCIDENT",
    30: "E_MY_DIPLOMAT_ESCAPE",
    31: "E_MY_DIPLOMAT_EMBASSY",
    32: "E_MY_DIPLOMAT_FAILED",
    33: "E_MY_DIPLOMAT_INCITE",
    34: "E_MY_DIPLOMAT_POISON",
    35: "E_MY_DIPLOMAT_SABOTAGE",
    36: "E_MY_DIPLOMAT_THEFT",
    37: "E_ENEMY_DIPLOMAT_BRIBE",
    38: "E_ENEMY_DIPLOMAT_EMBASSY",
    39: "E_ENEMY_DIPLOMAT_FAILED",
    40: "E_ENEMY_DIPLOMAT_INCITE",
    41: "E_ENEMY_DIPLOMAT_POISON",
    42: "E_ENEMY_DIPLOMAT_SABOTAGE",
    43: "E_ENEMY_DIPLOMAT_THEFT",
    44: "E_CARAVAN_ACTION",
    45: "E_SCRIPT",
    46: "E_BROADCAST_REPORT",
    47: "E_GAME_END",
    48: "E_GAME_START",
    49: "E_NATION_SELECTED",
    50: "E_DESTROYED",
    51: "E_REPORT",
    52: "E_TURN_BELL",
    53: "E_NEXT_YEAR",
    54: "E_GLOBAL_ECO",
    55: "E_NUKE",
    56: "E_HUT_BARB",
    57: "E_HUT_CITY",
    58: "E_HUT_GOLD",
    59: "E_HUT_BARB_KILLED",
    60: "E_HUT_MERC",
    61: "E_HUT_SETTLER",
    62: "E_HUT_TECH",
    63: "E_HUT_BARB_CITY_NEAR",
    64: "E_IMP_BUY",
    65: "E_IMP_BUILD",
    66: "E_IMP_AUCTIONED",
    67: "E_IMP_AUTO",
    68: "E_IMP_SOLD",
    69: "E_TECH_GAIN",
    70: "E_TECH_LEARNED",
    71: "E_TREATY_ALLIANCE",
    72: "E_TREATY_BROKEN",
    73: "E_TREATY_CEASEFIRE",
    74: "E_TREATY_PEACE",
    75: "E_TREATY_SHARED_VISION",
    76: "E_UNIT_LOST_ATT",
    77: "E_UNIT_WIN_ATT",
    78: "E_UNIT_BUY",
    79: "E_UNIT_BUILT",
    80: "E_UNIT_LOST_DEF",
    81: "E_UNIT_WIN",
    82: "E_UNIT_BECAME_VET",
    83: "E_UNIT_UPGRADED",
    84: "E_UNIT_RELOCATED",
    85: "E_UNIT_ORDERS",
    86: "E_WONDER_BUILD",
    87: "E_WONDER_OBSOLETE",
    88: "E_WONDER_STARTED",
    89: "E_WONDER_STOPPED",
    90: "E_WONDER_WILL_BE_BUILT",
    91: "E_DIPLOMACY",
    92: "E_TREATY_EMBASSY",
    93: "E_BAD_COMMAND",
    94: "E_SETTING",
    95: "E_CHAT_MSG",
    96: "E_MESSAGE_WALL",
    97: "E_CHAT_ERROR",
    98: "E_CONNECTION",
    99: "E_AI_DEBUG",
    100: "E_LOG_ERROR",
    101: "E_LOG_FATAL",
    102: "E_TECH_GOAL",
    103: "E_UNIT_LOST_MISC",
    104: "E_CITY_PLAGUE",
    105: "E_VOTE_NEW",
    106: "E_VOTE_RESOLVED",
    107: "E_VOTE_ABORTED",
    108: "E_CITY_RADIUS_SQ",
    109: "E_UNIT_BUILT_POP_COST",
    110: "E_DISASTER",
    111: "E_ACHIEVEMENT",
    112: "E_TECH_LOST",
    113: "E_TECH_EMBASSY",
    114: "E_MY_SPY_STEAL_GOLD",
    115: "E_ENEMY_SPY_STEAL_GOLD",
    116: "E_UNIT_ILLEGAL_ACTION",
    117: "E_DEPRECATION_WARNING",
}


def strip_markup(text):
    return _MARKUP.sub("", text).strip()


class Message(object):
    __slots__ = ("text", "sender", "speaker", "conn_id", "turn", "event",
                 "tile", "is_chat")

    def __init__(self, text, sender, conn_id, turn, event, tile, speaker=None):
        self.text = text
        self.sender = sender
        self.speaker = speaker
        self.conn_id = conn_id
        self.turn = turn
        self.event = event
        self.tile = tile
        self.is_chat = event in CHAT_EVENTS

    @property
    def event_name(self):
        return EVENT_NAMES.get(self.event, "E_%d" % self.event)

    def __repr__(self):
        who = self.speaker or self.sender or self.event_name
        return "<%s T%s %s: %s>" % ("chat" if self.is_chat else "event",
                                    self.turn, who, self.text)


def from_packet(values, conns, my_conn_id=None):
    """Build a Message from a PACKET_CHAT_MSG, naming the sender if we can."""
    conn_id = values.get("conn_id")
    sender = None
    # conn_id is -1 (or unknown) for messages the server itself generated.
    conn = conns.get(conn_id) if conn_id is not None else None
    if conn is not None:
        sender = conn.get("username") or None
    text = strip_markup(values.get("message", ""))
    speaker = None
    match = _SPEAKER.match(text)
    if match:
        speaker, text = match.group(1), match.group(2)
    return Message(text=text,
                   sender=sender,
                   speaker=speaker,
                   conn_id=conn_id,
                   turn=values.get("turn", 0),
                   event=values.get("event", -1),
                   tile=values.get("tile", -1))
