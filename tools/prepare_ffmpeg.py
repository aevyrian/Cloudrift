"""Fetch a pinned, verified Linux runtime for the image; never install on Windows."""
import hashlib
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

NAME='ffmpeg-n9.0.2-33-g4cf298e66a-linux64-gpl-9.0.tar.xz'
URL='https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-22-13-06/'+NAME
SHA256='1cb0a72cf393a1fdb661cbbbd0cfe89dc8b197bb21e21473bbde505bb4f2a2a7'


def prepare(destination, archive):
    destination,archive=Path(destination),Path(archive)
    archive.parent.mkdir(parents=True,exist_ok=True);destination.mkdir(parents=True,exist_ok=True)
    if not archive.is_file():
        temporary=archive.with_suffix('.part')
        try:
            with urllib.request.urlopen(URL,timeout=120) as response,temporary.open('wb') as target:shutil.copyfileobj(response,target)
            with temporary.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=SHA256:raise ValueError('FFmpeg archive checksum mismatch')
            temporary.replace(archive)
        finally:temporary.unlink(missing_ok=True)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=SHA256:raise ValueError('FFmpeg archive checksum mismatch')
    with tarfile.open(archive,'r:xz') as tar:
        for name in ('ffmpeg','ffprobe','LICENSE.txt'):
            matches=[m for m in tar.getmembers() if m.isfile() and Path(m.name).name==name]
            if len(matches)!=1:raise ValueError('FFmpeg runtime member missing: '+name)
            out=destination/name
            with tar.extractfile(matches[0]) as source,out.open('wb') as target:shutil.copyfileobj(source,target)
            out.chmod(0o755 if name!='LICENSE.txt' else 0o644)
    print('Verified Linux FFmpeg runtime',SHA256)


if __name__=='__main__':prepare(sys.argv[1],sys.argv[2])
