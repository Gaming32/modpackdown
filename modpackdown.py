import io
import json
import shutil
import sys
import zipfile
from json.decoder import JSONDecodeError
from pathlib import Path
from typing import Optional, TypeVar, TypedDict, Union
from zipfile import BadZipFile, ZipFile

_T_ModPackDown = TypeVar('_T_ModPackDown', bound='ModPackDown')

InstalledModsCounter = dict[str, int]
FsOrZipPath = Union[Path, zipfile.Path]
LoadedModList = dict[str, tuple[str, FsOrZipPath]]
CachedModVersions = dict[str, tuple[str, str]]

if sys.platform == 'win32':
    _default_mods_dir = '~/Appdata/Roaming/.minecraft/mods'
elif sys.platform == 'darwin':
    _default_mods_folder = '~/Library/Application Support/minecraft'
else:
    _default_mods_folder = '~/.minecraft'
DEFAULT_MODS_DIR = Path(_default_mods_dir).expanduser()


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
        with ZipFile(pack_path) as pack_zip:
            zip_root = zipfile.Path(pack_zip)
            pack_mods = get_mod_versions(zip_root, self.version_id_cache)
            print('Identified', len(pack_mods), 'mods to maybe install')
            for (mod_id, (mod_version, mod_origin)) in pack_mods.items():
                if mod_id in self.current_mods:
                    if mod_id in self.packed_mods:
                        # Record this mod as installed again
                        self.packed_mods[mod_id] += 1
                        print(f'Skipped installation of mod {mod_id} as it was already installed from another pack')
                    else:
                        # Otherwise it's from the user
                        self.packed_mods[mod_id] = 2
                        print(f'Skipped installation of mod {mod_id} as it was already user installed')
                    skipped_count += 1
                else:
                    self.packed_mods[mod_id] = 1
                    with (
                            mod_origin.open('rb') as fp_from,
                            (self.mods_dir / mod_origin.name).open('wb') as fp_to
                        ):
                        shutil.copyfileobj(fp_from, fp_to)
                    print(f'Successfully installed mod {mod_id}:{mod_version}')
                    installed_count += 1
        print('Installed', installed_count, 'mods from this pack')
        if skipped_count:
            print(skipped_count, 'mods were skipped because they were already installed')

    def uninstall_pack(self, pack_path: Path) -> None:
        uninstalled_count = 0
        skipped_count = 0
        failed_count = 0
        with ZipFile(pack_path) as pack_zip:
            zip_root = zipfile.Path(pack_zip)
            pack_mods = get_mod_versions(zip_root, self.version_id_cache)
            print('Identified', len(pack_mods), 'mods to maybe uninstall')
            for (mod_id, (mod_version, mod_origin)) in pack_mods.items():
                if mod_id in self.packed_mods:
                    self.packed_mods[mod_id] -= 1
                    if self.packed_mods[mod_id] > 0:
                        print(f'Skipped uninstallation of mod {mod_id} as it was installed from somewhere else as well')
                        skipped_count += 1
                    else:
                        removal_path = self.mods_dir / mod_origin.name
                        try:
                            removal_path.unlink()
                        except FileNotFoundError:
                            print(f'Failed to uninstall {mod_id}:{mod_version} because it was missing')
                            failed_count += 1
                        else:
                            print(f'Successfully uninstalled mod {mod_id}:{mod_version}')
                            uninstalled_count += 1
                        self.current_mods.pop(mod_id, None)
                        self.packed_mods.pop(mod_id, None)
                else:
                    print(f'Failed to uninstall mod {mod_id} because it was not installed')
                    failed_count += 1
        print('Uninstalled', uninstalled_count, 'mods from this pack')
        if skipped_count:
            print(skipped_count, 'mods were skipped because they were installed from somewhere else')
        if failed_count:
            print(failed_count, 'mods failed to uninstall because they were missing or the installation state was inconsistent')
