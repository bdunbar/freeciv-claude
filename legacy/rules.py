"""Read the pieces of the ruleset the agent needs (resource identifiers)."""

import os
import re

RULESET_DIR = '/usr/share/games/freeciv'


def resource_identifiers(ruleset='classic'):
    """Map savegame resource identifier chars -> resource names."""
    path = os.path.join(RULESET_DIR, ruleset, 'terrain.ruleset')
    out = {}
    name = None
    with open(path, encoding='utf-8', errors='replace') as f:
        in_res = False
        for line in f:
            s = line.strip()
            if s.startswith('['):
                in_res = s.startswith('[resource_')
                name = None
                continue
            if not in_res:
                continue
            m = re.match(r'name\s*=\s*_?\("?(.*?)"?\)', s)
            if m:
                name = re.sub(r'^\?.*?:', '', m.group(1))
                continue
            m = re.match(r'identifier\s*=\s*"(.*)"', s)
            if m and name:
                out[m.group(1)] = name
    return out
