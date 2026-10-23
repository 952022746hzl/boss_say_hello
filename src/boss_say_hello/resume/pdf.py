import hashlib
from io import BytesIO
from pathlib import Path
from pypdf import PdfReader


def extract_pdf(path: Path) -> tuple[str, str]:
    if path.suffix.lower() != '.pdf':
        raise ValueError('仅支持 PDF 简历')
    try:
        raw = path.read_bytes()
        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError('请先解密 PDF 再导入')
        text = '\n\n'.join(page.extract_text() or '' for page in reader.pages).strip()
    except ValueError:
        raise
    except Exception:
        raise ValueError('无法读取 PDF，请检查文件是否损坏') from None
    if not text:
        raise ValueError('PDF 没有可提取文本，首版不支持扫描件 OCR')
    return hashlib.sha256(raw).hexdigest(), text
