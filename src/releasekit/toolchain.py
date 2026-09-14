"""Pinned third-party engines behind the single release-kit command.

Adopting repositories should not each own download snippets, versions, checksums, or
platform switches.  release-kit resolves the current platform, downloads an official
archive, verifies its release SHA-256, extracts only the expected executable, and
checks the executable's self-reported version before it is used.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import storage


class ToolchainError(RuntimeError):
    """A supported tool could not be resolved or verified."""


@dataclass(frozen=True)
class Asset:
    filename: str
    sha256: str
    # The executable's own digest, pinned beside the archive's. Deriving it from the
    # archive meant unpacking the archive on every run to learn what to expect, and an
    # expectation computed from the artefact under test is weaker than a stated one.
    executable_sha256: str


@dataclass(frozen=True)
class Tool:
    name: str
    version: str
    repository: str
    release: str
    executable: str
    assets: dict[tuple[str, str], Asset]

    def url(self, asset: Asset) -> str:
        return f"https://github.com/{self.repository}/releases/download/{self.release}/{asset.filename}"


BETTERLEAKS = Tool(
    name="betterleaks",
    version="1.8.1",
    repository="betterleaks/betterleaks",
    release="v1.8.1",
    executable="betterleaks.exe" if sys.platform == "win32" else "betterleaks",
    assets={
        ("darwin", "arm64"): Asset(
            "betterleaks_1.8.1_darwin_arm64.tar.gz",
            "8e80f33b5f2a7426b390347b9fd466033723cb94b6bdffa7572632e2eaec964e",
            "a4808e33f9e9a405198dd7196496a5777023ae1f03eac59ecf24b3e787e344d2",
        ),
        ("darwin", "x64"): Asset(
            "betterleaks_1.8.1_darwin_x64.tar.gz",
            "6abc37df76f881cffae406aa2cec72bea6e6ae64b4e771b3ed21b4aac472ed10",
            "d0aa388e456ca3eec1bc2d136fe1ac61e29d0a672dffca9f5dcd0e354fd8b959",
        ),
        ("linux", "arm64"): Asset(
            "betterleaks_1.8.1_linux_arm64.tar.gz",
            "bbb578b12a2f65d7082ab436abf37724232bc71d8a078e3c41336574420f1b48",
            "1d5e40e7ea9070393744a34f0334c435edda1a230989974734fda96fe987001b",
        ),
        ("linux", "x64"): Asset(
            "betterleaks_1.8.1_linux_x64.tar.gz",
            "efa407244e1ea8e35f582b8a42becdeac08bdead04f68eb752adda722d583c2a",
            "380a770d9ea9215e7d3b964246a72d8698b2975496addef2627d0e82b174a1f8",
        ),
        ("windows", "arm64"): Asset(
            "betterleaks_1.8.1_windows_arm64.zip",
            "aa12beb9ce1f6a911da91e1d0d8a72d7e68daf56a52a53f930038fd81f10f0ba",
            "b4b4d88ab5cc3942a10fe85f27c9443a3c6319c2963670d2afb438e36ff915fc",
        ),
        ("windows", "x64"): Asset(
            "betterleaks_1.8.1_windows_x64.zip",
            "94310d028285a1bcce7f160bc19eb62f87de6460c95bfd4319151ef5b501ed3f",
            "727820f1a9f9264319cc50458b99f01acbb93e93df7a39b8eaa2715a7b1aaed5",
        ),
    },
)

LYCHEE = Tool(
    name="lychee",
    version="0.24.2",
    repository="lycheeverse/lychee",
    release="lychee-v0.24.2",
    executable="lychee.exe" if sys.platform == "win32" else "lychee",
    assets={
        ("darwin", "arm64"): Asset(
            "lychee-aarch64-apple-darwin.tar.gz",
            "c9d3740ea2d891854d37116c9fba840f37b6e7c89d330e7db84ac333631c4977",
            "af111b5890746a863e60b4929720ab383337843ed15b701515871aa6e8817c2b",
        ),
        ("darwin", "x64"): Asset(
            "lychee-x86_64-apple-darwin.tar.gz",
            "887503a9cff667d322b8d0892b40bf49976eb9507af8483220a3706cdad55978",
            "db236f940ad0dbe02b8c3e12ac0f7b7dcae15be02bb5fb1868def5026b5e01e7",
        ),
        ("linux", "arm64"): Asset(
            "lychee-aarch64-unknown-linux-gnu.tar.gz",
            "91a7bd65685da41b90ccb9bc867a3d649a7818042dae04ff405e55a25bddee4c",
            "7674d743f60997b0f2c6d6dbf4e02b50b311d8fe6c0051b11ad085c3f244e98a",
        ),
        ("linux", "x64"): Asset(
            "lychee-x86_64-unknown-linux-gnu.tar.gz",
            "1f4e0ef7f6554a6ed33dd7ac144fb2e1bbed98598e7af973042fc5cd43951c9a",
            "87e6e75195df5753f08c53b5c0a13694b8328edd8df009914ae9e29b5000162d",
        ),
        ("windows", "x64"): Asset(
            "lychee-x86_64-pc-windows-msvc.zip",
            "32975d1493ee1a975d6bb41e4fb56fe419cb442ded628bb772ba2e614acfacad",
            "2d15a3f78ac680103720b0c59667dc6f1da7de35f7e9c95171bdf4b76fb6829b",
        ),
    },
)

TOOLS = {tool.name: tool for tool in (BETTERLEAKS, LYCHEE)}


def _windows_machine() -> str:
    """Query Windows when Python cannot identify it from the inherited environment."""
    import ctypes

    class SystemInfo(ctypes.Structure):
        _fields_ = [
            ("wProcessorArchitecture", ctypes.c_uint16),
            ("wReserved", ctypes.c_uint16),
            ("dwPageSize", ctypes.c_uint32),
            ("lpMinimumApplicationAddress", ctypes.c_void_p),
            ("lpMaximumApplicationAddress", ctypes.c_void_p),
            ("dwActiveProcessorMask", ctypes.c_size_t),
            ("dwNumberOfProcessors", ctypes.c_uint32),
            ("dwProcessorType", ctypes.c_uint32),
            ("dwAllocationGranularity", ctypes.c_uint32),
            ("wProcessorLevel", ctypes.c_uint16),
            ("wProcessorRevision", ctypes.c_uint16),
        ]

    try:
        query = ctypes.WinDLL("kernel32", use_last_error=True).GetNativeSystemInfo
        query.argtypes = [ctypes.POINTER(SystemInfo)]
        query.restype = None
        info = SystemInfo(wProcessorArchitecture=0xFFFF)
        query(ctypes.byref(info))
    except (AttributeError, OSError) as error:
        raise ToolchainError(f"could not determine Windows architecture: {error}") from error
    return {9: "amd64", 12: "arm64", 0: "x86", 5: "arm", 6: "ia64"}.get(
        info.wProcessorArchitecture, f"unknown-{info.wProcessorArchitecture}"
    )


def _platform_key() -> tuple[str, str]:
    operating_system = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    if operating_system == "windows" and not machine:
        machine = _windows_machine()
    architecture = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, machine)
    return operating_system, architecture


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "release-kit"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _archive_member_names(path: Path) -> tuple[str, ...]:
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return tuple(archive.namelist())
    with tarfile.open(path, "r:gz") as archive:
        return tuple(member.name for member in archive.getmembers() if member.isfile())


def _archive_executable_sha256(archive_path: Path, executable_name: str) -> str:
    """Hash the executable inside a release archive without running it."""
    candidates = [
        name
        for name in _archive_member_names(archive_path)
        if PurePosixPath(name.replace("\\", "/")).name == executable_name
    ]
    if len(candidates) != 1:
        raise ToolchainError(
            f"{archive_path.name} contains {len(candidates)} copies of {executable_name}"
        )
    member = candidates[0]
    digest = hashlib.sha256()
    if archive_path.name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive, archive.open(member) as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    else:
        with tarfile.open(archive_path, "r:gz") as archive:
            source = archive.extractfile(member)
            if source is None:
                raise ToolchainError(f"could not read {member} from {archive_path.name}")
            with source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def _extract_executable(archive_path: Path, executable_name: str, destination: Path) -> None:
    candidates = [
        name
        for name in _archive_member_names(archive_path)
        if PurePosixPath(name.replace("\\", "/")).name == executable_name
    ]
    if len(candidates) != 1:
        raise ToolchainError(
            f"{archive_path.name} contains {len(candidates)} copies of {executable_name}"
        )
    member = candidates[0]
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}-",
        suffix=".partial",
        dir=destination.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        if archive_path.name.endswith(".zip"):
            with (
                zipfile.ZipFile(archive_path) as archive,
                archive.open(member) as source,
                temporary.open("wb") as output,
            ):
                shutil.copyfileobj(source, output)
        else:
            with tarfile.open(archive_path, "r:gz") as archive:
                source = archive.extractfile(member)
                if source is None:
                    raise ToolchainError(f"could not read {member} from {archive_path.name}")
                with source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output)
        temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _verify(tool: Tool, executable: Path) -> None:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolchainError(f"could not run {executable}: {error}") from error
    output = (result.stdout + result.stderr).strip()
    version = re.compile(rf"(?<![0-9.]){re.escape(tool.version)}(?![0-9.])")
    if result.returncode != 0 or not version.search(output):
        raise ToolchainError(
            f"unexpected {tool.name} executable at {executable}: {output or 'no version output'}"
        )


# Engines resolved in this process, keyed by everything that decides the answer.
# Verification itself is unchanged; an identical request simply is not repeated within
# one command. Nothing is remembered across runs, and no metadata is trusted.
_RESOLVED: dict[tuple[str, str, bool, str, str], Path] = {}


def forget_resolved_engines() -> None:
    """Drop the per-process resolution cache; tests change overrides in place."""
    _RESOLVED.clear()


def resolve(name: str, *, root: Path, allow_download: bool = True) -> Path:
    """Return a verified executable, provisioning the pinned official release once."""
    key = (
        name,
        str(root),
        allow_download,
        os.environ.get(f"RELKIT_{name.upper()}", ""),
        os.environ.get("RELKIT_CACHE_DIR", ""),
    )
    if (cached := _RESOLVED.get(key)) is not None:
        return cached
    executable = _resolve(name, root=root, allow_download=allow_download)
    _RESOLVED[key] = executable
    return executable


def prepare(names: Sequence[str], *, root: Path, allow_download: bool = True) -> None:
    """Provision engines together: they are independent, and each verifies its own file.

    Verification is dominated by hashing and by running the executable once, both of
    which release the interpreter lock, so overlapping two engines removes roughly the
    smaller of the two from the wall clock.
    """
    if len(names) < 2:
        for name in names:
            resolve(name, root=root, allow_download=allow_download)
        return
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = [
            pool.submit(resolve, name, root=root, allow_download=allow_download) for name in names
        ]
        for future in futures:
            future.result()


def _resolve(name: str, *, root: Path, allow_download: bool = True) -> Path:
    root = storage.checked(root)
    tool = TOOLS[name]
    override = os.environ.get(f"RELKIT_{name.upper()}")
    if override:
        executable = Path(override).expanduser().resolve()
        _verify(tool, executable)
        return executable

    key = _platform_key()
    try:
        asset = tool.assets[key]
    except KeyError:
        raise ToolchainError(f"{tool.name} {tool.version} has no pinned asset for {key}") from None
    cache_root = Path(os.environ.get("RELKIT_CACHE_DIR", root / ".cache" / "release-kit"))
    if not cache_root.is_absolute():
        cache_root = root / cache_root
    storage.checked(cache_root)
    destination = cache_root / tool.name / tool.version / tool.executable
    archive_path = destination.parent / asset.filename
    if not archive_path.is_file():
        if not allow_download:
            raise ToolchainError(
                f"verified archive for {tool.name} {tool.version} is not cached at {archive_path}"
            )
        storage.cache_write_path(root, destination.parent)
        destination.parent.mkdir(parents=True, exist_ok=True)
        storage.checked(archive_path)
        with tempfile.NamedTemporaryFile(
            prefix=f"{tool.name}-",
            suffix=Path(asset.filename).suffix,
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary_archive = Path(handle.name)
        try:
            _download(tool.url(asset), temporary_archive)
            observed = _sha256(temporary_archive)
            if observed != asset.sha256:
                raise ToolchainError(
                    f"SHA-256 mismatch for {asset.filename}: expected {asset.sha256}, got {observed}"
                )
            temporary_archive.replace(archive_path)
        finally:
            temporary_archive.unlink(missing_ok=True)

    # What every run must establish is that this executable is the pinned one, and the
    # pin states that directly. The archive only matters when the executable has to be
    # produced from it, so hashing and unpacking it belong in that branch: a cached run
    # used to hash the whole archive and unpack it again to recompute a value already
    # known. One digest of the executable then decides, instead of one per branch.
    cached = destination.is_file()
    if not cached:
        observed_archive = _sha256(archive_path)
        if observed_archive != asset.sha256:
            raise ToolchainError(
                f"SHA-256 mismatch for cached {asset.filename}: "
                f"expected {asset.sha256}, got {observed_archive}"
            )
        storage.cache_write_path(root, destination)
        _extract_executable(archive_path, tool.executable, destination)
    if _sha256(destination) != asset.executable_sha256:
        raise ToolchainError(
            f"SHA-256 mismatch for {'cached' if cached else 'extracted'} "
            f"executable at {destination}"
        )
    _verify(tool, destination)
    return destination


def versions() -> str:
    return f"Betterleaks {BETTERLEAKS.version}, Lychee {LYCHEE.version}"
