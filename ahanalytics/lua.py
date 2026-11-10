"""Parser für WoW-SavedVariables (Lua-Tabellenliterale).

Arbeitet auf Bytes, weil WoW Binärdaten (z. B. CBOR-Blobs von Auctionator)
roh bzw. mit Escape-Sequenzen in Lua-Strings schreibt. Strings werden daher
als ``bytes`` zurückgegeben; ``decode_strings`` wandelt sie bei Bedarf um.
"""
from __future__ import annotations


class LuaParseError(ValueError):
    pass


_ESCAPES = {
    ord("n"): b"\n", ord("t"): b"\t", ord("r"): b"\r", ord("a"): b"\a",
    ord("b"): b"\b", ord("f"): b"\f", ord("v"): b"\v", ord("\\"): b"\\",
    ord('"'): b'"', ord("'"): b"'", ord("\n"): b"\n",
}
_WS = b" \t\r\n"
_DIGITS = b"0123456789"


class _Parser:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0
        self.n = len(data)

    def error(self, msg):
        line = self.d.count(b"\n", 0, self.i) + 1
        raise LuaParseError(f"{msg} (Zeile {line}, Position {self.i})")

    def skip(self):
        d, n = self.d, self.n
        while self.i < n:
            c = d[self.i]
            if c in _WS:
                self.i += 1
            elif d.startswith(b"--", self.i):
                if d.startswith(b"--[[", self.i):
                    end = d.find(b"]]", self.i)
                    self.i = n if end < 0 else end + 2
                else:
                    end = d.find(b"\n", self.i)
                    self.i = n if end < 0 else end + 1
            else:
                break

    def peek(self):
        self.skip()
        return self.d[self.i] if self.i < self.n else None

    def expect(self, ch: bytes):
        self.skip()
        if not self.d.startswith(ch, self.i):
            self.error(f"'{ch.decode()}' erwartet")
        self.i += len(ch)

    def name(self):
        self.skip()
        start = self.i
        d = self.d
        while self.i < self.n and (chr(d[self.i]).isalnum() or d[self.i] == ord("_")):
            self.i += 1
        if start == self.i:
            self.error("Bezeichner erwartet")
        return d[start:self.i].decode("ascii")

    def string(self) -> bytes:
        d = self.d
        q = d[self.i]
        if q == ord("["):  # [[long string]] / [==[...]==]
            j = self.i + 1
            level = 0
            while d[j] == ord("="):
                level += 1
                j += 1
            if d[j] != ord("["):
                self.error("Ungültiger langer String")
            close = b"]" + b"=" * level + b"]"
            end = d.find(close, j + 1)
            if end < 0:
                self.error("Langer String nicht beendet")
            s = d[j + 1:end]
            if s.startswith(b"\n"):
                s = s[1:]
            self.i = end + len(close)
            return s
        self.i += 1
        out = bytearray()
        while True:
            # schneller Pfad: bis zum nächsten Quote/Backslash kopieren
            nq = d.find(bytes([q]), self.i)
            nb = d.find(b"\\", self.i, nq if nq >= 0 else self.n)
            if nq < 0:
                self.error("String nicht beendet")
            if nb < 0:
                out += d[self.i:nq]
                self.i = nq + 1
                return bytes(out)
            out += d[self.i:nb]
            self.i = nb + 1
            c = d[self.i]
            if c in _DIGITS:
                j = self.i
                while j < self.i + 3 and j < self.n and d[j] in _DIGITS:
                    j += 1
                val = int(d[self.i:j])
                if val > 255:
                    self.error("Ungültige Dezimal-Escape-Sequenz")
                out.append(val)
                self.i = j
            elif c == ord("x"):
                out.append(int(d[self.i + 1:self.i + 3], 16))
                self.i += 3
            elif c == ord("z"):
                self.i += 1
                while self.i < self.n and d[self.i] in _WS:
                    self.i += 1
            elif c == ord("\r"):
                out += b"\n"
                self.i += 2 if d[self.i + 1:self.i + 2] == b"\n" else 1
            elif c in _ESCAPES:
                out += _ESCAPES[c]
                self.i += 1
            else:
                self.error(f"Unbekannte Escape-Sequenz \\{chr(c)}")

    def number(self):
        d = self.d
        start = self.i
        if d[self.i] == ord("-"):
            self.i += 1
        if d.startswith(b"0x", self.i) or d.startswith(b"0X", self.i):
            self.i += 2
            while self.i < self.n and chr(d[self.i]) in "0123456789abcdefABCDEF":
                self.i += 1
            return int(d[start:self.i], 16)
        while self.i < self.n and (d[self.i] in _DIGITS or d[self.i] in b".eE" or
                                   (d[self.i] in b"+-" and d[self.i - 1] in b"eE")):
            self.i += 1
        txt = d[start:self.i].decode("ascii")
        if txt in ("-",):
            self.error("Zahl erwartet")
        if any(c in txt for c in ".eE") or txt in ("inf", "-inf", "nan"):
            return float(txt)
        return int(txt)

    def value(self):
        c = self.peek()
        if c is None:
            self.error("Wert erwartet")
        if c == ord("{"):
            return self.table()
        if c in (ord('"'), ord("'")):
            return self.string()
        if c == ord("[") and self.d[self.i + 1] in b"[=":
            return self.string()
        if c in _DIGITS or c == ord("-") or c == ord("."):
            return self.number()
        word = self.name()
        if word == "true":
            return True
        if word == "false":
            return False
        if word == "nil":
            return None
        if word in ("inf", "nan"):
            return float(word)
        self.error(f"Unerwarteter Bezeichner '{word}'")

    def table(self):
        self.expect(b"{")
        result = {}
        arr_idx = 1
        while True:
            c = self.peek()
            if c == ord("}"):
                self.i += 1
                break
            if c == ord("[") and self.d[self.i + 1] not in b"[=":
                self.i += 1
                key = self.value()
                self.expect(b"]")
                self.expect(b"=")
                result[key] = self.value()
            else:
                # name = value  oder Array-Wert
                save = self.i
                if c is not None and (chr(c).isalpha() or c == ord("_")):
                    word = self.name()
                    if self.peek() == ord("=") and self.d[self.i + 1:self.i + 2] != b"=":
                        self.i += 1
                        result[word.encode()] = self.value()
                    else:
                        self.i = save
                        result[arr_idx] = self.value()
                        arr_idx += 1
                else:
                    result[arr_idx] = self.value()
                    arr_idx += 1
            c = self.peek()
            if c in (ord(","), ord(";")):
                self.i += 1
        return result

    def file(self):
        out = {}
        while self.peek() is not None:
            name = self.name()
            self.expect(b"=")
            out[name] = self.value()
        return out


def parse_savedvariables(data: bytes) -> dict:
    """Parst eine SavedVariables-Datei: ``{globaler_name: wert}``."""
    return _Parser(data).file()


def parse_value(data: bytes):
    return _Parser(data).value()


def decode_strings(obj):
    """Wandelt alle bytes-Strings (Schlüssel und Werte) rekursiv in str um."""
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {decode_strings(k): decode_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode_strings(v) for v in obj]
    return obj
