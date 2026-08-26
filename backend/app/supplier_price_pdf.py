from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pdfplumber

MAX_PDF_PAGES = 500
MAX_PDF_ROWS = 50_000


@dataclass(frozen=True)
class ExtractedPdfRow:
    page_number: int
    row_number: int
    values: dict[str, Any]
    extraction_suspect: bool


@dataclass(frozen=True)
class PdfExtraction:
    rows: list[ExtractedPdfRow]
    report: dict[str, Any]


class PdfSectionOverlapError(ValueError):
    pass


def _matches_section(section: dict[str, Any], page_number: int, text: str) -> bool:
    in_range = (
        section.get("page_start") is not None
        and section.get("page_end") is not None
        and int(section["page_start"]) <= page_number <= int(section["page_end"])
    )
    signature = str(section.get("header_signature") or "").strip()
    return in_range or (bool(signature) and signature.casefold() in text.casefold())


def _section_for_page(
    sections: list[dict[str, Any]], page_number: int, text: str
) -> dict[str, Any] | None:
    matches = [
        (index, section)
        for index, section in enumerate(sections, start=1)
        if _matches_section(section, page_number, text)
    ]
    if len(matches) > 1:
        first, second = matches[0][0], matches[1][0]
        raise PdfSectionOverlapError(
            f"sayfa {page_number}: 'bölüm {first}' ve 'bölüm {second}' "
            "çakışıyor — profili netleştirin"
        )
    return matches[0][1] if matches else None


def extract_pdf_rows(
    content: bytes, sections: list[dict[str, Any]]
) -> PdfExtraction:
    with pdfplumber.open(io.BytesIO(content)) as document:
        if len(document.pages) > MAX_PDF_PAGES:
            raise ValueError("PDF en fazla 500 sayfa içerebilir")
        extracted_rows: list[ExtractedPdfRow] = []
        extracted_count = 0
        matched_pages: list[int] = []
        processed_pages: list[int] = []
        tableless_pages: list[int] = []
        uncovered_pages: list[int] = []
        for page_number, page in enumerate(document.pages, start=1):
            section = _section_for_page(
                sections, page_number, page.extract_text() or ""
            )
            if section is None:
                uncovered_pages.append(page_number)
                continue
            matched_pages.append(page_number)
            rows = [
                row
                for table in page.extract_tables()
                for row in table
                if row
            ]
            if not rows:
                tableless_pages.append(page_number)
                continue
            processed_pages.append(page_number)
            dominant_columns, dominant_count = Counter(
                len(row) for row in rows
            ).most_common(1)[0]
            page_is_suspect = dominant_count / len(rows) < 0.60
            column_map = {
                key: int(index) for key, index in section["column_map"].items()
            }
            for row_number, row in enumerate(rows, start=1):
                extracted_count += 1
                if extracted_count > MAX_PDF_ROWS:
                    raise ValueError("PDF en fazla 50.000 satır içerebilir")
                values = {
                    key: row[index] if 0 <= index < len(row) else None
                    for key, index in column_map.items()
                }
                extracted_rows.append(
                    ExtractedPdfRow(
                        page_number=page_number,
                        row_number=row_number,
                        values=values,
                        extraction_suspect=(
                            page_is_suspect or len(row) != dominant_columns
                        ),
                    )
                )
        return PdfExtraction(
            rows=extracted_rows,
            report={
                "total_page_count": len(document.pages),
                "matched_page_count": len(matched_pages),
                "processed_page_count": len(processed_pages),
                "tableless_pages": tableless_pages,
                "uncovered_page_count": len(uncovered_pages),
                "uncovered_pages": uncovered_pages,
                "warnings": [
                    f"sayfa {page}: tablo çıkarılamadı"
                    for page in tableless_pages
                ],
            },
        )
