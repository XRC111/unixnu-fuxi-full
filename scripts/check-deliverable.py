"""Check that a built payload and boot image meet the four deliverable conditions from AGENTS.md.

Written as a file rather than a heredoc inside the workflow: a heredoc in a `run: |` block has to keep the
same indentation as the rest of the block or the YAML stops parsing, and the failure surfaces as "no jobs
were created" with nothing in the log.

usage: python3 check-deliverable.py <payload.json> <payload.img> <bootimage.json> [mdir]
"""
import json
import shutil
import subprocess
import sys


def fail(message):
    print('::error::' + message)
    return False


def main() -> int:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    report, image, bootimage = sys.argv[1], sys.argv[2], sys.argv[3]
    mdir = sys.argv[4] if len(sys.argv) > 4 else 'mdir'

    ok = True
    payload = json.load(open(report))
    trust = payload.get('trust_cache_sha256')
    addon = payload.get('gpu_addon_sha256')
    if not trust:
        ok = fail('payload.json has no trust_cache_sha256')
    if not addon:
        ok = fail('payload.json has no gpu_addon_sha256')
    print('trust_cache_sha256', trust)
    print('gpu_addon_sha256', addon)

    if not shutil.which(mdir):
        ok = fail(f'{mdir} is missing from PATH; cannot list the payload image')
    else:
        result = subprocess.run([mdir, '-i', image, '::/EFI/UNIXNU/PAYLOADS'],
                                capture_output=True, text=True)
        listing = result.stdout + result.stderr
        print(listing)
        if 'kernel.macho.trustcache' not in listing.lower():
            ok = fail('kernel.macho.trustcache is not in the payload image')

    boot = json.load(open(bootimage))
    header = boot.get('header_version')
    print('header_version', header)
    if header != 3:
        ok = fail(f'bootimage.json header_version is {header}, expected 3')

    if ok:
        print('all four deliverable checks passed')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
