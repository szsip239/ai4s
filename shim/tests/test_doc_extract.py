#!/usr/bin/env python3
"""shim/doc_extract.py（issue #48，自研提取器）单测。

覆盖：四种 Office 格式提取（含中文）、文本类多编码解码（GBK/BOM）、
拒绝路径（.doc/图片/未知扩展名/空文件/魔数不符/损坏 zip/扫描 PDF）。
fixture 全部现场生成（python-docx/openpyxl/python-pptx/PyMuPDF），不落盘；
造档函数放模块级，admin API 端到端用例（test_admin_api.py）复用。
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import doc_extract as dx  # noqa: E402


# ---------------------------------------------------------------------------
# fixture：现场生成各格式文档字节
# ---------------------------------------------------------------------------


def make_docx_bytes(paragraphs) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_xlsx_bytes(sheets) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        for row in rows:
            ws.append(row)
        first = False
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pptx_bytes(slides_text) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    for slide_texts in slides_text:
        slide = prs.slides.add_slide(prs.slide_layouts[5])  # 近空白版式
        for i, text in enumerate(slide_texts):
            tb = slide.shapes.add_textbox(Inches(1), Inches(1 + i * 0.6), Inches(6), Inches(0.5))
            tb.text_frame.text = text
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def make_pdf_bytes(pages_text, fontname="helv") -> bytes:
    """一页一段文字；fontname='china-s' 用 PyMuPDF 内置 CJK 字体写中文。"""
    import fitz

    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontname=fontname)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 各格式提取
# ---------------------------------------------------------------------------


class TestExtractDocx(unittest.TestCase):
    def test_basic(self):
        text = dx.extract_text_from_bytes(
            "doc.docx", make_docx_bytes(["Hello world", "Second paragraph", "  "])
        )
        self.assertIn("Hello world", text)
        self.assertIn("Second paragraph", text)

    def test_chinese(self):
        text = dx.extract_text_from_bytes("合同.docx", make_docx_bytes(["机密合同条款 第一条"]))
        self.assertIn("机密合同条款 第一条", text)


class TestExtractXlsx(unittest.TestCase):
    def test_multiple_sheets(self):
        data = make_xlsx_bytes({"Alpha": [["a1", "b1"], ["a2", 42]], "Beta": [["x", "y"]]})
        text = dx.extract_text_from_bytes("book.xlsx", data)
        self.assertIn("a1", text)
        self.assertIn("42", text)
        self.assertIn("x", text)

    def test_chinese(self):
        text = dx.extract_text_from_bytes("报表.xlsx", make_xlsx_bytes({"数据": [["项目", "金额"], ["阿尔法", 42]]}))
        self.assertIn("项目", text)
        self.assertIn("阿尔法", text)


class TestExtractPptx(unittest.TestCase):
    def test_basic(self):
        data = make_pptx_bytes([["Slide 1 title", "Slide 1 body"], ["Slide 2 only text"]])
        text = dx.extract_text_from_bytes("deck.pptx", data)
        self.assertIn("Slide 1 title", text)
        self.assertIn("Slide 2 only text", text)

    def test_chinese(self):
        text = dx.extract_text_from_bytes("路演.pptx", make_pptx_bytes([["路演机密数据 2026"]]))
        self.assertIn("路演机密数据 2026", text)


class TestExtractPdf(unittest.TestCase):
    def test_basic(self):
        text = dx.extract_text_from_bytes("a.pdf", make_pdf_bytes(["Hello PDF world"]))
        self.assertIn("Hello PDF world", text)

    def test_chinese(self):
        text = dx.extract_text_from_bytes("z.pdf", make_pdf_bytes(["机密PDF内容"], fontname="china-s"))
        self.assertIn("机密PDF内容", text)

    def test_multipage(self):
        text = dx.extract_text_from_bytes("m.pdf", make_pdf_bytes(["page one text", "page two text"]))
        self.assertIn("page one text", text)
        self.assertIn("page two text", text)

    def test_no_synthetic_page_markers(self):
        """提取结果不含 "--- Page N ---" 类合成标记（指纹按真实内容计算，防跨文档误命中）。"""
        text = dx.extract_text_from_bytes("m.pdf", make_pdf_bytes(["page one text", "page two text"]))
        self.assertNotIn("---", text)


class TestExtractText(unittest.TestCase):
    def test_utf8(self):
        text = dx.extract_text_from_bytes("note.txt", "hello 你好\nline two".encode("utf-8"))
        self.assertIn("hello 你好", text)
        self.assertIn("line two", text)

    def test_gbk(self):
        """GBK 护栏：中文 Windows 文档正确解码（GBK 字节现场编码，源码不贴不可见字符）。"""
        text = dx.extract_text_from_bytes("note.txt", "你好世界".encode("gbk"))
        self.assertEqual(text, "你好世界")

    def test_utf8_bom_stripped(self):
        text = dx.extract_text_from_bytes("note.md", b"\xef\xbb\xbfhello")
        self.assertEqual(text, "hello")

    def test_markdown_ext(self):
        self.assertIn("# T", dx.extract_text_from_bytes("d.markdown", b"# T\n\nbody\n"))


# ---------------------------------------------------------------------------
# 拒绝路径
# ---------------------------------------------------------------------------


class TestRejections(unittest.TestCase):
    def test_empty_bytes(self):
        with self.assertRaises(dx.EmptyDocumentError):
            dx.extract_text_from_bytes("f.docx", b"")

    def test_legacy_doc(self):
        with self.assertRaises(dx.UnsupportedDocumentError) as cm:
            dx.extract_text_from_bytes("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 ole")
        self.assertIn(".docx", str(cm.exception))

    def test_image(self):
        with self.assertRaises(dx.UnsupportedDocumentError) as cm:
            dx.extract_text_from_bytes("pic.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        self.assertIn("OCR", str(cm.exception))

    def test_unknown_extension(self):
        with self.assertRaises(dx.UnsupportedDocumentError):
            dx.extract_text_from_bytes("a.zip", b"PK\x03\x04" + b"\x00" * 32)

    def test_pdf_magic_mismatch(self):
        with self.assertRaises(dx.CorruptDocumentError):
            dx.extract_text_from_bytes("f.pdf", b"this is not a pdf")

    def test_ooxml_magic_mismatch(self):
        with self.assertRaises(dx.CorruptDocumentError):
            dx.extract_text_from_bytes("f.docx", b"not an office file")

    def test_corrupt_docx_zip(self):
        """魔数对头（PK）但 zip 体损坏 → Corrupt。"""
        with self.assertRaises(dx.CorruptDocumentError):
            dx.extract_text_from_bytes("f.docx", b"PK\x03\x04" + b"\x00" * 512)

    def test_scanned_pdf_empty(self):
        """扫描版 PDF（无内嵌文本）→ ScannedPdfError（EmptyDocumentError 子类，issue #49 P2-5）。"""
        with self.assertRaises(dx.ScannedPdfError):
            dx.extract_text_from_bytes("scan.pdf", make_pdf_bytes([""]))
        # 兼容语义：仍是 EmptyDocumentError 子类
        with self.assertRaises(dx.EmptyDocumentError):
            dx.extract_text_from_bytes("scan.pdf", make_pdf_bytes([""]))


class TestExtractedTextCap(unittest.TestCase):
    """提取文本 8M 字符上限（issue #49 P1-1）：zip/流扩张防 OOM，超限明确中文报错。"""

    def test_over_cap_rejected(self):
        data = ("x" * (dx.MAX_EXTRACTED_CHARS + 1)).encode("utf-8")  # ~8MB，线路 16MB 上限内
        with self.assertRaises(dx.ExtractedTextTooLargeError) as cm:
            dx.extract_text_from_bytes("big.txt", data)
        self.assertIn("上限", str(cm.exception))

    def test_just_under_cap_passes(self):
        line = "商密文档边界行 abcdefghijklmnopqrstuvwxyz0123456789"  # >12 字符，避免误判场景干扰
        data = (line * 100).encode("utf-8")  # 远小于上限的正常文档
        text = dx.extract_text_from_bytes("ok.txt", data)
        self.assertIn("商密文档边界行", text)


if __name__ == "__main__":
    unittest.main()
