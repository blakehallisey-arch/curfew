"""`python3 -m curfew` is the CLI. `python3 -m curfew.hook` is the hook."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
