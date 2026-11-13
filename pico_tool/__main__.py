"""gammaos-pico-tool: with no arguments the window opens; with a command, the command line runs."""

import sys


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("--gui", "gui"):
        _hide_own_console()
        from .gui.app import main as gui_main
        return gui_main()
    from .cli import main as cli_main
    return cli_main(args)


def _hide_own_console():
    """On Windows, a double-clicked build gets a console window of its own: hide it for the window
    (one opened from a command prompt is shared, and stays)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        procs = (ctypes.c_uint * 4)()
        if k32.GetConsoleProcessList(procs, 4) == 1:
            hwnd = k32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
