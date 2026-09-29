#!/usr/bin/env sh
# ROUT III - find, or provision, a suitable CPython interpreter (Linux/POSIX).
#
# Sourceable:  . tools/ensure_python.sh; rout_ensure_python
# Runnable:     sh tools/ensure_python.sh   -> prints the interpreter path
#
# Messages are ASCII-only on purpose: a system with LANG=C would render
# non-ASCII text as mojibake (same rule as the Windows .bat launchers).
#
# Pinned interpreter: CPython 3.12.14 from python-build-standalone 20260924
# (install_only bundles). Nothing is installed system-wide: the tree is unpacked
# into a user directory, so no root and no distro package manager is needed.

ROUT_PY_VERSION=3.12.14
ROUT_PY_RELEASE=20260924
ROUT_PY_MIN_MAJOR=3
ROUT_PY_MIN_MINOR=12

# '%2B' is the URL-escaped '+' of the asset name.
ROUT_PY_ARCHIVE_PREFIX="cpython-3.12.14%2B20260924"
ROUT_PY_URL_BASE="https://github.com/astral-sh/python-build-standalone/releases/download/20260924"

ROUT_PY_SHA256_x86_64="5eae8cf79dd47fc2496a4fccc892936be831ce7a84d984b2299dfb1cdb592682"
ROUT_PY_SHA256_aarch64="c0574c9c9fec561eb192ad510e4e79aaad039929b3531b3bb873e2f630fb14a9"

ROUT_PY_CANDIDATES="python3.12 python3 python"

rout_die() {
    printf '%s\n' "$1" >&2
    exit 1
}

# Machine triple of the pinned standalone build, or empty when unsupported.
rout_arch_triple() {
    case "$(uname -m 2>/dev/null)" in
        x86_64|amd64)  printf 'x86_64-unknown-linux-gnu' ;;
        aarch64|arm64) printf 'aarch64-unknown-linux-gnu' ;;
        *)             printf '' ;;
    esac
}

rout_arch_sha256() {
    case "$1" in
        x86_64-unknown-linux-gnu)  printf '%s' "$ROUT_PY_SHA256_x86_64" ;;
        aarch64-unknown-linux-gnu) printf '%s' "$ROUT_PY_SHA256_aarch64" ;;
        *)                         printf '' ;;
    esac
}

# Directory that holds the provisioned interpreter tree.
rout_python_dir() {
    if [ -n "${ROUT_PYTHON_DIR:-}" ]; then
        printf '%s' "$ROUT_PYTHON_DIR"
    else
        printf '%s' "${XDG_DATA_HOME:-$HOME/.local/share}/ROUT/python/$ROUT_PY_VERSION"
    fi
}

# rout_python_usable <candidate>
# Prints the absolute path of the candidate and returns 0 when it is a
# regular (non free-threaded) CPython >= 3.12. Returns 1 otherwise.
rout_python_usable() {
    _cand=$1
    [ -n "$_cand" ] || return 1
    _path=$(command -v "$_cand" 2>/dev/null) || _path=''
    [ -n "$_path" ] || return 1
    [ -x "$_path" ] || return 1
    _info=$("$_path" -c 'import sys, sysconfig
print("%d %d %d" % (sys.version_info[0], sys.version_info[1], 1 if sysconfig.get_config_var("Py_GIL_DISABLED") else 0))' 2>/dev/null) || return 1
    [ -n "$_info" ] || return 1
    # shellcheck disable=SC2086
    set -- $_info
    [ $# -eq 3 ] || return 1
    if [ "$1" -gt "$ROUT_PY_MIN_MAJOR" ]; then
        printf '%s' "$_path"
        return 0
    fi
    if [ "$1" -lt "$ROUT_PY_MIN_MAJOR" ]; then
        return 1
    fi
    [ "$2" -ge "$ROUT_PY_MIN_MINOR" ] || return 1
    [ "$3" -eq 0 ] || return 1
    printf '%s' "$_path"
    return 0
}

# First usable interpreter: a provisioned one (ROUT_PYTHON_DIR) wins over the
# system PATH, so a hand-made prefix can pin a specific build.
rout_find_python() {
    if [ -n "${ROUT_PYTHON_DIR:-}" ]; then
        for _cand in "$ROUT_PYTHON_DIR/python/bin/python3" "$ROUT_PYTHON_DIR/bin/python3"; do
            if _found=$(rout_python_usable "$_cand"); then
                printf '%s' "$_found"
                return 0
            fi
        done
    fi
    for _cand in $ROUT_PY_CANDIDATES; do
        if _found=$(rout_python_usable "$_cand"); then
            printf '%s' "$_found"
            return 0
        fi
    done
    return 1
}

# sha256 of a file, using whichever coreutils/perl helper exists.
rout_sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        return 1
    fi
}

# Download + verify + unpack the pinned build into the user directory.
rout_install_python() {
    _dir=$(rout_python_dir)
    if [ "${ROUT_PYTHON_NO_INSTALL:-0}" = "1" ]; then
        rout_die "No suitable CPython >= $ROUT_PY_MIN_MAJOR.$ROUT_PY_MIN_MINOR found, and ROUT_PYTHON_NO_INSTALL=1 forbids the automatic download.
Install CPython 3.12+ manually (e.g. 'sudo apt install python3.12 python3.12-venv')
or unset ROUT_PYTHON_NO_INSTALL to let ROUT III provision it into $_dir."
    fi

    _triple=$(rout_arch_triple)
    if [ -z "$_triple" ]; then
        rout_die "Unsupported architecture '$(uname -m 2>/dev/null)': no pinned CPython $ROUT_PY_VERSION build for it.
Supported: x86_64, aarch64 (gnu). Install CPython 3.12+ manually, or point ROUT_PYTHON_DIR at a working interpreter."
    fi
    _sha=$(rout_arch_sha256 "$_triple")
    [ -n "$_sha" ] || rout_die "Internal error: no sha256 pinned for $_triple."

    if [ ! -x "$_dir/python/bin/python3" ]; then
        if command -v curl >/dev/null 2>&1; then
            _dl=curl
        elif command -v wget >/dev/null 2>&1; then
            _dl=wget
        else
            rout_die "No CPython >= $ROUT_PY_MIN_MAJOR.$ROUT_PY_MIN_MINOR found, and neither curl nor wget is installed.
Install one of them, or install CPython 3.12+ manually."
        fi
        command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || \
            rout_die "Neither sha256sum nor shasum found: cannot verify the downloaded interpreter. Install coreutils, or install CPython 3.12+ manually."
        command -v tar >/dev/null 2>&1 || \
            rout_die "tar not found: cannot unpack the downloaded interpreter. Install tar, or install CPython 3.12+ manually."

        _url="$ROUT_PY_URL_BASE/$ROUT_PY_ARCHIVE_PREFIX-$_triple-install_only.tar.gz"
        _tmp=$(mktemp -d) || rout_die "mktemp failed: cannot create a temporary directory."
        _archive="$_tmp/python.tar.gz"
        printf 'Downloading CPython %s (%s), ~65 MB ...\n' "$ROUT_PY_VERSION" "$_triple" >&2
        if [ "$_dl" = curl ]; then
            curl -fL --retry 2 -o "$_archive" "$_url" || {
                rm -rf "$_tmp"
                rout_die "Download failed (no network, or the pinned release is gone):
$_url
Install CPython 3.12+ manually, or re-run later when the network is available."
            }
        else
            wget -q -O "$_archive" "$_url" || {
                rm -rf "$_tmp"
                rout_die "Download failed (no network, or the pinned release is gone):
$_url
Install CPython 3.12+ manually, or re-run later when the network is available."
            }
        fi

        _got=$(rout_sha256_of "$_archive")
        if [ -z "$_got" ] || [ "$_got" != "$_sha" ]; then
            rm -rf "$_tmp"
            rout_die "Checksum mismatch: the downloaded archive is corrupt or was tampered with.
expected sha256 $_sha
actual   sha256 ${_got:-<none>}
Nothing was installed. Re-run when the network is reliable, or install CPython 3.12+ manually."
        fi

        printf 'Unpacking CPython %s into %s ...\n' "$ROUT_PY_VERSION" "$_dir" >&2
        tar xzf "$_archive" -C "$_tmp" || {
            rm -rf "$_tmp"
            rout_die "Failed to unpack the downloaded archive."
        }
        if [ ! -x "$_tmp/python/bin/python3" ]; then
            rm -rf "$_tmp"
            rout_die "Unpacked archive has no python/bin/python3: unexpected archive layout."
        fi
        mkdir -p "$_dir" || {
            rm -rf "$_tmp"
            rout_die "Cannot create $_dir."
        }
        rm -rf "$_dir/python"
        mv "$_tmp/python" "$_dir/python" || {
            rm -rf "$_tmp"
            rout_die "Cannot move the interpreter into $_dir/python."
        }
        rm -rf "$_tmp"
    fi

    _final="$_dir/python/bin/python3"
    if ! _check=$(rout_python_usable "$_final"); then
        rout_die "The provisioned interpreter $_final is not a usable CPython 3.12+.
Delete $_dir and re-run."
    fi
    printf '%s' "$_final"
}

# Echoes the interpreter to use: an already present one, or a fresh download.
rout_ensure_python() {
    if _found=$(rout_find_python); then
        printf '%s' "$_found"
        return 0
    fi
    rout_install_python
}

# Run only when executed, not when sourced (then $0 is the caller).
case "$0" in
    *ensure_python.sh) rout_ensure_python; printf '\n' ;;
esac
