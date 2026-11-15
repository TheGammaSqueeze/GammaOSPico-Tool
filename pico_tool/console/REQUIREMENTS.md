# pico_tool.console: dependencies and packaging needs

## pip

```
pyusb>=1.2.1
libusb-package>=1.0.26.1
```

- `pyusb` is the Python side of USB access (1.2.1 or later has `usb.core.USBTimeoutError`).
- `libusb-package` ships the libusb 1.0 library inside the wheel for Windows, macOS and Linux, so
  nobody has to install libusb on their system. It pulls in `importlib_resources` by itself.
- Nothing else for USB: the fastboot client is our own code in `fastboot.py`. No system `fastboot`,
  adb or libusb is needed at run time.
- The rest of the tool (downloads and box art) also needs `truststore` (Python 3.10 and later) and
  `certifi`: every HTTPS request goes through `pico_tool/net.py`, which checks certificates
  against the operating system's trust store, or certifi's list when that is not available.
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

HTTPS (the frozen OpenSSL's own CA path is the build machine's, which the person's computer does
not have, so one of these must be in the bundle):

```
--hidden-import truststore --collect-submodules truststore
--hidden-import certifi --collect-data certifi
```

The release workflow runs each built binary's `check-network` with `SSL_CERT_FILE` and
`SSL_CERT_DIR` pointed at nothing, and fails the build when it cannot reach GitHub securely. When
the built-in ScreenScraper access is baked in (`pico_tool/scrape/_devcreds_gen.py`, from the
`SS_DEVCREDS_GEN` secret, required for a tag build), the build also adds
`--hidden-import pico_tool.scrape._devcreds_gen` and checks the binary reports it.

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

## Consoles

`models.py` holds one `ConsoleModel` per console: the Konkr Pocket Advance (`kpa`, product
`k85v1_64_kpa`, writes `lk_a` and `lk_b`) and the Konkr Pocket Block (`block`, product
`k68v1_64_block`, writes `lk_a` only). `ConsoleLink.model` says which one is connected;
`backup_lk`, `read_backup`, `write_lk` and `check_lk_image(data, model)` follow it, and an image,
flash package or backup made for one console is refused on the other. `backup.json` records
`"model"`; a backup without it is a Pocket Advance backup.

## Public API notes for the GUI and CLI

Beyond the shared contract, the module also exports:

- `scan()` returns a `ScanReport` (`links`, `problems`, `blocked`, `library_problem`) so the UI can
  say why a connected device cannot be used (driver, permission, busy, stock bootloader).
  `wait_for_console` already sends those sentences through `progress`.
- `read_backup(path, model=None)` loads a backup's LK copies (checked against the recorded sha256)
  for `write_lk`; with `model`, a backup of the other console is refused. `backup_model(entry)`
  says which console a backup is of.
- `has_backup(root, ident)`.
- `ConsoleCancelled` and `ConsoleTooOld` are subclasses of `ConsoleError`.
- `write_lk(..., backup_root=None)`: a console whose bootloader is older than 1.0.0 (no
  `oem part-read`) is written only when `backup_root` (default `default_backup_root()`) holds a
  backup of it. It returns `{"lk_a": "verified" | "written", ...}`.
- `list_backups` entries also carry `"ok"` and `"problems"` (files re-hashed against backup.json).
