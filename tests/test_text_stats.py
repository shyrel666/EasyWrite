"""正文字数口径：与前端 utils/wordCount.js 一致（共用 frontend/src/utils/__tests__/wordCountSample.js 的样例）"""
import re
from pathlib import Path

from app.models.schemas import OutlineNode
from app.services.checker.section_check import _check_quality
from app.services.text_stats import body_chars

SAMPLE_JS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "utils" / "__tests__" / "wordCountSample.js"


def _sample():
    source = SAMPLE_JS.read_text(encoding="utf-8")
    text = re.search(r"SAMPLE = `((?:\\.|[^`\\])*)`", source).group(1).replace("\\`", "`")
    return text, int(re.search(r"SAMPLE_BODY_CHARS = (\d+)", source).group(1))


def test_body_chars_matches_frontend_sample():
    text, expected = _sample()
    assert "```mermaid" in text
    assert body_chars(text) == expected


def test_unclosed_code_block_and_empty():
    assert body_chars("正文\n```mermaid\ngraph TD\nA-->B") == 2
    assert body_chars("") == 0 and body_chars(None) == 0


def test_length_check_ignores_diagram_code():
    """Mermaid 图代码不算正文篇幅：图很长时也不因此判为超出预算"""
    diagram = "```mermaid\ngraph TD\n" + "\n".join(f"  N{i}[节点{i}] --> N{i + 1}[节点{i + 1}]" for i in range(40)) + "\n```"
    text = "运维" * 500 + "\n\n" + diagram
    issues = _check_quality(text, OutlineNode(id="s", title="1.1 运维", word_budget=1000))
    assert not [i for i in issues if i.code == "length"]
