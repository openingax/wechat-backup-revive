#!/usr/bin/env python3
"""Make an old WeChat 4.x "backup & restore" package visible again to WeChat for Mac.

Works only on your own backups. Never edits the original: `stage` makes an APFS
clone (`cp -c`) and every later step touches only that clone.

Subcommands
  inspect  PKG_OR_USER_DIR          decode pkg_info.dat, storage type, unique id, key indices
  stage    OLD_USER_DIR ROOT ACCT   clone OLD_USER_DIR into ROOT/xwechat_backup/ACCT/<same hash>/
  borrow   USER_DIR OLD_ID NEW_ID HOLD
                                    park files/NEW_ID in HOLD, move old files/OLD_ID into its
                                    slot and give it NEW_ID's server-known unique id
  mark     DIR                      set every file's atime to 2000-01-01 (read tracker)
  progress DIR                      show which session dirs / bytes were read since `mark`
  selftest                          run the protobuf round-trip check
"""
from __future__ import annotations

import collections
import os
import shutil
import subprocess
import sys
import time

# ---------- minimal protobuf wire format (no deps) ----------

def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]; i += 1
        r |= (c & 0x7F) << s; s += 7
        if c < 0x80:
            return r, i


def _enc_varint(v: int) -> bytes:
    out = bytearray()
    while True:
        c = v & 0x7F; v >>= 7
        if v:
            out.append(c | 0x80)
        else:
            out.append(c)
            return bytes(out)


def pb_fields(b: bytes) -> list[tuple[int, int, bytes, object]]:
    """[(field, wiretype, raw_bytes_including_key, value)] for varint / length-delimited."""
    out, i = [], 0
    while i < len(b):
        start = i
        k, i = _varint(b, i)
        f, t = k >> 3, k & 7
        if t == 0:
            v, i = _varint(b, i)
        elif t == 2:
            n, i = _varint(b, i)
            v = b[i:i + n]; i += n
        else:
            raise ValueError(f"unsupported wire type {t}")
        out.append((f, t, b[start:i], v))
    return out


def pb_varint_field(f: int, v: int) -> bytes:
    return _enc_varint(f << 3) + _enc_varint(v)


def pb_bytes_field(f: int, v: bytes) -> bytes:
    return _enc_varint(f << 3 | 2) + _enc_varint(len(v)) + v


def pb_dump(b: bytes, ind: int = 0) -> None:
    for f, t, _, v in pb_fields(b):
        pad = " " * ind
        if t == 0:
            print(f"{pad}{f}: {v}")
            continue
        try:
            s = v.decode()
            if s.isprintable():
                print(f'{pad}{f}: "{s}"'); continue
        except UnicodeDecodeError:
            pass
        try:
            print(f"{pad}{f}: {{"); pb_dump(v, ind + 2); print(f"{pad}}}")
        except (ValueError, IndexError):
            print(f"{pad}  raw {v.hex()}"); print(f"{pad}}}")

# ---------- pkg_info.dat ----------
# 1 package number (must equal the files/<N> dir name)
# 2 kind/state        3 display name        4 storage device {1 type, 2 id, 3 path, 4 name}
#   type 4011 = "this computer", 13001 = external storage written by the phone
# 6 backup range      7 ?                   8 unique_package_id (checked against the server)
# 10 {1 total bytes, 2 last backup unix time}

ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 10]


def read_pkg_info(path: str) -> dict[int, bytes]:
    with open(path, "rb") as h:
        return {f: raw for f, _, raw, _ in pb_fields(h.read())}


def write_pkg_info(path: str, fields: dict[int, bytes]) -> None:
    extra = sorted(set(fields) - set(ORDER))
    with open(path, "wb") as h:  # new file content; on an APFS clone this never touches the original
        h.write(b"".join(fields[f] for f in ORDER + extra if f in fields))


def field_value(fields: dict[int, bytes], f: int):
    return pb_fields(fields[f])[0][3] if f in fields else None


def borrow_fields(old: dict[int, bytes], new: dict[int, bytes], new_id: int) -> dict[int, bytes]:
    """Old package content, but numbered/registered like the new, server-known package."""
    out = dict(old)
    out[1] = pb_varint_field(1, new_id)
    for f in (2, 4, 5, 8):  # kind, storage device = this Mac, source marker, unique id
        if f in new:
            out[f] = new[f]
    # keep old 6 (range), 7, 10 (size/time) so the listing shows the old backup's size and date
    return out

# ---------- RMFH (encrypted) files ----------

def rmfh_key_index(path: str) -> int | None:
    with open(path, "rb") as h:
        head = h.read(12)
    return head[11] if head[:4] == b"RMFH" and len(head) == 12 else None

# ---------- commands ----------

def cmd_inspect(target: str) -> None:
    pkgs = [target] if os.path.exists(os.path.join(target, "pkg_info.dat")) else [
        os.path.join(target, "files", d) for d in sorted(os.listdir(os.path.join(target, "files")))
        if os.path.isdir(os.path.join(target, "files", d))]
    alt = os.path.join(target, "alt_name.dat")
    if not pkgs[0] == target:
        print(f"alt_name.dat: {'present' if os.path.exists(alt) else 'MISSING (WeChat 4.1 skips this dir)'}")
    for p in pkgs:
        print(f"\n== {p}")
        info = os.path.join(p, "pkg_info.dat")
        if not os.path.exists(info):
            print("no pkg_info.dat"); continue
        with open(info, "rb") as h:
            pb_dump(h.read())
        idx = collections.Counter()
        for root, _, files in os.walk(p):
            for fn in files:
                k = rmfh_key_index(os.path.join(root, fn))
                if k is not None:
                    idx[k] += 1
        print("RMFH key index histogram:", dict(sorted(idx.items())))


def cmd_stage(old_user_dir: str, root: str, acct: str) -> None:
    dst_parent = os.path.join(root, "xwechat_backup", acct)
    dst = os.path.join(dst_parent, os.path.basename(os.path.normpath(old_user_dir)))
    if os.path.exists(dst):
        sys.exit(f"refusing to overwrite {dst}")
    os.makedirs(dst_parent, exist_ok=True)
    subprocess.run(["cp", "-cR", old_user_dir, dst], check=True)  # APFS clone, instant, no extra space
    print(f"cloned to {dst}")


def cmd_borrow(user_dir: str, old_id: str, new_id: str, hold: str) -> None:
    files = os.path.join(user_dir, "files")
    old_dir, new_dir = os.path.join(files, old_id), os.path.join(files, new_id)
    for d in (old_dir, new_dir):
        if not os.path.isdir(d):
            sys.exit(f"missing {d}")
    os.makedirs(hold, exist_ok=False)
    parked = os.path.join(hold, new_id)
    shutil.move(new_dir, parked)
    shutil.move(old_dir, new_dir)
    merged = borrow_fields(read_pkg_info(os.path.join(new_dir, "pkg_info.dat")),
                           read_pkg_info(os.path.join(parked, "pkg_info.dat")), int(new_id))
    write_pkg_info(os.path.join(new_dir, "pkg_info.dat"), merged)
    print(f"old package now at {new_dir}; real package {new_id} parked at {parked}")


def cmd_mark(d: str) -> None:
    t = time.mktime((2000, 1, 1, 0, 0, 0, 0, 0, -1))
    n = 0
    for root, _, files in os.walk(d):
        for fn in files:
            p = os.path.join(root, fn)
            os.utime(p, (t, os.stat(p).st_mtime)); n += 1
    print(f"marked {n} files; macOS updates atime on the next read (atime <= mtime)")


def cmd_progress(d: str) -> None:
    cutoff = time.mktime((2001, 1, 1, 0, 0, 0, 0, 0, -1))
    read_b = total_b = 0
    dirs, read_dirs = set(), set()
    for root, _, files in os.walk(d):
        rel = os.path.relpath(root, d).split(os.sep)[0]
        for fn in files:
            if fn == ".DS_Store":
                continue
            st = os.stat(os.path.join(root, fn))
            total_b += st.st_size
            if rel != ".":
                dirs.add(rel)
            if st.st_atime > cutoff:
                read_b += st.st_size
                if rel != ".":
                    read_dirs.add(rel)
    print(f"session dirs read {len(read_dirs)}/{len(dirs)}, bytes {read_b/1e9:.2f}/{total_b/1e9:.2f} GB")


def selftest() -> None:
    old = (pb_varint_field(1, 2) + pb_varint_field(2, 11) + pb_bytes_field(3, "外部".encode())
           + pb_bytes_field(4, pb_varint_field(1, 13001)) + pb_bytes_field(8, b"OLDID")
           + pb_bytes_field(10, pb_varint_field(1, 37_000_000_000)))
    new = (pb_varint_field(1, 5) + pb_varint_field(2, 2) + pb_bytes_field(4, pb_varint_field(1, 4011))
           + pb_bytes_field(5, b"source_device_id") + pb_bytes_field(8, b"NEWID"))
    o = {f: raw for f, _, raw, _ in pb_fields(old)}
    n = {f: raw for f, _, raw, _ in pb_fields(new)}
    m = borrow_fields(o, n, 5)
    got = {f: v for f, _, _, v in pb_fields(b"".join(m[f] for f in ORDER if f in m))}
    assert got[1] == 5 and got[2] == 2 and got[8] == b"NEWID"
    assert pb_fields(got[4])[0][3] == 4011            # registered as "this computer"
    assert pb_fields(got[10])[0][3] == 37_000_000_000  # old size kept
    assert got[3] == "外部".encode()                    # old display name kept
    assert _varint(_enc_varint(300), 0) == (300, 2)
    print("selftest ok")


def main() -> None:
    a = sys.argv[1:]
    cmds = {"inspect": (cmd_inspect, 1), "stage": (cmd_stage, 3), "borrow": (cmd_borrow, 4),
            "mark": (cmd_mark, 1), "progress": (cmd_progress, 1), "selftest": (selftest, 0)}
    if not a or a[0] not in cmds or len(a) - 1 != cmds[a[0]][1]:
        sys.exit(__doc__)
    cmds[a[0]][0](*a[1:])


if __name__ == "__main__":
    main()
