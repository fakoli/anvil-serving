"""Protected local key files for explicit local monitoring probes."""
import os
from pathlib import Path
import re
import stat


def safe_path(value):
    if (not isinstance(value, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value)
            or any(p in {'', '.', '..'} for p in value.split('/')[1:])):
        raise ValueError('secret file requires a normalized absolute path')
    return value


def read_key(path):
    """Return a key and non-secret file identity; never include content in errors."""
    safe_path(path)
    try:
        directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        private_ancestor = False
        try:
            for part in Path(path).parts[1:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory); directory = child
                parent = os.fstat(directory)
                sticky_root = parent.st_uid == 0 and parent.st_mode & stat.S_ISVTX
                if parent.st_uid not in {0, os.getuid()} or (parent.st_mode & 0o022 and not private_ancestor and not sticky_root):
                    raise ValueError("secret directory custody is not protected")
                private_ancestor |= not bool(parent.st_mode & 0o077)
            if os.fstat(directory).st_mode & 0o022:
                raise ValueError("secret parent must not be group or world writable")
            fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid not in {0, os.getuid()}
                    or before.st_mode & 0o077 or not 1 <= before.st_size <= 8192):
                raise ValueError('secret file must be protected, owned, regular and bounded')
            raw = handle.read(8193)
            after = os.fstat(handle.fileno())
        def identity(s):
            return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_uid)
        if identity(before) != identity(after) or identity(after) != identity(os.stat(path, follow_symlinks=False)):
            raise ValueError('secret file changed during read')
        key = raw.rstrip(b'\r\n')
        if not key or len(raw) > 8192 or any(c < 33 or c > 126 for c in key):
            raise ValueError('secret key must contain visible ASCII without whitespace')
        return key.decode('ascii'), identity(after)
    except OSError:
        raise ValueError('secret file unavailable') from None
