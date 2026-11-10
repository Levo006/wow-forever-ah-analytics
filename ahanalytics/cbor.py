"""Minimaler CBOR-Decoder (RFC 8949) ohne externe Abhängigkeiten.

Auctionator speichert die Preisdatenbank pro Realm als CBOR-Blob
(C_EncodingUtil.SerializeCBOR bzw. LibCBOR). Wir brauchen nur Decoding.
"""
from __future__ import annotations

import struct


class CBORError(ValueError):
    pass


_BREAK = object()


class _Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise CBORError("Unerwartetes Ende der CBOR-Daten")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise CBORError("Unerwartetes Ende der CBOR-Daten")
        b = self.data[self.pos]
        self.pos += 1
        return b


def _arg(r: _Reader, info: int):
    if info < 24:
        return info
    if info == 24:
        return r.byte()
    if info == 25:
        return struct.unpack(">H", r.take(2))[0]
    if info == 26:
        return struct.unpack(">I", r.take(4))[0]
    if info == 27:
        return struct.unpack(">Q", r.take(8))[0]
    if info == 31:
        return None  # indefinite length
    raise CBORError(f"Ungültige Zusatzinfo {info}")


def _half(b: bytes) -> float:
    h = struct.unpack(">H", b)[0]
    exp = (h >> 10) & 0x1F
    mant = h & 0x3FF
    if exp == 0:
        val = mant * 2 ** -24
    elif exp == 31:
        val = float("inf") if mant == 0 else float("nan")
    else:
        val = (mant + 1024) * 2 ** (exp - 25)
    return -val if h & 0x8000 else val


def _decode(r: _Reader):
    ib = r.byte()
    major, info = ib >> 5, ib & 0x1F
    if major == 0:
        return _arg(r, info)
    if major == 1:
        return -1 - _arg(r, info)
    if major in (2, 3):
        n = _arg(r, info)
        if n is None:
            parts = []
            while True:
                if r.data[r.pos] == 0xFF:
                    r.pos += 1
                    break
                parts.append(_decode(r))
            raw = b"".join(p if isinstance(p, bytes) else p.encode("utf-8") for p in parts)
        else:
            raw = r.take(n)
        if major == 3:
            return raw.decode("utf-8", errors="replace")
        return raw
    if major == 4:
        n = _arg(r, info)
        out = []
        if n is None:
            while True:
                v = _decode(r)
                if v is _BREAK:
                    break
                out.append(v)
        else:
            for _ in range(n):
                out.append(_decode(r))
        return out
    if major == 5:
        n = _arg(r, info)
        out = {}
        if n is None:
            while True:
                k = _decode(r)
                if k is _BREAK:
                    break
                out[_hashable(k)] = _decode(r)
        else:
            for _ in range(n):
                k = _decode(r)
                out[_hashable(k)] = _decode(r)
        return out
    if major == 6:  # Tag: ignorieren, Inhalt zurückgeben
        _arg(r, info)
        return _decode(r)
    # major 7
    if info == 20:
        return False
    if info == 21:
        return True
    if info in (22, 23):
        return None
    if info == 25:
        return _half(r.take(2))
    if info == 26:
        return struct.unpack(">f", r.take(4))[0]
    if info == 27:
        return struct.unpack(">d", r.take(8))[0]
    if info == 31:
        return _BREAK
    if info < 24:
        return None
    if info == 24:
        r.byte()
        return None
    raise CBORError(f"Unbekannter Simple-Wert {info}")


def _hashable(k):
    if isinstance(k, list):
        return tuple(k)
    if isinstance(k, dict):
        return tuple(sorted(k.items()))
    return k


def loads(data: bytes):
    r = _Reader(data)
    v = _decode(r)
    if v is _BREAK:
        raise CBORError("Unerwartetes BREAK")
    return v


def dumps(obj) -> bytes:
    """Kleiner Encoder – nur für Tests/Fixtures."""
    out = bytearray()

    def head(major, n):
        if n < 24:
            out.append((major << 5) | n)
        elif n < 256:
            out.extend([(major << 5) | 24, n])
        elif n < 65536:
            out.append((major << 5) | 25)
            out.extend(struct.pack(">H", n))
        elif n < 2 ** 32:
            out.append((major << 5) | 26)
            out.extend(struct.pack(">I", n))
        else:
            out.append((major << 5) | 27)
            out.extend(struct.pack(">Q", n))

    def enc(o):
        if o is True:
            out.append(0xF5)
        elif o is False:
            out.append(0xF4)
        elif o is None:
            out.append(0xF6)
        elif isinstance(o, int):
            if o >= 0:
                head(0, o)
            else:
                head(1, -1 - o)
        elif isinstance(o, float):
            out.append(0xFB)
            out.extend(struct.pack(">d", o))
        elif isinstance(o, str):
            b = o.encode("utf-8")
            head(3, len(b))
            out.extend(b)
        elif isinstance(o, bytes):
            head(2, len(o))
            out.extend(o)
        elif isinstance(o, (list, tuple)):
            head(4, len(o))
            for v in o:
                enc(v)
        elif isinstance(o, dict):
            head(5, len(o))
            for k, v in o.items():
                enc(k)
                enc(v)
        else:
            raise TypeError(type(o))

    enc(obj)
    return bytes(out)
