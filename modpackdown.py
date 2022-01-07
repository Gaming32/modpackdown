import io
import json
import shutil
import sys
import zipfile
from json.decoder import JSONDecodeError
from pathlib import Path
from typing import Optional, TypedDict, Union
from zipfile import BadZipFile, ZipFile

InstalledModsCounter = dict[str, int]
FsOrZipPath = Union[Path, zipfile.Path]
LoadedModList = dict[str, tuple[str, FsOrZipPath]]
CachedModVersions = dict[str, tuple[str, str]]


class BasicFabricModJson(TypedDict):
    schemaVersion: int
    id: str
    version: str


def _read_mod_version(zfp: ZipFile) -> Optional[tuple[str, str]]:
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


def _get_mod_versions(mods_dir: FsOrZipPath, cache: CachedModVersions) -> LoadedModList:
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
                    info = _read_mod_version(zfp)
            except BadZipFile:
                continue # Not a valid JAR file
            if info is None:
                continue # Is a JAR file, but isn't a mod
            mod_id, mod_version = info
            cache[file.name] = info
            result[mod_id] = (mod_version, file)
    return result


def install_pack(
    mods_dir: Path,
    installed_packs: InstalledModsCounter,
    current_mods: LoadedModList,
    pack_path: Path,
    version_id_cache: CachedModVersions
) -> None:
    installed_count = 0
    skipped_count = 0
    with ZipFile(pack_path) as pack_zip:
        zip_root = zipfile.Path(pack_zip)
        pack_mods = _get_mod_versions(zip_root, version_id_cache)
        print('Identified', len(pack_mods), 'mods to maybe install')
        for (mod_id, (mod_version, mod_origin)) in pack_mods.items():
            if mod_id in current_mods:
                if mod_id in installed_packs:
                    # Record this mod as installed again
                    installed_packs[mod_id] += 1
                    print(f'Skipped installation of mod {mod_id} as it was already installed from another pack')
                else:
                    # Otherwise it's from the user
                    installed_packs[mod_id] = 2
                    print(f'Skipped installation of mod {mod_id} as it was already user installed')
                skipped_count += 1
            else:
                installed_packs[mod_id] = 1
                with (
                        mod_origin.open('rb') as fp_from,
                        (mods_dir / mod_origin.name).open('wb') as fp_to
                    ):
                    shutil.copyfileobj(fp_from, fp_to)
                print(f'Successfully installed mod {mod_id}:{mod_version}')
                installed_count += 1
    print('Installed', installed_count, 'mods from this pack')
    if skipped_count:
        print(skipped_count, 'mods were skipped because they were already installed')


def main() -> None:
    mods_dir = Path('~/AppData/Roaming/.minecraft/mods').expanduser()
    cache_file = mods_dir / 'modpackdown_cache.json'
    installed_packs_file = mods_dir / 'modpackdown_data.json'
    version_id_cache: CachedModVersions
    installed_packs: InstalledModsCounter
    try:
        with open(cache_file) as fp:
            version_id_cache = json.load(fp)
    except (FileNotFoundError, UnicodeDecodeError, JSONDecodeError):
        version_id_cache = {}
    try:
        with open(installed_packs_file) as fp:
            installed_packs = json.load(fp)
    except (FileNotFoundError, UnicodeDecodeError, JSONDecodeError):
        installed_packs = {}

    current_mods = _get_mod_versions(mods_dir, version_id_cache)
    print('Identified', len(current_mods), 'currently installed mods')
    if sys.argv[1] == 'install':
        install_pack(
            mods_dir,
            installed_packs,
            current_mods,
            Path(sys.argv[2]).expanduser(),
            version_id_cache
        )

    with open(cache_file, 'w') as fp:
        json.dump(version_id_cache, fp)
    with open(installed_packs_file, 'w') as fp:
        json.dump(installed_packs, fp)


if __name__ == '__main__':
    main()
