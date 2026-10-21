"""Run the integrated Vela API locally using only dedicated paper-model settings."""
import argparse
import os
from pathlib import Path
import shlex
import stat
import sys

ROOT=Path(__file__).resolve().parents[1]
ALLOWED={"VELA_MODEL","VELA_MODEL_KEY","VELA_MODEL_ENDPOINT"}


def load_model_file(path):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT/".runtime/lighter-showcase"):
        raise ValueError("Model configuration must stay in the isolated paper runtime directory")
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Model configuration must be an owner-only regular file (chmod 600)")
    if info.st_size>16384:
        raise ValueError("Model configuration exceeds size limit")
    values={}
    for line in path.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line=line[7:]
        key,sep,value=line.partition("=")
        key=key.strip()
        if not sep or key not in ALLOWED or key in values:
            raise ValueError("Only unique Vela development-model settings are permitted")
        parts=shlex.split(value,comments=True)
        if len(parts)!=1 or not parts[0] or any(c.isspace() for c in parts[0]):
            raise ValueError("Invalid model setting format")
        values[key]=parts[0]
    if not values.get("VELA_MODEL") or not values.get("VELA_MODEL_KEY"):
        raise ValueError("Both dedicated model name and key are required")
    return values


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-env",type=Path)
    parser.add_argument("--port",type=int,default=8790)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:
        parser.error("Use a local development port between 1024 and 65535")
    env=os.environ.copy()
    if args.model_env:
        try:env.update(load_model_file(args.model_env))
        except (ValueError,OSError):
            # Never include file contents, parser tokens or secret values in diagnostics.
            parser.error("Model configuration could not be loaded. Check location, mode 600 and the allowed variable names.")
    env["PYTHONPATH"]=str(ROOT/"backend")
    env["VELA_EXECUTION_MODE"]="PAPER"
    env["VELA_CHAIN_WRITES"]="false"
    env["VELA_PORTAL_ORIGIN"]=f"http://127.0.0.1:{args.port}"
    os.chdir(ROOT)
    os.execve(sys.executable,[sys.executable,"-m","uvicorn","vela.api:app",
        "--host","127.0.0.1","--port",str(args.port),"--no-access-log"],env)


if __name__=="__main__":main()
