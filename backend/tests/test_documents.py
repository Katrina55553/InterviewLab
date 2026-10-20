"""Shared document helpers preserve the platform's extraction and privacy behavior."""

from docx import Document

from app.core.documents import extract_text, mask_raw_text, parse_demo_resume


def test_extract_txt_and_docx(tmp_path):
    text = "张三\nPython 后端工程师"
    txt = tmp_path / "resume.txt"
    txt.write_text(text, encoding="utf-8")
    assert extract_text(str(txt)) == text

    docx = tmp_path / "resume.docx"
    document = Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    document.save(docx)
    assert extract_text(str(docx)) == text


def test_mask_sensitive_text_before_storage_and_model_calls():
    text = "13812345678 zhangsan@example.com 110101199001011234"
    masked = mask_raw_text(text)
    assert masked == "138****5678 z**n@example.com 1101**********1234"
    assert mask_raw_text(masked) == masked


def test_demo_parser_preserves_resume_structure():
    result = parse_demo_resume("张三\nPython FastAPI Redis\n\n测试大学 本科")
    assert result["name"] == "张三"
    assert result["skills"] == [{"category": "技术栈", "skills": ["Python", "FastAPI", "Redis"]}]
    assert result["education"][0]["school"] == "测试大学 本科"
    assert result["work_experience"] == []
