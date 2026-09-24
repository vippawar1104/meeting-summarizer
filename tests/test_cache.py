import fakeredis

from app.cost.cache import ReviewCache
from app.github.diff import DiffLine, parse_diff
from app.pipeline.hunks import group_file, normalize_line


def cache():
    return ReviewCache(fakeredis.FakeAsyncRedis(), ttl_days=7)


def key(**over):
    args = {
        "prompt_version": "v1", "system": "sys", "section_norm": "3|add|''|x = 1",
        "rules": ["r"], "context": "", "feedback": "",
    }  # fmt: skip
    return ReviewCache.key(**{**args, **over})


def test_the_same_inputs_give_the_same_key():
    assert key() == key() and len(key()) == 64


def test_every_input_that_shapes_the_answer_changes_the_key():
    base = key()
    for over in (
        {"prompt_version": "v2"}, {"system": "other"}, {"section_norm": "3|add|''|x = 2"},
        {"rules": []}, {"context": "ctx"}, {"feedback": "fb"},
    ):  # fmt: skip
        assert key(**over) != base, over


def test_fields_cannot_be_shifted_into_each_other_to_collide():
    assert key(system="a", section_norm="b") != key(system="ab", section_norm="")


async def test_roundtrip_and_miss():
    c = cache()
    assert await c.get(1, "k") is None
    await c.put(1, "k", '{"findings": []}')
    assert await c.get(1, "k") == '{"findings": []}'


async def test_entries_expire():
    r = fakeredis.FakeAsyncRedis()
    await ReviewCache(r, ttl_days=7).put(1, "k", "x")
    [k] = await r.keys("cache:*")
    assert 6 * 86400 < await r.ttl(k) <= 7 * 86400


async def test_installations_never_share_cached_replies():
    c = cache()
    await c.put(1, "same-key", "tenant one's review")
    assert await c.get(2, "same-key") is None


async def test_clearing_one_installation_leaves_the_others():
    c = cache()
    await c.put(1, "a", "x")
    await c.put(1, "b", "x")
    await c.put(2, "c", "x")
    assert await c.clear_installation(1) == 2
    assert await c.get(1, "a") is None and await c.get(2, "c") == "x"


# ---- what counts as "the same code" -----------------------------------------------------------


def line(content, kind="add", n=5):
    return DiffLine(kind, None, n, content)


def test_spacing_inside_a_line_does_not_change_the_normalised_form():
    assert normalize_line(line("x=foo( a,b )")) == normalize_line(line("x = foo(a, b)"))
    assert normalize_line(line("total = a  +  b")) == normalize_line(line("total = a + b"))


def test_trailing_whitespace_is_ignored():
    assert normalize_line(line("x = 1   ")) == normalize_line(line("x = 1"))


def test_any_real_token_change_changes_the_form():
    base = normalize_line(line("if count > 0:"))
    for changed in (
        "if count >= 0:",
        "if count < 0:",
        "if count > 1:",
        "if counts > 0:",
        "if not count > 0:",
    ):
        assert normalize_line(line(changed)) != base, changed


def test_indentation_is_meaningful():
    assert normalize_line(line("    return x")) != normalize_line(line("return x"))
    assert normalize_line(line("\treturn x")) != normalize_line(line("    return x"))


def test_string_contents_are_meaningful_including_their_spacing():
    assert normalize_line(line('msg = "a  b"')) != normalize_line(line('msg = "a b"'))
    assert normalize_line(line("q = 'select 1'")) != normalize_line(line("q = 'select 2'"))


def test_line_number_and_kind_are_part_of_the_form():
    assert normalize_line(line("x = 1", n=5)) != normalize_line(line("x = 1", n=6))
    assert normalize_line(line("x = 1", kind="add")) != normalize_line(line("x = 1", kind="ctx"))


def diff_for(body):
    text = "".join(f"+{ln}\n" for ln in body)
    return f"diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -0,0 +1,{len(body)} @@\n{text}"


def norm(body):
    return group_file(parse_diff(diff_for(body))[0], 10_000)[0].norm_text


def test_a_reformatted_section_normalises_identically_but_a_changed_condition_does_not():
    assert norm(["def f(a,b):", "    return a+b"]) == norm(["def f( a, b ):", "    return  a + b"])
    assert norm(["if n > 0:", "    go()"]) != norm(["if n >= 0:", "    go()"])
