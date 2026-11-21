#!/usr/bin/env python3
"""Make an empty APFS Data container for the payload's Data seed.

The payload needs a Data disk, and the upstream path assumes one already exists: seed-data-grow.py grows a
container with `diskutil apfs resizeContainer`, which needs a container to grow, and the only seed this
repository ever had was the restore ramdisk, which is not the Data volume. This produces the base.

Two hosts, because the tools differ:

  macOS (default)   `hdiutil create -fs APFS` formats a fresh container in one step, then `hdiutil convert
                    -format UDRO` writes the plain bytes out. Nothing is mounted, so no volume is
                    disturbed.
  Linux (--mkapfs)  `mkapfs` from apfsprogs creates the container at the exact size. It needs
                    <linux/fs.h>, so it is a Linux-only option.

The packed seed keeps only nonzero pages, so an empty container of many GiB travels small; that is also
why it fits the 256 MiB DBRDISK1 limit DarwinBoot accepts.

Usage:
    python3 make-data-seed.py --gib 21 --output data.dbr [--report FILE] [--keep-raw FILE]
    python3 make-data-seed.py --gib 21 --output data.dbr --mkapfs
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

GIB = 1024 ** 3
MIB = 1024 ** 2
PAGE = 4096
LABEL = 'Data'
HERE = Path(__file__).resolve().parent


def load_packer():
    """Use the repository's own packer, so the seed is the DBRDISK1 form the payload already ships."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('pack_data_ramdisk', HERE / 'pack-data-ramdisk.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*command, ok_codes=(0,)):
    # Echo the command first: when a build fails on the runner the log is the only evidence, and the
    # previous failure reported only "hdiutil failed" without saying which invocation.
    print('+', ' '.join(str(x) for x in command), flush=True)
    result = subprocess.run([str(x) for x in command], capture_output=True, text=True)
    if result.returncode not in ok_codes:
        raise SystemExit(f'{command[0]} failed ({result.returncode}):\n'
                         f'  command: {" ".join(str(x) for x in command)}\n'
                         f'  stdout: {result.stdout.strip()}\n'
                         f'  stderr: {result.stderr.strip()}')
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)
    return result.stdout


def make_with_hdiutil(raw: Path, size: int) -> str:
    """Format a fresh APFS container and read its bytes back. Returns how it was made."""
    if not shutil.which('hdiutil'):
        raise SystemExit('hdiutil not found; this is the macOS path, or pass --mkapfs on Linux')
    # newfs_apfs is the formatter this sequence drives. Checking it here names the missing tool instead of
    # letting the subprocess fail with a bare "command not found", which the log would attribute to hdiutil.
    for tool in ('newfs_apfs', 'dd'):
        if not shutil.which(tool):
            raise SystemExit(f'{tool} not found; the macOS seed path needs hdiutil, newfs_apfs and dd')

    # Step 1: an empty read/write UDIF image, no filesystem yet. -format already implies UDIF, and hdiutil
    # refuses "-format and -type cannot both be specified", which is what the second attempt failed on.
    image = raw.with_suffix('.dmg')
    run('hdiutil', 'create', '-size', f'{size // MIB}m', '-format', 'UDRW', '-layout', 'NONE', '-ov',
        str(image))

    # Step 2: attach it, write the APFS container onto it, detach. newfs_apfs is hdiutil's own formatter and
    # produces exactly what the kernel and mount_apfs expect.
    attached = run('hdiutil', 'attach', '-nomount', '-nobrowse', '-noverify', str(image))
    device = None
    for line in attached.split('\n'):
        match = re.match(r'(/dev/disk\d+)', line.strip())
        if match:
            device = match.group(1)
            break
    if not device:
        raise SystemExit(f'hdiutil attach did not name a device:\n{attached}')

    try:
        # Format the whole device. APFS is case-insensitive by default, which is what a Data volume is.
        run('newfs_apfs', '-v', LABEL, device)
        # Read the bytes back through the raw character device. The size is the whole container, so a dd of
        # the device yields exactly `size` bytes; a timing out dd is a real failure, not a partial success.
        source = '/dev/r' + device.split('/')[-1]
        result = subprocess.run(
            ['dd', f'if={source}', f'of={raw}', f'bs={MIB}', f'count={size // MIB}'],
            capture_output=True, text=True)
        if result.returncode:
            raise SystemExit(f'dd from {source} failed ({result.returncode}):\n{result.stderr}')
    finally:
        subprocess.run(['hdiutil', 'detach', device, '-force'], capture_output=True)

    if raw.stat().st_size != size:
        raise SystemExit(f'the raw copy is {raw.stat().st_size} bytes, not the {size} requested')
    return 'hdiutil'


def make_with_mkapfs(raw: Path, size: int, volume_uuid: str, container_uuid: str) -> str:
    mkapfs = shutil.which('mkapfs')
    if not mkapfs:
        raise SystemExit('mkapfs not found: apt install apfsprogs, or build linux-apfs/apfsprogs')
    with raw.open('wb') as handle:
        handle.truncate(size)
    run(mkapfs, '-L', LABEL, '-U', container_uuid, '-u', volume_uuid, str(raw))
    return 'mkapfs'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gib', type=int, required=True, help='container size, which becomes the Data disk size')
    parser.add_argument('--output', type=Path, required=True, help='packed DBRDISK1 seed to write')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--keep-raw', type=Path, help='also keep the raw container for inspection')
    parser.add_argument('--mkapfs', action='store_true', help='use apfsprogs mkapfs (Linux only)')
    parser.add_argument('--volume-uuid', default=None)
    args = parser.parse_args()

    if args.output.exists():
        sys.exit(f'refuse to overwrite {args.output}')

    size = args.gib * GIB
    if size % PAGE:
        sys.exit('the container size must be a whole number of pages')
    if size > 1024 * GIB:
        sys.exit('the kernel accepts at most 1 TiB of Data')
    # Deriving the UUIDs from the size keeps builds reproducible: the same size yields the same recorded
    # identity, and a size change yields a different volume, which is what restarts the Data store.
    volume_uuid = args.volume_uuid or str(uuid.uuid5(uuid.NAMESPACE_OID, f'fuxi-data-{args.gib}GiB')).upper()
    container_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, f'fuxi-data-container-{args.gib}GiB')).upper()

    with tempfile.TemporaryDirectory(prefix='fuxi-data-') as td:
        raw = Path(td) / 'data.raw'
        if args.mkapfs:
            method = make_with_mkapfs(raw, size, volume_uuid, container_uuid)
        else:
            method = make_with_hdiutil(raw, size)

        data = raw.read_bytes()
        if data[32:36] != b'NXSB':
            sys.exit('the container does not start with an APFS NXSB superblock at offset 32')
        packer = load_packer()
        packed, extents = packer.pack(data)
        packer.check(packed, len(data))

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(packed)
        if args.keep_raw:
            args.keep_raw.parent.mkdir(parents=True, exist_ok=True)
            args.keep_raw.write_bytes(data)

        report = {
            'gib': args.gib,
            'logical_bytes': size,
            'container_uuid': container_uuid,
            'volume_uuid': volume_uuid,
            'label': LABEL,
            'method': method,
            'packed_bytes': len(packed),
            'packed_sha256': hashlib.sha256(packed).hexdigest(),
            'extents': extents,
        }
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        print(f'seed {args.gib} GiB -> {len(packed)} bytes ({extents} extents)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
