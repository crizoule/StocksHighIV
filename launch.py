"""Standard-library entry point: serves the UI before installing project dependencies."""
import sys

if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11 or newer is required. Install it from https://www.python.org/downloads/')

from highiv.app import main

if __name__ == '__main__':
    main()
