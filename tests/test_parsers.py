"""Tests für Lua-SavedVariables-Parser und CBOR-Decoder."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import cbor, lua  # noqa: E402


class LuaParserTest(unittest.TestCase):
    def test_basic_table(self):
        src = b'''
FOO = {
["a"] = 1,
["b"] = -2.5,
["c"] = true,
["d"] = nil,
["nested"] = { 1, 2, "drei", },
[10] = "zehn",
plain = "x",
}
BAR = "hallo"
'''
        r = lua.decode_strings(lua.parse_savedvariables(src))
        self.assertEqual(r["BAR"], "hallo")
        foo = r["FOO"]
        self.assertEqual(foo["a"], 1)
        self.assertEqual(foo["b"], -2.5)
        self.assertIs(foo["c"], True)
        self.assertIsNone(foo["d"])
        self.assertEqual(foo["nested"], {1: 1, 2: 2, 3: "drei"})
        self.assertEqual(foo[10], "zehn")
        self.assertEqual(foo["plain"], "x")

    def test_escapes_and_binary(self):
        src = b'X = "a\\"b\\\\c\\n\\0\\255\\0010\\x41"'
        v = lua.parse_savedvariables(src)["X"]
        self.assertEqual(v, b'a"b\\c\n\x00\xff\x010A')

    def test_raw_binary_bytes(self):
        payload = bytes(range(1, 256)).replace(b'"', b"").replace(b"\\", b"")
        src = b'X = "' + payload + b'"'
        self.assertEqual(lua.parse_savedvariables(src)["X"], payload)

    def test_long_string_and_comments(self):
        src = b'-- Kommentar\nX = [[zeile1\nzeile2]] --[[ block ]]\nY = [==[a]]b]==]'
        r = lua.parse_savedvariables(src)
        self.assertEqual(r["X"], b"zeile1\nzeile2")
        self.assertEqual(r["Y"], b"a]]b")

    def test_umlaut(self):
        r = lua.decode_strings(lua.parse_savedvariables('X = "Königsblut"'.encode()))
        self.assertEqual(r["X"], "Königsblut")

    def test_error_has_position(self):
        with self.assertRaises(lua.LuaParseError):
            lua.parse_savedvariables(b"X = {")


class CborTest(unittest.TestCase):
    def test_roundtrip(self):
        obj = {"a": [1, 2, 3], "b": {"x": -5, "y": 2 ** 40}, "c": 1.5, "d": True, "e": None,
               "f": b"\x00\x01", "g": "Thoriumerz"}
        self.assertEqual(cbor.loads(cbor.dumps(obj)), obj)

    def test_indefinite_and_half_float(self):
        # {_ "a": [_ 1, 2], "h": 1.5 (half)}
        data = bytes([0xBF, 0x61, ord("a"), 0x9F, 0x01, 0x02, 0xFF,
                      0x61, ord("h"), 0xF9, 0x3E, 0x00, 0xFF])
        self.assertEqual(cbor.loads(data), {"a": [1, 2], "h": 1.5})

    def test_half_special(self):
        self.assertTrue(math.isinf(cbor.loads(bytes([0xF9, 0x7C, 0x00]))))

    def test_truncated(self):
        with self.assertRaises(cbor.CBORError):
            cbor.loads(bytes([0x83, 0x01]))


if __name__ == "__main__":
    unittest.main()
