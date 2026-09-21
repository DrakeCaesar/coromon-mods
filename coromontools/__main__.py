"""`python -m coromontools` - the same entry point as the script beside this package.

Handy from inside the tools folder: it is the shortest thing to type while working on the
window, and it exercises the same import path the script does.
"""

import sys

from . import main

if __name__ == "__main__":
    sys.exit(main())
