"""The dependency boundary, enforced mechanically.

`unifi_runtime` is COPY'd into images whose interpreter we do not
control: Debian 11's python 3.9.2, with no pip and no venv. A third-party
import there is not a lint problem, it is an image that fails to boot — and
it would pass every test on a developer machine where the package happens
to be installed.

So this walks the AST rather than trusting anyone to remember the rule.
`unifi_containers`, `scripts/` and `tests/` are deliberately not checked; they may
use anything.
"""

import ast
import importlib.util
import os
import sys
import sysconfig

import pytest
from conftest import REPO_ROOT

SHIPPED = REPO_ROOT / "src" / "unifi_runtime"
STDLIB_DIR = os.path.realpath(sysconfig.get_paths()["stdlib"])

LOCAL_ROOTS = {"unifi_runtime"}

#: `_vendor/` holds third-party packages copied in verbatim (see its README).
#: They are not pip dependencies — they are COPY'd into the image along with
#: everything else — so the same rule applies to them, with their own top-level
#: name added. A vendored package that needed anything else could not be
#: vendored.
VENDORED_ROOTS = {"javaproperties"}


#: The init hooks are python too, and are executed at container boot, but they
#: are named for the hook runner and carry no extension — so an `*.py` glob
#: walks straight past them. That blind spot has already produced one false
#: negative here.
SHIPPED_HOOKS = sorted(
    [*REPO_ROOT.glob("network/**/demo-mode"), *REPO_ROOT.glob("unifi-os/**/demo-mode")]
)


def shipped_modules():
    return sorted(SHIPPED.rglob("*.py")) + SHIPPED_HOOKS


def is_vendored(path):
    if SHIPPED not in path.parents:
        return False
    return "_vendor" in path.relative_to(SHIPPED).parts


def import_roots(tree):
    """Every top-level module name this file imports."""
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative import, stays inside the package
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def is_stdlib(name):
    """True for a module that ships with CPython.

    Version-agnostic on purpose: `sys.stdlib_module_names` is 3.10+, and a
    hardcoded allowlist would drift. Resolving the spec and checking where
    it lives works on every interpreter we target. The site-packages guard
    matters because uv's python-build-standalone puts site-packages
    *underneath* the stdlib directory.
    """
    if name in sys.builtin_module_names:
        return True
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, ModuleNotFoundError):
        return False
    if spec is None:
        return False
    if spec.origin in ("built-in", "frozen"):
        return True
    if spec.origin is None:
        return False
    origin = os.path.realpath(spec.origin)
    return origin.startswith(STDLIB_DIR) and "site-packages" not in origin


def test_there_is_something_to_check():
    # A path typo that silently checks zero files would make this suite lie.
    assert len(shipped_modules()) >= 5


@pytest.mark.parametrize("path", shipped_modules(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_shipped_module_imports_stdlib_only(path):
    allowed = LOCAL_ROOTS | (VENDORED_ROOTS if is_vendored(path) else set())
    roots = import_roots(ast.parse(path.read_text(), filename=str(path)))
    offenders = sorted(name for name in roots if name not in allowed and not is_stdlib(name))
    assert not offenders, (
        f"{path.name} imports non-stdlib module(s) {offenders}. Everything under "
        "src/unifi_runtime/ is COPY'd into images with no pip — move this "
        "code to src/unifi_containers/, use the stdlib, or vendor it."
    )


def test_the_vendored_tree_is_actually_being_checked():
    # The exception above is a widened allowlist, not a skip. If _vendor/ ever
    # stops being walked, the rule silently stops applying to the one part of
    # the tree we did not write.
    vendored = [path for path in shipped_modules() if is_vendored(path)]
    assert vendored, "no vendored modules found; the _vendor/ path must have moved"


def module_name(path):
    return ".".join(path.relative_to(SHIPPED.parent).with_suffix("").parts)


def test_selfcheck_covers_every_shipped_module():
    """`selfcheck` is the only thing that runs shipped code on Debian's 3.9.2.

    A module missing from its list is a module the vendor interpreter never
    imports, which is where a 3.10-only slip survives all the way to a release.
    """
    from unifi_runtime import selfcheck

    listed = set(selfcheck.MODULES)
    missing = sorted(
        name
        for path in shipped_modules()
        if SHIPPED in path.parents and not is_vendored(path) and path.name != "__init__.py"
        for name in [module_name(path)]
        if name not in listed and name != "unifi_runtime.selfcheck"
    )
    assert not missing, f"{missing} are shipped but never imported by selfcheck"


def test_the_check_would_actually_catch_a_violation(tmp_path):
    # Guard against the assertion above passing because is_stdlib() says yes
    # to everything.
    assert not is_stdlib("requests_or_some_such_thing")
    offending = ast.parse("import requests\nfrom yaml import safe_load\n")
    assert import_roots(offending) == {"requests", "yaml"}


def test_no_shipped_file_imports_the_ci_package():
    """Files COPY'd into an image must not reach for `unifi_containers`.

    It is not installed there, and this catches the files a `*.py` glob misses:
    the init hooks are named `demo-mode`, with no extension, so a rename swept
    every module and left them importing a package that no longer existed. The
    hook died, no demo admin was seeded, and the sim image went unhealthy.
    """
    shipped = [
        *REPO_ROOT.glob("network/**/demo-mode"),
        *REPO_ROOT.glob("unifi-os/**/demo-mode"),
        *SHIPPED.rglob("*.py"),
    ]
    assert shipped, "found nothing to check"
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in shipped
        if "unifi_containers" in path.read_text()
    ]
    assert not offenders, f"these ship into an image and import unifi_containers: {offenders}"
