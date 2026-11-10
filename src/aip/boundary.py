"""Restrict baseline worker Python file opens to its packet and runtime libraries.

This is defense against accidental truth reads in trusted prototype code. Python
audit hooks are not an adversarial OS sandbox or a boundary for malicious code.
"""
import os
import sys
from pathlib import Path


def install_read_boundary(packet):
    allowed = [Path(packet).resolve(), Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
    code = Path(__file__).resolve().parent
    allowed_code = {code / "baseline.py", code / "__init__.py", code / "boundary.py"}

    def within(path, root):
        return path == root or root in path.parents

    def audit(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            mode, flags = args[1], args[2]
            writes = (isinstance(mode, str) and any(x in mode for x in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writes:
                raise PermissionError(f"Baseline worker does not write files: {path.name}")
            if not any(within(path, root) for root in allowed) and path not in allowed_code:
                raise PermissionError(f"File is outside the image-only worker boundary: {path.name}")
    sys.dont_write_bytecode = True
    sys.addaudithook(audit)
