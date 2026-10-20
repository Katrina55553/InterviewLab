"""文件上传安全校验

四重校验：扩展名、MIME 类型、魔数、文件大小。
"""

from fastapi import HTTPException, UploadFile

from app.core.config import settings

# 文件魔数映射
FILE_SIGNATURES = {
    "pdf": [b"%PDF"],
    "docx": [b"PK\x03\x04"],  # ZIP 格式（DOCX 是 ZIP 包）
    "doc": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],  # OLE2 格式
    "txt": [],  # 纯文本无固定魔数，跳过魔数校验
}


async def validate_upload_file(file: UploadFile) -> dict:
    """四重校验上传文件

    Args:
        file: 上传的文件

    Returns:
        校验结果信息

    Raises:
        HTTPException: 校验失败时抛出 400
    """
    errors = []

    # 1. 检查文件扩展名
    ext_error = _check_extension(file.filename)
    if ext_error:
        errors.append(ext_error)

    # 2. 检查 MIME 类型
    mime_error = _check_mime_type(file.content_type)
    if mime_error:
        errors.append(mime_error)

    # 3. 检查魔数（文件签名）
    magic_error = await _check_magic_number(file)
    if magic_error:
        errors.append(magic_error)

    # 4. 检查文件大小
    size_error = await _check_file_size(file)
    if size_error:
        errors.append(size_error)

    if errors:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "文件校验失败",
                "errors": errors,
            },
        )

    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "valid": True,
    }


def _check_extension(filename: str | None) -> str | None:
    """检查文件扩展名

    Args:
        filename: 文件名

    Returns:
        错误信息或 None
    """
    if not filename:
        return "文件名为空"

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in settings.ALLOWED_EXTENSIONS:
        return f"不支持的文件扩展名: .{ext}，允许: {settings.ALLOWED_EXTENSIONS}"

    return None


def _check_mime_type(content_type: str | None) -> str | None:
    """检查 MIME 类型

    Args:
        content_type: MIME 类型

    Returns:
        错误信息或 None
    """
    allowed_mimes = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "text/plain",
    ]

    if not content_type or content_type not in allowed_mimes:
        return f"不支持的 MIME 类型: {content_type}"

    return None


async def _check_magic_number(file: UploadFile) -> str | None:
    """检查文件魔数

    Args:
        file: 上传的文件

    Returns:
        错误信息或 None
    """
    # .txt 文件无固定魔数，跳过校验
    if file.filename and file.filename.lower().endswith(".txt"):
        return None

    # 读取前 8 字节
    header = await file.read(8)
    await file.seek(0)  # 重置文件指针

    # Validate against the claimed extension, not a signature of any allowed format.
    extension = (file.filename or "").rsplit(".", 1)[-1].lower()
    if any(header.startswith(signature) for signature in FILE_SIGNATURES.get(extension, [])):
        return None

    return "文件内容与扩展名不匹配（魔数校验失败）"


async def _check_file_size(file: UploadFile) -> str | None:
    """检查文件大小

    Args:
        file: 上传的文件

    Returns:
        错误信息或 None
    """
    # 读取文件内容检查大小
    content = await file.read()
    await file.seek(0)  # 重置文件指针

    if len(content) > settings.max_upload_size_bytes:
        size_mb = len(content) / (1024 * 1024)
        return f"文件大小 {size_mb:.1f}MB 超过限制 {settings.MAX_UPLOAD_SIZE_MB}MB"

    if len(content) == 0:
        return "文件为空"

    return None
