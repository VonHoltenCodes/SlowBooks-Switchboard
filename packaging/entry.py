"""The frozen program's entry point: the same commands as
``python -m sbplive``. With no command it is the stdio server,
so a host's configuration names this executable alone."""

import sys

from sbplive.cli import main

if __name__ == "__main__":
    sys.exit(main())
