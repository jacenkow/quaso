from __future__ import annotations

import pytest

from quaso.tools.base import ToolContext, ToolError
from quaso.tools.fs import (
    EditFile,
    EditFileParams,
    ListDir,
    ListDirParams,
    ReadFile,
    ReadFileParams,
    WriteFile,
    WriteFileParams,
)


@pytest.fixture
def ctx(tmp_path):
    """Context with the read-before-edit guard off (exercised separately)."""
    return ToolContext(cwd=tmp_path, require_read_before_edit=False)


@pytest.fixture
def guarded_ctx(tmp_path):
    return ToolContext(cwd=tmp_path, require_read_before_edit=True)


@pytest.mark.asyncio
async def test_read_file_numbers_lines(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\n")
    out = await ReadFile().run(ReadFileParams(path="a.txt"), ctx)
    assert "1\talpha" in out and "2\tbeta" in out


@pytest.mark.asyncio
async def test_read_file_offset_limit(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("\n".join(f"line{i}" for i in range(10)))
    out = await ReadFile().run(
        ReadFileParams(path="a.txt", offset=2, limit=3), ctx
    )
    assert "line2" in out and "line5" not in out
    assert "more lines" in out


@pytest.mark.asyncio
async def test_read_missing_file(ctx):
    with pytest.raises(ToolError):
        await ReadFile().run(ReadFileParams(path="nope.txt"), ctx)


@pytest.mark.asyncio
async def test_list_dir(ctx, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "f.txt").write_text("x")
    out = await ListDir().run(ListDirParams(path="."), ctx)
    assert "sub/" in out and "f.txt" in out


@pytest.mark.asyncio
async def test_write_then_edit(ctx, tmp_path):
    await WriteFile().run(
        WriteFileParams(path="new/deep.txt", content="hello world"), ctx
    )
    target = tmp_path / "new" / "deep.txt"
    assert target.read_text() == "hello world"

    await EditFile().run(
        EditFileParams(
            path="new/deep.txt", old_string="world", new_string="quaso"
        ),
        ctx,
    )
    assert target.read_text() == "hello quaso"


@pytest.mark.asyncio
async def test_edit_requires_unique_match(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("x x")
    with pytest.raises(ToolError, match="2 times"):
        await EditFile().run(
            EditFileParams(path="a.txt", old_string="x", new_string="y"), ctx
        )
    out = await EditFile().run(
        EditFileParams(
            path="a.txt", old_string="x", new_string="y", replace_all=True
        ),
        ctx,
    )
    assert "2 replacement" in out
    assert (tmp_path / "a.txt").read_text() == "y y"


@pytest.mark.asyncio
async def test_edit_missing_string(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("abc")
    with pytest.raises(ToolError, match="not found"):
        await EditFile().run(
            EditFileParams(path="a.txt", old_string="zzz", new_string="y"), ctx
        )


@pytest.mark.asyncio
async def test_edit_without_reading_is_blocked(guarded_ctx, tmp_path):
    (tmp_path / "a.txt").write_text("abc")
    with pytest.raises(ToolError, match="must read"):
        await EditFile().run(
            EditFileParams(path="a.txt", old_string="abc", new_string="xyz"),
            guarded_ctx,
        )
    assert (tmp_path / "a.txt").read_text() == "abc"


@pytest.mark.asyncio
async def test_edit_after_reading_is_allowed(guarded_ctx, tmp_path):
    (tmp_path / "a.txt").write_text("abc")
    await ReadFile().run(ReadFileParams(path="a.txt"), guarded_ctx)
    await EditFile().run(
        EditFileParams(path="a.txt", old_string="abc", new_string="xyz"),
        guarded_ctx,
    )
    assert (tmp_path / "a.txt").read_text() == "xyz"


@pytest.mark.asyncio
async def test_edit_blocked_when_file_changed_underneath(
    guarded_ctx, tmp_path
):
    target = tmp_path / "a.txt"
    target.write_text("abc")
    await ReadFile().run(ReadFileParams(path="a.txt"), guarded_ctx)
    target.write_text(
        "abc changed by someone else"
    )  # different size → detected
    with pytest.raises(ToolError, match="changed on disk"):
        await EditFile().run(
            EditFileParams(path="a.txt", old_string="abc", new_string="xyz"),
            guarded_ctx,
        )


@pytest.mark.asyncio
async def test_new_file_creation_is_not_blocked(guarded_ctx, tmp_path):
    out = await WriteFile().run(
        WriteFileParams(path="brand_new.txt", content="hi"), guarded_ctx
    )
    assert "Created" in out


@pytest.mark.asyncio
async def test_overwriting_unread_file_is_blocked(guarded_ctx, tmp_path):
    (tmp_path / "a.txt").write_text("original")
    with pytest.raises(ToolError, match="must read"):
        await WriteFile().run(
            WriteFileParams(path="a.txt", content="clobber"), guarded_ctx
        )
    assert (tmp_path / "a.txt").read_text() == "original"
