import io
import json
import logging
import shutil
import sys
import zipfile
from json.decoder import JSONDecodeError
from pathlib import Path
from typing import Any, Optional, TypedDict, TypeVar, Union
from zipfile import BadZipFile, ZipFile

_T_ModPackDown = TypeVar('_T_ModPackDown', bound='ModPackDown')

FsOrZipPath = Union[Path, zipfile.Path]
EventMod = tuple[str, str, FsOrZipPath]
InstalledModsCounter = dict[str, int]
LoadedModList = dict[str, tuple[str, FsOrZipPath]]
CachedModVersions = dict[str, tuple[str, str]]

if sys.platform == 'win32':
    _default_mods_dir = '~/Appdata/Roaming/.minecraft/mods'
elif sys.platform == 'darwin':
    _default_mods_folder = '~/Library/Application Support/minecraft'
else:
    _default_mods_folder = '~/.minecraft'
DEFAULT_MODS_DIR = Path(_default_mods_dir).expanduser()


class EventNames:
    pass


class BasicFabricModJson(TypedDict):
    schemaVersion: int
    id: str
    version: str


def read_mod_version(zfp: ZipFile) -> Optional[tuple[str, str]]:
    try:
        mod_json_info = zfp.getinfo('fabric.mod.json')
    except KeyError:
        return None
    with zfp.open(mod_json_info, 'r') as info_fp_bytes:
        with io.TextIOWrapper(info_fp_bytes, 'utf-8') as info_fp:
            try:
                mod_json: BasicFabricModJson = json.load(info_fp)
            except (JSONDecodeError, UnicodeDecodeError):
                return None
    if mod_json.get('schemaVersion') != 1:
        return None # Unsupported fabric.mod.json version
    mod_id = mod_json.get('id')
    mod_version = mod_json.get('version')
    if mod_id is None or mod_version is None:
        return None # Missing id or version
    return mod_id, mod_version


def get_mod_versions(mods_dir: FsOrZipPath, cache: CachedModVersions) -> LoadedModList:
    result: LoadedModList = {}
    for file in mods_dir.iterdir():
        if not file.name.endswith('.jar'):
            continue # Not a JAR file
        if not file.is_file():
            continue
        if file.name in cache:
            mod_id, mod_version = cache[file.name]
            result[mod_id] = (mod_version, file)
        else:
            try:
                with ZipFile(file.open('rb'), 'r') as zfp:
                    info = read_mod_version(zfp)
            except BadZipFile:
                continue # Not a valid JAR file
            if info is None:
                continue # Is a JAR file, but isn't a mod
            mod_id, mod_version = info
            cache[file.name] = info
            result[mod_id] = (mod_version, file)
    return result


class ModPackDown:
    mods_dir: Path
    _cache_file: Path
    _installed_packs_file: Path
    initted: bool

    packed_mods: InstalledModsCounter
    current_mods: LoadedModList
    version_id_cache: CachedModVersions

    def __init__(self, mods_dir: Path) -> None:
        self.mods_dir = mods_dir
        self._cache_file = mods_dir / 'modpackdown_cache.json'
        self._installed_packs_file = mods_dir / 'modpackdown_data.json'
        self.initted = False

    def init(self) -> None:
        try:
            with open(self._cache_file) as fp:
                self.version_id_cache = json.load(fp)
        except (FileNotFoundError, UnicodeDecodeError, JSONDecodeError):
            self.version_id_cache = {}
        try:
            with open(self._installed_packs_file) as fp:
                self.packed_mods = json.load(fp)
        except (FileNotFoundError, UnicodeDecodeError, JSONDecodeError):
            self.packed_mods = {}
        self.current_mods = get_mod_versions(self.mods_dir, self.version_id_cache)
        self.initted = True

    def deinit(self) -> None:
        to_reraise: Optional[Exception] = None
        try:
            with open(self._cache_file, 'w') as fp:
                json.dump(self.version_id_cache, fp)
        except Exception as e:
            to_reraise = e
        try:
            with open(self._installed_packs_file, 'w') as fp:
                json.dump(self.packed_mods, fp)
        except Exception as e:
            if to_reraise is None:
                to_reraise = e
            else:
                to_reraise = Exception(to_reraise, e)
        self.initted = False
        if to_reraise is not None:
            raise to_reraise

    # Type update should be updated when PEP 673 is implemented
    def __enter__(self: _T_ModPackDown) -> _T_ModPackDown:
        self.init()
        return self

    def __exit__(self, *args) -> None:
        self.deinit()

    def install_pack(self, pack_path: Path) -> None:
        installed_count = 0
        skipped_count = 0
        failed_count = 0
        with ZipFile(pack_path) as pack_zip:
            zip_root = zipfile.Path(pack_zip)
            pack_mods = get_mod_versions(zip_root, self.version_id_cache)
            self.identified_mods_to_install(pack_mods, False)
            for (mod_id, (mod_version, mod_origin)) in pack_mods.items():
                event_mod_data = (mod_id, mod_version, mod_origin)
                if mod_id in self.current_mods:
                    skipped_count += 1
                    if mod_id in self.packed_mods:
                        # Record this mod as installed again
                        self.packed_mods[mod_id] += 1
                        self.skipped_installation(event_mod_data, skipped_count, True)
                    else:
                        # Otherwise it's from the user
                        self.packed_mods[mod_id] = 2
                        self.skipped_installation(event_mod_data, skipped_count, False)
                else:
                    self.packed_mods[mod_id] = 1
                    try:
                        with (
                                mod_origin.open('rb') as fp_from,
                                (self.mods_dir / mod_origin.name).open('wb') as fp_to
                            ):
                            shutil.copyfileobj(fp_from, fp_to)
                    except Exception as e:
                        failed_count += 1 # Disk space or permissions error I guess?
                        self.failed_installation(event_mod_data, failed_count, e)
                    else:
                        installed_count += 1
                        self.succeeded_installation(event_mod_data, installed_count)
        self.pack_installed(installed_count, skipped_count, failed_count)

    def uninstall_pack(self, pack_path: Path) -> None:
        uninstalled_count = 0
        skipped_count = 0
        failed_count = 0
        with ZipFile(pack_path) as pack_zip:
            zip_root = zipfile.Path(pack_zip)
            pack_mods = get_mod_versions(zip_root, self.version_id_cache)
            self.identified_mods_to_install(pack_mods, True)
            for (mod_id, (mod_version, mod_origin)) in pack_mods.items():
                event_mod_data = (mod_id, mod_version, mod_origin)
                if mod_id in self.packed_mods:
                    self.packed_mods[mod_id] -= 1
                    if self.packed_mods[mod_id] > 0:
                        skipped_count += 1
                        self.skipped_uninstallation(event_mod_data, skipped_count)
                    else:
                        removal_path = self.mods_dir / mod_origin.name
                        try:
                            removal_path.unlink()
                        except FileNotFoundError:
                            failed_count += 1
                            self.failed_uninstallation(event_mod_data, failed_count, 'it was missing')
                        else:
                            uninstalled_count += 1
                            self.succeeded_uninstallation(event_mod_data, uninstalled_count)
                        self.current_mods.pop(mod_id, None)
                        self.packed_mods.pop(mod_id, None)
                else:
                    failed_count += 1
                    self.failed_uninstallation(event_mod_data, failed_count, 'it was not installed')
        self.pack_uninstalled(uninstalled_count, skipped_count, failed_count)

    ####################
    ## Event handlers ##
    ####################

    # Use Any so subclasses can return whatever
    def identified_mods_to_install(self, mods: LoadedModList, is_uninstall: bool) -> Any:
        logging.info('Identified %i mods to maybe %sinstall', len(mods), 'un' * is_uninstall)

    def skipped_installation(self, mod: EventMod, counter: int, was_from_another_pack: bool) -> Any:
        message = 'Skipped installation of mod %s as it was already '
        if was_from_another_pack:
            message += 'installed from another pack'
        else:
            message += 'user installed'
        logging.info(message, mod[0])

    def failed_installation(self, mod: EventMod, counter: int, reason: Optional[BaseException] = None) -> Any:
        logging.error('Failed to install mod %s:%s because %s', mod[0], mod[1], reason)

    def succeeded_installation(self, mod: EventMod, counter: int) -> Any:
        logging.info('Successfully installed mod %s:%s', mod[0], mod[1])

    def pack_installed(self, succeeded: int, skipped: int, failed: int) -> Any:
        logging.info('Installed %i mods from this pack', succeeded)
        if skipped:
            logging.info('%i mods were skipped because they were already installed', skipped)
        if failed:
            logging.warning('%i mods failed to install for some reason', failed)

    def skipped_uninstallation(self, mod: EventMod, counter: int) -> Any:
        logging.info('Skipped uninstallation of mod %s as it was installed from somewhere else as well', mod[0])

    def failed_uninstallation(self, mod: EventMod, counter: int, reason: Union[None, str, BaseException] = None) -> Any:
        logging.error('Failed to uninstall %s:%s because %s', mod[0], mod[1], reason)

    def succeeded_uninstallation(self, mod: EventMod, counter: int) -> Any:
        logging.info('Successfully uninstalled %s:%s', mod[0], mod[1])

    def pack_uninstalled(self, succeeded: int, skipped: int, failed: int) -> Any:
        logging.info('Uninstalled %i mods from this pack', succeeded)
        if skipped:
            logging.info('%i mods were skipped because they were installed from somewhere else', skipped)
        if failed:
            logging.warning('%i mods failed to uninstall because they were missing or the installation state was inconsistent', failed)
