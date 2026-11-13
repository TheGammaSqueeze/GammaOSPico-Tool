# pico_tool.console: dependencies and packaging needs

## pip

```
pyusb>=1.2.1
libusb-package>=1.0.26.1
```

- `pyusb` is the Python side of USB access (1.2.1 or later has `usb.core.USBTimeoutError`).
- `libusb-package` ships the libusb 1.0 library inside the wheel for Windows, macOS and Linux, so
  nobody has to install libusb on their system. It pulls in `importlib_resources` by itself.
- Nothing else: the fastboot client is our own code in `fastboot.py`. No system `fastboot`, adb or
  libusb is needed at run time.
- Tests: `pytest`. They never touch USB hardware (an in-memory console in
  `tests/console/console_fake.py` stands in for it); the pyusb tests use stand-in device objects
  and are skipped when pyusb is missing.

## PyInstaller

Hidden imports (pyusb loads its backends by name, and `libusb_package` is imported lazily):

```
--hidden-import usb.backend.libusb1
--hidden-import libusb_package
--hidden-import importlib_resources
```

Binaries:

```
--collect-binaries libusb_package
```

`libusb-package` also registers its own PyInstaller hook (entry point `pyinstaller40`) that copies
the library to the root of the bundle. `pico_tool/console/usb.py` looks for it in both places
(`sys._MEIPASS` and `sys._MEIPASS/libusb_package`) before falling back to a system libusb, so
either the hook or `--collect-binaries` is enough; keeping both does no harm.

Per system notes:

- Windows: the bundled `libusb-1.0.dll` talks to devices that have the WinUSB driver. The tool
  shows `driver_help()` (Zadig or the Google USB Driver) when the console is found but cannot be
  opened.
- macOS: sign the bundled `libusb-1.0.dylib` together with the app when the app is signed. Build
  on each architecture (arm64 and x86_64), since the wheel carries a library for the machine it was
  installed on.
- Linux: the bundled `libusb-1.0.so` is used; the person still needs the udev rule from
  `driver_help()` to open the console without root.

## Public API notes for the GUI and CLI

Beyond the shared contract, the module also exports:

- `scan()` returns a `ScanReport` (`links`, `problems`, `blocked`, `library_problem`) so the UI can
  say why a connected device cannot be used (driver, permission, busy, stock bootloader).
  `wait_for_console` already sends those sentences through `progress`.
- `read_backup(path)` loads a backup's `lk_a` and `lk_b` (checked against the recorded sha256)
  for `write_lk`.
- `has_backup(root, ident)`.
- `ConsoleCancelled` and `ConsoleTooOld` are subclasses of `ConsoleError`.
- `write_lk(..., backup_root=None)`: a console whose bootloader is older than 1.0.0 (no
  `oem part-read`) is written only when `backup_root` (default `default_backup_root()`) holds a
  backup of it. It returns `{"lk_a": "verified" | "written", ...}`.
- `list_backups` entries also carry `"ok"` and `"problems"` (files re-hashed against backup.json).
