"""Recipe-selected startup gate; forward original arguments only after success."""

import os
from pathlib import Path
import subprocess
import sys


if __name__ == "__main__":
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("kernel_regression.py"))],
        check=True, timeout=180,
    )
    os.execv("/usr/local/bin/lil-entrypoint", ["/usr/local/bin/lil-entrypoint", *sys.argv[1:]])
