"""Shared document extraction, text masking, and explicit demo resume parsing."""

import re
from pathlib import Path


def _extract_text_pdf(file_path: str) -> str:
    """用 pdfplumber 提取 PDF 文本"""
    import pdfplumber
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def _extract_text_docx(file_path: str) -> str:
    """用 python-docx 提取 Word 文本"""
    from docx import Document
    doc = Document(file_path)
    return "\n".join(para.text for para in doc.paragraphs if para.text.strip())


def extract_text(file_path: str) -> str:
    """根据文件类型提取文本"""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return _extract_text_pdf(file_path)
    elif ext in (".docx", ".doc"):
        return _extract_text_docx(file_path)
    elif ext == ".txt":
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    return ""


def mask_raw_text(text: str) -> str:
    """脱敏简历原文中的手机号、邮箱、身份证号（发送给 LLM 前调用）"""
    # 先处理身份证，避免其中的连续数字被手机号规则局部替换后漏脱敏。
    text = re.sub(
        r"(\d{4})\d{10}(\d{4})",
        lambda m: m.group(1) + "**********" + m.group(2),
        text,
    )
    # 手机号：13812345678 → 138****5678
    text = re.sub(
        r"(1[3-9]\d)\d{4}(\d{4})",
        lambda m: m.group(1) + "****" + m.group(2),
        text,
    )
    # 邮箱：zhangsan@example.com → z**n@example.com
    def _mask_email_match(m):
        local, domain = m.group(1), m.group(2)
        if len(local) <= 2:
            return f"{local}@{domain}"
        masked = local[0] + "**" + local[-1]
        return f"{masked}@{domain}"
    text = re.sub(r"([\w.+-]+)@([\w-]+\.[\w.-]+)", _mask_email_match, text)
    return text


def parse_demo_resume(raw_text: str) -> dict:
    """显式演示模式的规则解析（不用于真实评分）

    用正则从简历原文中提取基本信息。
    """
    lines = raw_text.strip().split("\n")
    first_line = lines[0].strip() if lines else ""

    # 尝试提取姓名（第一行通常是姓名）
    name = first_line if len(first_line) <= 10 and not any(c.isdigit() for c in first_line) else None

    # 提取手机号
    phone_match = re.search(r"1[3-9]\d{9}", raw_text)
    phone = None
    if phone_match:
        p = phone_match.group()
        phone = p[:3] + "****" + p[7:]

    # 提取邮箱
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw_text)
    email = email_match.group() if email_match else None

    # 提取技能关键词（常见技术栈）
    tech_keywords = [
        "Python", "Java", "Go", "JavaScript", "TypeScript", "C++", "Rust",
        "React", "Vue", "Angular", "Node.js", "Django", "Flask", "FastAPI", "Spring",
        "MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch",
        "Docker", "Kubernetes", "K8s", "AWS", "Linux", "Git",
        "HTML", "CSS", "Tailwind", "Webpack", "Vite",
        "Kafka", "RabbitMQ", "Nginx", "GraphQL", "gRPC",
    ]
    found_skills = [kw for kw in tech_keywords if re.search(rf"\b{re.escape(kw)}\b", raw_text, re.IGNORECASE)]

    # 按段落拆分简历
    sections = re.split(r"\n\s*\n", raw_text)

    work_experience = []
    education = []
    projects = []

    for section in sections:
        s = section.strip()
        if not s:
            continue
        # 简单启发：包含"公司"或"实习"或"工程师"的段落 → 工作经历
        if any(kw in s for kw in ["公司", "实习", "工程师", "开发", "在职", "至今"]):
            work_experience.append({
                "company": s.split("\n")[0][:30] if "\n" in s else s[:30],
                "position": "",
                "start_date": None,
                "end_date": None,
                "description": s[:200],
                "achievements": [],
            })
        # 包含"大学"或"学院"或"学历" → 教育经历
        elif any(kw in s for kw in ["大学", "学院", "本科", "硕士", "博士", "学历"]):
            education.append({
                "school": s.split("\n")[0][:30],
                "degree": "",
                "major": "",
                "start_date": None,
                "end_date": None,
            })
        # 包含"项目" → 项目经历
        elif "项目" in s:
            projects.append({
                "name": s.split("\n")[0][:30],
                "role": None,
                "description": s[:200],
                "technologies": [],
                "achievements": [],
            })

    skills = [{"category": "技术栈", "skills": found_skills}] if found_skills else []

    return {
        "name": name,
        "phone": phone,
        "email": email,
        "summary": raw_text[:200] if raw_text else None,
        "work_experience": work_experience,
        "education": education,
        "projects": projects,
        "skills": skills,
        "certifications": [],
        "raw_text": raw_text[:2000],
    }
