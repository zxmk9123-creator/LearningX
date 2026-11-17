"""Install the frozen, previously reviewed NIT pilot engine from a local ZIP.

DO NOT download unknown source or mutate this ZIP. The SHA256 is pinned to the
verified conversation artifact. Source stays outside the public Git repository.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile

PINNED_SHA256="59cb68c8c27fd16543db2f68b798828bd683ca08d0126bb15b064f009952f5e8"
REQUIRED=("scripts/ingest.py","scripts/verified_coaching.py",
          "runtime/coaching.py","runtime/gateway.py","runtime/concept_validation.py")

def install(source:Path,dest:Path):
    source=source.resolve();dest=dest.resolve()
    if not source.is_file():raise ValueError("Engine ZIP not found")
    if hashlib.sha256(source.read_bytes()).hexdigest()!=PINNED_SHA256:
        raise ValueError("Engine SHA256 does not match the pinned pilot patch")
    if dest.exists():raise ValueError("Target already exists; never overwrite an installed engine")
    with zipfile.ZipFile(source) as zf:
        files=[x for x in zf.infolist() if not x.is_dir()]
        if len(files)>500 or sum(x.file_size for x in files)>35*1024*1024:
            raise ValueError("Unreasonable ZIP size")
        roots={x.filename.split("/")[0] for x in files}
        if len(roots)!=1:raise ValueError("Unexpected ZIP layout")
        prefix=next(iter(roots))+"/"
        rel=[]
        for member in files:
            name=member.filename
            p=Path(name)
            if not name.startswith(prefix) or ".." in p.parts or p.is_absolute():
                raise ValueError("Invalid archive path")
            if (member.external_attr>>16)&0o170000==stat.S_IFLNK:
                raise ValueError("Symlink entries are disallowed")
            r=Path(name[len(prefix):])
            if r.parts:rel.append((member,r))
        if not all(Path(r) in {p for _,p in rel} for r in REQUIRED):
            raise ValueError("Missing required frozen engine modules")
        dest.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".nit-install-",dir=str(dest.parent)) as tmp:
            staging=Path(tmp)/"engine"
            staging.mkdir()
            for member,relative in rel:
                target=staging/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                with zf.open(member) as src,target.open("wb") as dst:
                    shutil.copyfileobj(src,dst)
            shutil.move(str(staging),str(dest))
    return dest

def main():
    p=argparse.ArgumentParser()
    p.add_argument("zip_path",type=Path)
    p.add_argument("--dest",type=Path,default=Path("backend/.engines/nit-pilot-pinned"))
    a=p.parse_args()
    path=install(a.zip_path,a.dest)
    print("Validated engine installed. Set NIT_ENGINE_ROOT="+str(path))

if __name__=="__main__":main()
