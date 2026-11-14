"""Fixed complete inventory runner; executed only inside existing confinement."""
import json
import re
from pathlib import Path
import subprocess
import sys


def main():
    inventory = json.loads(Path(sys.argv[1]).read_text())
    tests = Path.cwd()/'tests'
    actual = sorted(p.name for p in tests.iterdir() if p.is_file() and p.name.startswith('test_') and p.suffix in {'.lua', '.py'})
    if actual != inventory or len(actual) != len(set(actual)) or not actual:
        raise ValueError('Committed complete test inventory changed')
    passed = failed = 0
    for name in actual:
        command = (['lua5.1', '-e', 'require("bit"); ROOT="../!!!TwichUI/"; REPO="../"; TESTS="./"', name]
            if name.endswith('.lua') else ['python3', '-B', '-m', 'unittest', '-q', Path(name).stem])
        result = subprocess.run(command, cwd=tests, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        output = result.stdout.decode(errors='replace')
        if name.endswith('.lua'):
            exercised='ERROR:' not in output
        else:
            count=re.search(r'Ran ([0-9]+) tests?',output)
            skipped=re.search(r'OK \(skipped=([0-9]+)\)',output)
            exercised=bool(count and int(count[1])>(int(skipped[1]) if skipped else 0))
        valid = result.returncode == 0 and exercised
        if valid:
            passed += 1
            print('ok   '+name, flush=True)
        else:
            failed += 1
            print('FAIL '+name, flush=True)
            print(output[-6000:], flush=True)
    print(f'{passed} passed, {failed} failed', flush=True)
    return int(failed != 0)


if __name__ == '__main__':
    raise SystemExit(main())
