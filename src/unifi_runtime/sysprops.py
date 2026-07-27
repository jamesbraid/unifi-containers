"""Read and update UniFi's `system.properties`.

It is a Java `.properties` file, read by the JVM with Java's escaping rules, so
it is handled by a Java `.properties` library — see `_vendor/README.md`.
"""

from ._vendor.javaproperties import PropertiesFile, loads


def parse(text):
    """`key -> value` for every entry, unescaped. Last occurrence wins.

    Java's separators are `=`, `:` and whitespace, and a trailing backslash
    continues the line.
    """
    return dict(loads(text or ""))


def merge(existing, updates, only_if_absent=False):
    """Return `existing` with `updates` applied, rewriting keys where they sit.

    Comments, blank lines and untouched entries keep their exact original text.
    A rewritten key loses its later duplicates, which would otherwise win on
    the next read.
    """
    props = PropertiesFile.loads(existing or "")
    for key, value in updates.items():
        if only_if_absent and key in props:
            continue
        props[key] = str(value)
    return props.dumps()
