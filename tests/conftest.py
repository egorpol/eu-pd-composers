"""Make scripts/ importable as top-level modules (they import each other that way)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
