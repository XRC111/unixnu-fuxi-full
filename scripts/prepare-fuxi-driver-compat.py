#!/usr/bin/env python3
"""Apply the pinned shared GPU driver's Darwin23 source compatibility patch."""
from pathlib import Path
import re
import subprocess

root=Path(__file__).resolve().parents[1]
driver=root/'drivers/Adreno740'
revision=subprocess.check_output(['git','-C',driver,'rev-parse','HEAD'],text=True).strip()
# e8ef803: fuxi 2026-11-15 commit on a9a2cbb that already contains the compatibility patch (it then reverse-applies cleanly).
# f4a9fbb: fuxi 2026-11-18 merge of upstream 1c9029b, keeping the task-role compatibility (NativeAdrenoUserClient.cpp).
# 149ef6d8: what UniXNU's own gitlink pins for drivers/Adreno740. It predates the fuxi compatibility
# patches, so the forward apply below is the path that runs; a differing context still raises.
KNOWN = ('149ef6d8ac', 'a9a2cbb43b89', 'e8ef8031bdfd', 'f4a9fbb3da01')
if not any(revision.startswith(prefix) for prefix in KNOWN):
    raise SystemExit('Unexpected Adreno740 revision; requalify the compatibility patch')
# f4a9fbb carries every line of the patch (the merge kept fuxi's side); only an upstream comment beside it was reworded
# (sheng-gpu-wc -> gpu-wc), so the reverse check cannot match: accepted as applied.
if revision=='f4a9fbb3da01457a35a127e62be64ab702bd2c18':
    print('Darwin23 GPU compatibility already in the fuxi upstream merge'); raise SystemExit(0)
patch=root/'patches/drivers/adreno740-darwin23-task-role.patch'
def check(*args):
    return subprocess.run(['git','-C',driver,'apply',*args,str(patch)],capture_output=True).returncode==0
if check('--reverse','--check'):
    print('Darwin23 GPU compatibility already applied')
elif check('--check'):
    subprocess.run(['git','-C',driver,'apply',str(patch)],check=True)
else:
    raise SystemExit('GPU source context differs; refusing partial patch application')
