"""Protocol self-checks: spec parses, and every packet round-trips."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fcbot.protocol import constants, pdef
from fcbot.protocol.codec import Codec
from fcbot.protocol.connection import _find_packets_def
from fcbot import chat, fcmap


class SpecTest(unittest.TestCase):
    def setUp(self):
        self.by_number, self.by_name = pdef.parse(_find_packets_def())

    def test_all_packets_parse(self):
        self.assertEqual(len(self.by_number), 185)

    def test_every_size_resolves(self):
        for packet in self.by_number.values():
            for field in packet.fields:
                for full, _transfer in field.sizes:
                    constants.resolve_size(full)
                if field.dataio_type == "bitvector":
                    constants.bv_bytes(field.public_type)

    def test_zero_field_packets_have_no_delta(self):
        for packet in self.by_number.values():
            if not packet.fields:
                self.assertFalse(packet.delta, packet.name)


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.by_number, _ = pdef.parse(_find_packets_def())
        self.caps = {"year32", "techloss_forgiveness"}

    def test_blank_packets_round_trip(self):
        for number, packet in sorted(self.by_number.items()):
            enc = Codec(self.by_number, self.caps)
            dec = Codec(self.by_number, self.caps)
            blank = enc.blank(packet)
            body = enc.encode(number, blank)
            if body is None:        # is-info packet with nothing to say
                continue
            got = dec.decode(number, body)
            self.assertEqual(got, blank, "%s did not round-trip" % packet.name)

    def test_delta_only_sends_changes(self):
        """A second identical is-info packet is suppressed by the delta cache."""
        codec = Codec(self.by_number, self.caps)
        number = next(n for n, p in self.by_number.items()
                      if p.name == "PACKET_TILE_INFO")
        packet = self.by_number[number]
        values = codec.blank(packet)
        values["tile"] = 42
        first = codec.encode(number, values)
        self.assertIsNotNone(first)
        self.assertIsNone(codec.encode(number, values))

    def test_capability_changes_field_layout(self):
        """year32 swaps which year field is on the wire (and its width)."""
        number = next(n for n, p in self.by_number.items()
                      if p.name == "PACKET_NEW_YEAR")
        with_cap = Codec(self.by_number, {"year32"})
        without = Codec(self.by_number, set())
        names_with = {f.name for f in with_cap._fields_of(self.by_number[number])}
        names_without = {f.name for f in without._fields_of(self.by_number[number])}
        self.assertIn("year32", names_with)
        self.assertNotIn("year16", names_with)
        self.assertIn("year16", names_without)
        self.assertNotIn("year32", names_without)


class TopologyTest(unittest.TestCase):
    def test_square_roundtrip(self):
        topo = fcmap.Topology(20, 10, fcmap.TF_WRAPX)
        for index in range(topo.size()):
            self.assertEqual(topo.map_to_index(*topo.index_to_map(index)), index)

    def test_isometric_roundtrip(self):
        topo = fcmap.Topology(36, 48, fcmap.TF_WRAPX | fcmap.TF_ISO)
        for index in range(topo.size()):
            self.assertEqual(topo.map_to_index(*topo.index_to_map(index)), index)

    def test_steps_are_reversible(self):
        topo = fcmap.Topology(36, 48, fcmap.TF_WRAPX | fcmap.TF_ISO)
        for index in (0, 100, 871, 1727):
            for direction, neighbour in topo.neighbours(index):
                back = topo.step(neighbour, fcmap.DIR_REVERSE[direction])
                self.assertEqual(back, index)

    def test_direction_to_matches_step(self):
        topo = fcmap.Topology(36, 48, fcmap.TF_WRAPX | fcmap.TF_ISO)
        for _, neighbour in topo.neighbours(500):
            d = topo.direction_to(500, neighbour)
            self.assertIsNotNone(d)
            self.assertEqual(topo.step(500, d), neighbour)


class ChatTest(unittest.TestCase):
    def packet(self, message, event=chat.E_CHAT_MSG, conn_id=2):
        return {"message": message, "conn_id": conn_id, "turn": 7,
                "event": event, "tile": -1}

    def test_player_chat_is_attributed(self):
        m = chat.from_packet(
            self.packet('[c fg="#ffffff"]<Perikles> Hope you like tundra.[/c]'),
            {2: {"username": "bdunbar"}})
        self.assertTrue(m.is_chat)
        self.assertEqual(m.speaker, "Perikles")
        self.assertEqual(m.sender, "bdunbar")
        self.assertEqual(m.text, "Hope you like tundra.")

    def test_notifications_are_not_chat(self):
        m = chat.from_packet(self.packet("Your city Roma has grown.", event=1),
                             {})
        self.assertFalse(m.is_chat)
        self.assertIsNone(m.speaker)
        self.assertEqual(m.event_name, "E_CITY_LOST")

    def test_unknown_sender_is_tolerated(self):
        m = chat.from_packet(self.packet("Game started.", conn_id=-1), {})
        self.assertIsNone(m.sender)

    def test_event_table_is_populated(self):
        self.assertGreater(len(chat.EVENT_NAMES), 100)
        self.assertEqual(chat.EVENT_NAMES[chat.E_CHAT_MSG], "E_CHAT_MSG")


if __name__ == "__main__":
    unittest.main()
