"""测试 Glob 和 Grep 搜索工具。"""

import pytest
from pathlib import Path
from agentpal.tools.builtin_search import glob_files, grep_search


@pytest.mark.asyncio
async def test_glob_files_basic(tmp_path):
    """测试基本的 glob 文件匹配。"""
    # 创建测试文件
    (tmp_path / "test1.py").write_text("print('hello')")
    (tmp_path / "test2.py").write_text("print('world')")
    (tmp_path / "test.txt").write_text("text file")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "test3.py").write_text("print('nested')")

    # 测试匹配所有 Python 文件
    result = await glob_files("**/*.py", path=str(tmp_path))
    assert result.status == 0
    assert result.metadata["num_files"] == 3
    assert "test1.py" in result.content
    assert "test2.py" in result.content
    assert "test3.py" in result.content


@pytest.mark.asyncio
async def test_glob_files_limit(tmp_path):
    """测试 limit 参数。"""
    # 创建多个文件
    for i in range(10):
        (tmp_path / f"file{i}.txt").write_text(f"content {i}")

    result = await glob_files("*.txt", path=str(tmp_path), limit=5)
    assert result.status == 0
    assert result.metadata["num_files"] == 5
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_glob_files_no_matches(tmp_path):
    """测试无匹配结果。"""
    result = await glob_files("*.nonexistent", path=str(tmp_path))
    assert result.status == 0
    assert result.metadata["num_files"] == 0
    assert result.content == "No files found"


@pytest.mark.asyncio
async def test_glob_files_invalid_path():
    """测试无效路径。"""
    result = await glob_files("*.py", path="/nonexistent/path")
    assert result.status == 1
    assert "does not exist" in result.content


@pytest.mark.asyncio
async def test_grep_search_files_with_matches(tmp_path):
    """测试 grep 文件匹配模式。"""
    # 创建测试文件
    (tmp_path / "file1.py").write_text("def hello():\n    print('hello')")
    (tmp_path / "file2.py").write_text("def world():\n    print('world')")
    (tmp_path / "file3.txt").write_text("no match here")

    result = await grep_search("def ", path=str(tmp_path))
    assert result.status == 0
    assert "file1.py" in result.content
    assert "file2.py" in result.content
    assert "file3.txt" not in result.content


@pytest.mark.asyncio
async def test_grep_search_content_mode(tmp_path):
    """测试 grep 内容模式。"""
    test_file = tmp_path / "test.py"
    test_file.write_text("line 1\nline 2 ERROR\nline 3\nline 4")

    result = await grep_search(
        "ERROR",
        path=str(test_file),
        output_mode="content",
        show_line_numbers=True
    )
    assert result.status == 0
    assert "ERROR" in result.content
    assert ":2:" in result.content  # 行号


@pytest.mark.asyncio
async def test_grep_search_with_context(tmp_path):
    """测试带上下文的搜索。"""
    test_file = tmp_path / "test.py"
    test_file.write_text("line 1\nline 2\nERROR here\nline 4\nline 5")

    result = await grep_search(
        "ERROR",
        path=str(test_file),
        output_mode="content",
        context=1
    )
    assert result.status == 0
    assert "line 2" in result.content
    assert "ERROR" in result.content
    assert "line 4" in result.content


@pytest.mark.asyncio
async def test_grep_search_count_mode(tmp_path):
    """测试计数模式。"""
    test_file = tmp_path / "test.py"
    test_file.write_text("TODO: fix\nTODO: test\nDONE: complete")

    result = await grep_search(
        "TODO",
        path=str(test_file),
        output_mode="count"
    )
    assert result.status == 0
    assert "2" in result.content  # 2 个匹配
    assert "total occurrences" in result.content


@pytest.mark.asyncio
async def test_grep_search_case_insensitive(tmp_path):
    """测试忽略大小写。"""
    test_file = tmp_path / "test.py"
    test_file.write_text("Error\nerror\nERROR")

    result = await grep_search(
        "error",
        path=str(test_file),
        case_insensitive=True,
        output_mode="count"
    )
    assert result.status == 0
    assert "3" in result.content


@pytest.mark.asyncio
async def test_grep_search_with_glob_filter(tmp_path):
    """测试 glob 过滤。"""
    (tmp_path / "test.py").write_text("def hello(): pass")
    (tmp_path / "test.txt").write_text("def hello(): pass")

    result = await grep_search(
        "def ",
        path=str(tmp_path),
        glob="*.py"
    )
    assert result.status == 0
    assert "test.py" in result.content
    assert "test.txt" not in result.content


@pytest.mark.asyncio
async def test_grep_search_with_file_type(tmp_path):
    """测试文件类型过滤。"""
    (tmp_path / "test.py").write_text("import os")
    (tmp_path / "test.js").write_text("import os")

    result = await grep_search(
        "import",
        path=str(tmp_path),
        file_type="py"
    )
    assert result.status == 0
    assert "test.py" in result.content


@pytest.mark.asyncio
async def test_grep_search_no_matches(tmp_path):
    """测试无匹配结果。"""
    test_file = tmp_path / "test.py"
    test_file.write_text("no match here")

    result = await grep_search("NONEXISTENT", path=str(test_file))
    assert result.status == 0
    assert "No matches found" in result.content
