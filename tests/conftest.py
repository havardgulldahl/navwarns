import sys, pathlib

# Ensure project root on path so 'scripts' package is importable when running tests directly
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
