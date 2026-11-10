"""Installing and removing plugins in the user's plugin folder.

A plugin is installed by copying its folder (or the folder inside a .zip) into
:func:`patchbay.config.user_plugin_dir`. Nothing in the plugin is run while it
is installed; its code is loaded the first time one of its devices is used.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from .config import user_plugin_dir
from .plugin import PluginError, PluginInfo, read_manifest

TRUST_WARNING = ("Plugins are Python code and run with your permissions. Install only "
                 "plugins from sources you trust.")


def find_plugin_folder(root: Path) -> Path:
    """The folder holding plugin.toml: ``root`` itself or a single folder inside it."""
    if (root / "plugin.toml").is_file():
        return root
    inner = [p for p in root.iterdir()
             if p.is_dir() and not p.name.startswith((".", "__MACOSX"))]
    if len(inner) == 1 and (inner[0] / "plugin.toml").is_file():
        return inner[0]
    raise PluginError(f"no plugin.toml in {root}")


def check_plugin(folder: Path) -> PluginInfo:
    """Read the manifest and check that every driver it names has a source file."""
    info = read_manifest(folder)
    missing = set()
    for device in info.devices:
        for ref in filter(None, (device.driver, device.simulator)):
            module = ref.partition(":")[0]
            path = folder.joinpath(*module.split("."))
            if not (path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()):
                missing.add(f"{module}.py")
    if missing:
        raise PluginError(f"plugin '{info.id}' is missing {', '.join(sorted(missing))}")
    return info


def _extract(archive: Path, target: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as zf:
            root = target.resolve()
            for name in zf.namelist():
                dest = (target / name).resolve()
                if root != dest and root not in dest.parents:
                    raise PluginError(f"{archive.name}: unsafe path in the archive: {name}")
            zf.extractall(target)
    except zipfile.BadZipFile as exc:
        raise PluginError(f"{archive.name} is not a .zip file: {exc}") from exc


def installed_folder(plugin_id: str) -> Path | None:
    """The user-plugin folder that currently holds ``plugin_id``, if any."""
    root = user_plugin_dir()
    if not root.is_dir():
        return None
    for folder in sorted(root.iterdir()):
        if (folder / "plugin.toml").is_file():
            try:
                if read_manifest(folder).id == plugin_id:
                    return folder
            except PluginError:
                continue
    return None


def peek(source: Path) -> PluginInfo:
    """Check a plugin folder or .zip without installing it."""
    source = Path(source)
    if source.is_file():
        with tempfile.TemporaryDirectory() as tmp:
            _extract(source, Path(tmp))
            info = check_plugin(find_plugin_folder(Path(tmp)))
            info.path = source
            return info
    return check_plugin(find_plugin_folder(source))


def install(source: Path, replace: bool = False) -> PluginInfo:
    """Copy a plugin folder or .zip into the user plugin folder.

    Fails if a plugin with the same id is installed already, unless ``replace``.
    """
    source = Path(source)
    with tempfile.TemporaryDirectory() as tmp:
        if source.is_file():
            _extract(source, Path(tmp))
            folder = find_plugin_folder(Path(tmp))
        elif source.is_dir():
            folder = find_plugin_folder(source)
        else:
            raise PluginError(f"{source} does not exist")
        info = check_plugin(folder)
        existing = installed_folder(info.id)
        if existing is not None and not replace:
            raise PluginError(f"plugin '{info.id}' is installed already, in {existing}")
        root = user_plugin_dir()
        dest = root / re.sub(r"[^\w.-]", "_", info.id)
        if existing is not None:
            shutil.rmtree(existing)
        if dest.exists():
            shutil.rmtree(dest)
        root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(folder, dest,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    forget_modules(info.id)
    return read_manifest(dest)


def is_removable(info: PluginInfo) -> bool:
    """Only plugins in the user plugin folder are removed by the app."""
    root = user_plugin_dir().resolve()
    return Path(info.path).resolve().parent == root


def remove(info: PluginInfo, trash=None) -> None:
    """Delete an installed plugin's folder. ``trash(path) -> bool`` may move it to the
    recycle bin instead; the folder is deleted only if that returns False."""
    if not is_removable(info):
        raise PluginError(f"'{info.name}' is not an installed plugin; remove {info.path} by hand")
    if trash is None or not trash(Path(info.path)):
        shutil.rmtree(info.path)
    forget_modules(info.id)


def forget_modules(plugin_id: str | None = None) -> None:
    """Drop loaded plugin code so the next use loads it again from disk."""
    prefix = "patchbay_plugin_" + (re.sub(r"\W", "_", plugin_id) if plugin_id else "")
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + ("." if plugin_id else "")):
            del sys.modules[name]
