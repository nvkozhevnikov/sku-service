"""Run a frozen browser regression with evidence redirected to a new folder."""
import re
import sys
from pathlib import Path

script = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()
destination.mkdir(parents=True, exist_ok=True)
source = script.read_text(encoding='utf-8')
source, replacements = re.subn(r'''OUT\s*=\s*ROOT.parents\[1\]\s*/\s*['"][^'"]+['"]''',
    lambda _: 'OUT = Path(' + repr(str(destination)) + ')', source, count=1)
if replacements != 1:
    raise SystemExit('Expected one evidence destination')
sys.argv = [str(script)]
exec(compile(source, str(script), 'exec'), {'__file__': str(script), '__name__': '__main__'})
