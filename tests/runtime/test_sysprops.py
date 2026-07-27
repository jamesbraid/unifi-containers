"""system.properties handling.

The file is read by the JVM, so the reference for every case below is what
`java.util.Properties` does with it — not what looks reasonable. The shell this
replaces built a sed expression per write; the hand-rolled parser that replaced
the shell only ever split on the first `=`. Most of these tests are inputs that
broke one or the other.
"""

from unifi_runtime import sysprops


def test_parses_entries_and_ignores_comments_and_blanks():
    text = "# a comment\n\nunifi.https.port=8443\n! another\nis_simulation=true\n"
    assert sysprops.parse(text) == {
        "unifi.https.port": "8443",
        "is_simulation": "true",
    }


def test_last_occurrence_wins_on_parse():
    assert sysprops.parse("k=first\nk=second\n") == {"k": "second"}


def test_merge_rewrites_in_place_and_keeps_comments():
    existing = "# keep me\nunifi.https.port=8443\ntrailing=yes\n"
    result = sysprops.merge(existing, {"unifi.https.port": 9443})
    assert result == "# keep me\nunifi.https.port=9443\ntrailing=yes\n"


def test_merge_appends_new_keys():
    result = sysprops.merge("existing=1\n", {"fresh": "2"})
    assert result == "existing=1\nfresh=2\n"


def test_merge_drops_a_stale_duplicate_of_a_rewritten_key():
    # Leaving the second line would let the old value win on the next read.
    result = sysprops.merge("k=old\nother=x\nk=older\n", {"k": "new"})
    assert result == "k=new\nother=x\n"


def test_merge_leaves_untouched_lines_byte_for_byte():
    # Reformatting a line we were not asked to change is how a file that
    # decides whether the controller boots gets corrupted.
    existing = "spaced.out :   value\ncontinued = one \\\n  two\n"
    result = sysprops.merge(existing, {"added": "1"})
    assert result == existing + "added=1\n"


def test_only_if_absent_leaves_existing_values_alone():
    result = sysprops.merge(
        "is_simulation=false\n", {"is_simulation": "true", "demo.num_uap": 3}, only_if_absent=True
    )
    assert result == "is_simulation=false\ndemo.num_uap=3\n"


# --- Java's separators, which are not just `=` ---


def test_a_colon_separates_a_key_from_its_value():
    assert sysprops.parse("unifi.https.port:8443\n") == {"unifi.https.port": "8443"}


def test_whitespace_alone_separates_a_key_from_its_value():
    assert sysprops.parse("unifi.https.port 8443\n") == {"unifi.https.port": "8443"}


def test_a_key_may_contain_an_escaped_separator():
    # `a\:b = c\=d` is the key `a:b` with the value `c=d`. Splitting on the
    # first `=` gives the key `a\:b ` and the value ` c\=d` — both wrong.
    assert sysprops.parse(r"a\:b = c\=d") == {"a:b": "c=d"}


def test_a_trailing_backslash_continues_the_line():
    text = "unifi.jvm.opts=-Xmx1024M \\\n    -XX:+UseParallelGC\n"
    assert sysprops.parse(text) == {"unifi.jvm.opts": "-Xmx1024M -XX:+UseParallelGC"}


def test_only_if_absent_sees_a_key_written_with_a_colon():
    # The controller writes some of these with `:`. Missing one appends a
    # duplicate and the file then has two answers for the same setting.
    result = sysprops.merge("is_simulation:false\n", {"is_simulation": "true"}, only_if_absent=True)
    assert result == "is_simulation:false\n"


# --- escaping on the way out ---


def test_a_key_containing_a_separator_is_escaped_on_write():
    # Written raw, `weird=key=value` reads back as the key `weird` — a
    # different setting from the one that was asked for.
    result = sysprops.merge("", {"weird=key": "value", "colon:key": "v2"})
    assert sysprops.parse(result) == {"weird=key": "value", "colon:key": "v2"}
    assert "weird\\=key=value" in result


def test_values_with_shell_and_regex_metacharacters_survive_a_round_trip():
    nasty = {
        "db.mongo.uri": "mongodb://user:p&ss@host:27017/db?opt=1",
        "path.like": "/unifi/data/../data",
        "backslashes": r"C:\Program Files\thing",
        "ampersand": "a & b",
        "percent": "100%",
        "dollar": "$HOME/x",
        "equals.in.value": "a=b=c",
        "hash.in.value": "not#a#comment",
        "leading.space": "   indented",
        "newline.in.value": "one\ntwo",
    }
    merged = sysprops.merge("", nasty)
    assert sysprops.parse(merged) == nasty
    # One entry per key: an unescaped newline or `#` would have split a line
    # or commented the rest of one out.
    assert len(merged.splitlines()) == len(nasty)


def test_a_key_with_regex_metacharacters_round_trips():
    merged = sysprops.merge("", {"unifi.https.port": "8443", "a.b*c[d]": "x"})
    assert sysprops.parse(merged)["a.b*c[d]"] == "x"


def test_non_ascii_is_written_as_a_unicode_escape():
    # Java reads this file as Latin-1 unless told otherwise, so a raw UTF-8
    # byte would come back as mojibake.
    merged = sysprops.merge("", {"console.name": "café"})
    assert merged == "console.name=caf\\u00e9\n"
    assert sysprops.parse(merged) == {"console.name": "café"}


def test_crlf_input_does_not_produce_duplicate_keys():
    result = sysprops.merge("k=old\r\n", {"k": "new"})
    assert sysprops.parse(result) == {"k": "new"}


def test_merge_of_nothing_onto_nothing_is_empty():
    assert sysprops.merge("", {}) == ""


def test_output_always_ends_with_a_newline():
    # Appending to a file that lacks one would otherwise join two keys.
    assert sysprops.merge("a=1", {"b": "2"}).endswith("\n")
