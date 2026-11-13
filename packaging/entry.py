"""PyInstaller entry point: the window with no arguments, the command line with them."""
import sys

from pico_tool.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
