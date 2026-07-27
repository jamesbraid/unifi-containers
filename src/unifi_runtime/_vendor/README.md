# Vendored packages

| package | version | licence | upstream |
|---|---|---|---|
| `javaproperties` | 0.8.2 | MIT (`javaproperties/LICENSE`) | <https://github.com/jwodder/javaproperties> |

`unifi_runtime` is COPY'd into images running Debian's python 3.9.2 with no pip
and no venv, so a dependency it cannot install is a dependency it cannot have.
`javaproperties` has no dependencies of its own and supports python 3.8+, so a
copy in the tree is the whole install step.

It backs `unifi_runtime.sysprops`, which edits the `system.properties` file that
decides whether a controller boots. Java's format has escaping rules — `\:`,
`\=`, line continuations, `\uXXXX` — and a parser that gets them wrong corrupts
that file rather than failing loudly.

Copied unmodified from the 0.8.2 wheel, `py.typed` and licence included. To
update, replace the directory contents and bump the version above; there is no
patch to carry forward.
