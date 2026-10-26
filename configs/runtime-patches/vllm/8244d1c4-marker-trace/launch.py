"""Mandatory bounded GPU trace test, then the unchanged sampling startup gate."""

import os
from pathlib import Path
import subprocess
import sys


if __name__ == "__main__":
    subprocess.run([sys.executable, str(Path(__file__).with_name("gpu_regression.py"))],
                   check=True, timeout=120)
    os.execv(sys.executable, [sys.executable, "/opt/anvil-sampling/launch.py", *sys.argv[1:]])
