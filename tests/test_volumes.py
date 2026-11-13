from pico_tool.card import volumes


def test_linux_mount_table():
    text = ("/dev/sdb1 /media/gamma/GAMMAOS vfat rw,nosuid 0 0\n"
            "/dev/sdc1 /run/media/u/My\\040Card exfat rw 0 0\n"
            "C:\\134 /mnt/c 9p rw 0 0\n"
            "/dev/sdd1 /media/gamma/EXT ext4 rw 0 0\n"
            "/dev/sde1 /media/gamma/FUSE fuseblk rw 0 0\n"
            "tmpfs /run tmpfs rw 0 0\n"
            "/dev/sda2 / ext4 rw 0 0\n")
    blk = {"/dev/sdb1": ("vfat", "FAT32", "GAMMAOS"), "/dev/sde1": ("exfat", "1.0", "")}
    vols = volumes.parse_proc_mounts(text, lambda: blk)
    got = [(v.path, v.fs_label, v.fs_ok, v.label) for v in vols]
    assert got == [("/media/gamma/GAMMAOS", "FAT32", True, "GAMMAOS"),
                   ("/run/media/u/My Card", "exFAT", True, "My Card"),
                   ("/media/gamma/EXT", "ext4", False, "EXT"),
                   ("/media/gamma/FUSE", "exFAT", True, "FUSE")]


def test_macos_mount_output():
    t = volumes.parse_mount_output("/dev/disk4s1 on /Volumes/GAMMA OS (msdos, local, nodev, nosuid, noowners)\n"
                                   "/dev/disk1s1 on / (apfs, local, journaled)\n"
                                   "map auto_home on /System/Volumes/Data/home (autofs, automounted)\n")
    assert t["/Volumes/GAMMA OS"] == ("/dev/disk4s1", "msdos") and t["/"] == ("/dev/disk1s1", "apfs")


def test_folder_volume(tmp_path):
    (tmp_path / "gammaos").mkdir()
    (tmp_path / "gammaos" / "menu.pack").write_bytes(b"m")
    (tmp_path / "README.txt").write_text("GammaOS Pico for the Konkr Pocket Advance: the microSD card, release 2026.11.12 (lk-mt6785 x)\n")
    v = volumes.folder_volume(str(tmp_path))
    assert v.has_pico and v.version == "2026.11.12" and v.total > 0


def test_list_volumes_does_not_raise():
    assert isinstance(volumes.list_volumes(), list)
