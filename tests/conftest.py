import sys
import pathlib

# Ensure repository root is on sys.path so tests can import the top-level modules
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
