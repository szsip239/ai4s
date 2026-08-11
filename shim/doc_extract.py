"""EDM 文档文本提取（issue #48，自研）：bytes-in / text-out，服务端不落原始文件盘。

上传的二进制文档（PDF/DOCX/XLSX/PPTX）提取为纯文本后交 admin_api 走 edm_lib 指纹管线；
文本类（.txt/.md 等）按多编码链解码（GBK 中文文档可正确解码，护栏不回退）。
提取结果不含任何合成标记行（如 "--- Page N ---"）——指纹按真实内容计算，
避免标记行在跨文档间产生相同的行级指纹（误命中）或让空文档混过入库最小长度门槛。
扫描版 PDF（无内嵌文本）提取为空 → EmptyDocumentError，由 admin_api 转「需 OCR」提示。
"""
import io
from pathlib import PurePosixPath

import fitz  # PyMuPDF（AGPL/商业双许可，私有部署内部使用；issue #48 备注）
from docx import Document as _DocxDocument
from openpyxl import load_workbook as _load_workbook
from pptx import Presentation as _PptxPresentation

OFFICE_EXTENSIONS = frozenset({".pdf", ".docx", ".xlsx", ".pptx"})
TEXT_EXTENSIONS = frozenset({".txt", ".text", ".log", ".md", ".markdown"})
SUPPORTED_EXTENSIONS = OFFICE_EXTENSIONS | TEXT_EXTENSIONS
_LEGACY_DOC_EXTENSIONS = frozenset({".doc"})
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"})

# utf-8-sig 在前剥掉 BOM；GBK 系兜底中文 Windows 文档；latin-1 全字节可解（最终兜底，不抛错）
_TEXT_DECODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")

_PDF_MAGIC = b"%PDF-"
_OOXML_MAGIC = b"PK\x03\x04"  # docx/xlsx/pptx 均为 zip 包


class DocumentExtractionError(Exception):
    """提取失败基类：str(exc) 为用户可见中文文案。"""


class UnsupportedDocumentError(DocumentExtractionError):
    """扩展名不支持（含 .doc 老式格式与图片的定制提示）。"""


class CorruptDocumentError(DocumentExtractionError):
    """内容与扩展名不符、加密或解析失败。"""


class EmptyDocumentError(DocumentExtractionError):
    """空文件或未提取到文本（扫描 PDF 走此路）。"""


def extract_text_from_bytes(filename: str, data: bytes) -> str:
    """从单个文档的原始字节提取纯文本；失败抛 DocumentExtractionError 子类。"""
    if not data:
        raise EmptyDocumentError(f"「{filename}」是空文件")
    ext = PurePosixPath(filename or "").suffix.lower()
    if ext in _LEGACY_DOC_EXTENSIONS:
        raise UnsupportedDocumentError(
            "老式 .doc（二进制格式）暂不支持：请在 Word/WPS 中另存为 .docx 后上传"
        )
    if ext in _IMAGE_EXTENSIONS:
        raise UnsupportedDocumentError("图片需 OCR 提取文字，暂不支持：请导出为文本或文字版 PDF 后上传")
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedDocumentError(
            f"不支持的文件类型 '{ext or filename}'：支持 .pdf/.docx/.xlsx/.pptx 与 .txt/.md/.text/.log"
        )

    if ext == ".pdf":
        text = _extract_pdf(data, filename)
    elif ext == ".docx":
        text = _extract_docx(data, filename)
    elif ext == ".xlsx":
        text = _extract_xlsx(data, filename)
    elif ext == ".pptx":
        text = _extract_pptx(data, filename)
    else:  # 文本类
        text = _decode_text(data)

    if not text.strip():
        raise EmptyDocumentError(f"「{filename}」未提取到文本")
    return text


def _check_magic(data: bytes, magic: bytes, filename: str, kind: str) -> None:
    """文件头魔数校验：拦扩展名伪造（把 zip 改名为 .pdf 之类）。"""
    if not data.startswith(magic):
        raise CorruptDocumentError(f"「{filename}」内容不是有效 {kind}（文件头与扩展名不符）")


def _extract_pdf(data: bytes, filename: str) -> str:
    _check_magic(data, _PDF_MAGIC, filename, "PDF")
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.is_encrypted and not doc.authenticate(""):
                raise CorruptDocumentError(f"「{filename}」已加密，无法读取")
            return "\n\n".join(page.get_text() for page in doc)
    except CorruptDocumentError:
        raise
    except Exception as e:
        raise CorruptDocumentError(f"「{filename}」PDF 解析失败（{e}）") from e


def _extract_docx(data: bytes, filename: str) -> str:
    _check_magic(data, _OOXML_MAGIC, filename, "Office 文件")
    try:
        doc = _DocxDocument(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        raise CorruptDocumentError(f"「{filename}」DOCX 解析失败（{e}）") from e


def _extract_xlsx(data: bytes, filename: str) -> str:
    _check_magic(data, _OOXML_MAGIC, filename, "Office 文件")
    try:
        wb = _load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            lines = []
            for sheet_name in wb.sheetnames:
                for row in wb[sheet_name].iter_rows(values_only=True):
                    line = "\t".join(str(cell) if cell is not None else "" for cell in row)
                    if line.strip():
                        lines.append(line)
            return "\n".join(lines)
        finally:
            wb.close()
    except Exception as e:
        raise CorruptDocumentError(f"「{filename}」XLSX 解析失败（{e}）") from e


def _extract_pptx(data: bytes, filename: str) -> str:
    _check_magic(data, _OOXML_MAGIC, filename, "Office 文件")
    try:
        prs = _PptxPresentation(io.BytesIO(data))
        lines = []
        for slide in prs.slides:
            for shape in slide.shapes:
                text = getattr(shape, "text", "")
                if text.strip():
                    lines.append(text)
        return "\n".join(lines)
    except Exception as e:
        raise CorruptDocumentError(f"「{filename}」PPTX 解析失败（{e}）") from e


def _decode_text(data: bytes) -> str:
    """文本类多编码链解码（GBK 护栏）：逐个尝试，latin-1 兜底保证不抛 UnicodeDecodeError。"""
    for encoding in _TEXT_DECODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")  # pragma: no cover - latin-1 不失败
