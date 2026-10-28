"""Run the test suite without pytest (for environments where it cannot be installed)."""
import importlib
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "src"), str(root / "tests")]
fails = total = 0
for f in sorted((root / "tests").glob("test_*.py")):
    mod = importlib.import_module(f.stem)
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        total += 1
        try:
            getattr(mod, name)()
        except Exception as e:
            fails += 1
            print(f"FAIL {f.stem}.{name}: {e!r}"[:400])
print(f"{total - fails}/{total} passed")
sys.exit(1 if fails else 0)
