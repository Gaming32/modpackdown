import logging
import sys
from pathlib import Path

from modpackdown import DEFAULT_MODS_DIR, ModPackDown


def main() -> None:
    logging.basicConfig(
        format='[%(asctime)s/%(levelname)s] [%(filename)s:%(lineno)i]: %(message)s',
        datefmt='%H:%M:%S',
        level=logging.INFO
    )

    mods_dir = Path.cwd()
    if mods_dir.name != 'mods':
        mods_dir = DEFAULT_MODS_DIR

    with ModPackDown(mods_dir) as mpd:
        if sys.argv[1] == 'install':
            mpd.install_pack(Path(sys.argv[2]).expanduser())
        elif sys.argv[1] == 'uninstall':
            mpd.uninstall_pack(Path(sys.argv[2]).expanduser())


if __name__ == '__main__':
    main()
