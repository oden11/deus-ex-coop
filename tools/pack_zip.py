"""Pack a directory into the smallest plain zip we can make (used by make_release.sh).
    python tools/pack_zip.py <dir> <out.zip>

Files go in as standard Deflate (or stored when that is smaller), so the zip still opens with
Windows Explorer, Expand-Archive and any unzip tool. Each file is compressed several ways and the
smallest result is kept. With the zopfli module (pip install zopfli) Deflate gets a few percent
smaller than zlib's best; without it only zlib's levels are tried. ZOPFLI_ITERATIONS (default 50)
trades time for size. The zip is read back and compared with the source files before the script exits.
"""
import os
import struct
import sys
import time
import zipfile
import zlib

try:
    import zopfli.zlib as zopfli_zlib
except ImportError:
    zopfli_zlib = None

STORED, DEFLATED = 0, 8
ITERATIONS = int(os.environ.get("ZOPFLI_ITERATIONS", "50"))


def zlib_deflate(data, level, mem_level, strategy):
    c = zlib.compressobj(level, zlib.DEFLATED, -15, mem_level, strategy)
    return c.compress(data) + c.flush()


def smallest(data):
    """Returns (method, payload) with the fewest bytes."""
    outs = []
    # Level 9 is not always the smallest (an exe here packed better at 6), and zlib is fast, so try them all.
    for level in range(1, 10):
        for mem_level in (8, 9):
            for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED):
                outs.append(zlib_deflate(data, level, mem_level, strategy))
    if zopfli_zlib:
        # zopfli returns a zlib stream: drop the 2 byte header and 4 byte Adler-32 to get raw Deflate.
        outs.append(zopfli_zlib.compress(data, numiterations=ITERATIONS)[2:-4])
    out = min(outs, key=len)
    return (DEFLATED, out) if len(out) < len(data) else (STORED, data)


def dos_time(mtime):
    t = time.localtime(max(mtime, 315532800))  # zip dates start in 1980
    return (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2), ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday


def list_files(root):
    found = []
    for d, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            path = os.path.join(d, n)
            found.append((os.path.relpath(path, root).replace(os.sep, "/"), path))
    return found


def pack(src, dst):
    files = list_files(src)
    if not files:
        sys.exit("pack_zip: no files in " + src)
    if len(files) >= 0xFFFF:
        sys.exit("pack_zip: too many files for a plain zip")
    central, raw_total = [], 0
    with open(dst, "wb") as out:
        for name, path in files:
            with open(path, "rb") as f:
                data = f.read()
            if len(data) >= 0xFFFFFFFF:
                sys.exit("pack_zip: %s is too big for a plain zip" % name)
            method, payload = smallest(data)
            crc = zlib.crc32(data)
            tm, dt = dos_time(os.path.getmtime(path))
            flags = 0 if name.isascii() else 0x800  # bit 11: name is UTF-8
            need = 20 if method == DEFLATED else 10
            encoded = name.encode("utf-8")
            offset = out.tell()
            out.write(struct.pack("<4s5H3L2H", b"PK\x03\x04", need, flags, method, tm, dt,
                                  crc, len(payload), len(data), len(encoded), 0))
            out.write(encoded)
            out.write(payload)
            central.append(struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 20, need, flags, method, tm, dt,
                                       crc, len(payload), len(data), len(encoded), 0, 0, 0, 0, 0, offset) + encoded)
            raw_total += len(data)
            print("  %-40s %9d -> %9d %s" % (name, len(data), len(payload), "stored" if method == STORED else "deflate"))
        cd_offset = out.tell()
        for entry in central:
            out.write(entry)
        cd_size = out.tell() - cd_offset
        if out.tell() >= 0xFFFFFFFF:
            sys.exit("pack_zip: zip is too big for a plain zip")
        out.write(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, len(central), len(central), cd_size, cd_offset, 0))
    verify(dst, files)
    print("%d files, %d bytes -> %d byte zip%s" % (len(files), raw_total, os.path.getsize(dst),
                                                     "" if zopfli_zlib else " (zlib only)"))
    if not zopfli_zlib:
        print("pack_zip: install zopfli (pip install zopfli) for a smaller zip", file=sys.stderr)


def verify(dst, files):
    with zipfile.ZipFile(dst) as z:
        if z.namelist() != [name for name, _ in files]:
            sys.exit("pack_zip: zip entries do not match the source files")
        for name, path in files:
            with open(path, "rb") as f:
                if z.read(name) != f.read():  # read() also checks the CRC
                    sys.exit("pack_zip: %s does not match after packing" % name)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pack(sys.argv[1], sys.argv[2])
