from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def make_pdf(path: Path, lines=("First page Python", "Last page project")):
    writer = PdfWriter()
    for line in lines:
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 250 Td ({line}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)


def test_all_pages_preserved(tmp_path):
    from boss_say_hello.resume.pdf import extract_pdf

    path = tmp_path / "test.pdf"
    make_pdf(path)
    digest, text = extract_pdf(path)
    assert len(digest) == 64 and "First page" in text and "Last page" in text


@pytest.mark.parametrize("kind", ["non_pdf", "empty", "encrypted", "scanned"])
def test_non_pdf_empty_encrypted_rejected(tmp_path, kind):
    from boss_say_hello.resume.pdf import extract_pdf

    path = tmp_path / ("test.txt" if kind == "non_pdf" else "test.pdf")
    if kind in ("non_pdf", "empty"):
        path.write_bytes(b"")
    else:
        writer = PdfWriter()
        writer.add_blank_page(100, 100)
        if kind == "encrypted":
            writer.encrypt("password")
        writer.write(path)
    with pytest.raises(ValueError):
        extract_pdf(path)


def test_edit_requires_confirmation(resume, storage):
    from boss_say_hello.resume.service import ResumeService

    service = ResumeService(storage, None)
    old = resume()
    storage.save_resume(old)
    new = service.revise(old, ("Python", "SQL"), "Confirmed project")
    assert new.id != old.id and not new.confirmed and old.confirmed
    confirmed = service.confirm(new)
    assert confirmed.confirmed
    assert storage.get_resume(old.id) == old


async def test_import_pdf_creates_unconfirmed_draft(tmp_path, storage):
    from boss_say_hello.resume.service import ResumeService

    path = tmp_path / "test.pdf"
    make_pdf(path)

    class AI:
        async def extract_resume(self, text):
            assert "Last page" in text
            return ("Python",), "Python project"

    result = await ResumeService(storage, AI(), tmp_path / "managed").import_pdf(path)
    assert not result.confirmed
    assert (tmp_path / "managed" / f"{result.pdf_hash}.pdf").exists()
