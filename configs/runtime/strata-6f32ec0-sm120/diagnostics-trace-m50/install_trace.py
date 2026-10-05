"""Install one extension only over the verified diagnostic parent bytes."""
import hashlib
from pathlib import Path

BASE_SHA256 = '2080dbf883df61f8382d0b3d65f6ec77cefb248b8ec536e39f27b9c749a962a1'
SERVER_SHA256 = '2741bcbc4fd6a5a50df706d64fc6c0fd757b19145ae3826dc997f90127a0044e'


def install(root):
    helper = root / 'anvil_diagnostics.py'
    server = root / 'serve/server.py'
    original = helper.read_bytes()
    if hashlib.sha256(original).hexdigest() != BASE_SHA256:
        raise ValueError('Pinned diagnostic helper identity mismatch')
    if hashlib.sha256(server.read_bytes()).hexdigest() != SERVER_SHA256:
        raise ValueError('Pinned diagnostic server identity mismatch')
    alias = root / 'anvil_diagnostics_base.py'
    if alias.exists():
        raise ValueError('Diagnostic base alias already exists')
    extension = (root / 'anvil/trace_diagnostics.py').read_bytes()
    compile(extension, 'anvil_diagnostics.py', 'exec')
    alias.write_bytes(original)
    helper.write_bytes(extension)


if __name__ == '__main__':
    install(Path('/opt/strata'))
