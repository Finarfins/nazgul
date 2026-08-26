from __future__ import annotations

from io import BytesIO

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from app.supplier_price_pdf import extract_pdf_rows


def _pdf_bytes() -> bytes:
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4)
    main_table = Table(
        [
            ["Kod", "Açıklama", "Peşin", "Vadeli"],
            ["A-1", "Normal", "10,00 TL", "11,00 TL"],
        ]
    )
    short_table = Table([["A-2", "Eksik kolon", "12,00 TL"]])
    for table in (main_table, short_table):
        table.setStyle(
            TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)])
        )
    main_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )
    document.build([main_table, Spacer(1, 12), short_table])
    return output.getvalue()


def test_extract_pdf_rows_maps_columns_and_marks_non_dominant_shape() -> None:
    extraction = extract_pdf_rows(
            _pdf_bytes(),
            [
                {
                    "page_start": 1,
                    "page_end": 1,
                    "header_signature": None,
                    "column_map": {
                        "code": 0,
                        "description": 1,
                        "price_cash": 2,
                        "price_term": 3,
                    },
                }
            ],
        )
    rows = extraction.rows
    assert rows[1].values["code"] == "A-1"
    assert rows[1].extraction_suspect is False
    assert rows[2].values["code"] == "A-2"
    assert rows[2].extraction_suspect is True
    assert extraction.report == {
        "total_page_count": 1,
        "matched_page_count": 1,
        "processed_page_count": 1,
        "tableless_pages": [],
        "uncovered_page_count": 0,
        "uncovered_pages": [],
        "warnings": [],
    }


def test_page_with_no_sixty_percent_dominant_shape_marks_every_row_suspect(
    monkeypatch,
) -> None:
    class Page:
        def extract_text(self):
            return "Fiyat listesi"

        def extract_tables(self):
            return [
                [["A", "B", "C", "D"]],
                [["A", "B", "C"]],
                [["A", "B"]],
            ]

    class Document:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("app.supplier_price_pdf.pdfplumber.open", lambda *_: Document())
    extraction = extract_pdf_rows(
        b"pdf",
        [
            {
                "page_start": 1,
                "page_end": 1,
                "column_map": {
                    "code": 0,
                    "description": 1,
                    "price_cash": 2,
                    "price_term": 3,
                },
            }
        ],
    )
    assert all(row.extraction_suspect for row in extraction.rows)


@pytest.mark.parametrize(
    "sections",
    [
        [
            {"page_start": 1, "page_end": 1, "column_map": {}},
            {"header_signature": "Fiyat", "column_map": {}},
        ],
        [
            {"header_signature": "Fiyat", "column_map": {}},
            {"header_signature": "listesi", "column_map": {}},
        ],
    ],
)
def test_runtime_section_overlap_fails_closed(monkeypatch, sections) -> None:
    class Page:
        def extract_text(self):
            return "Fiyat listesi"

        def extract_tables(self):
            return []

    class Document:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("app.supplier_price_pdf.pdfplumber.open", lambda *_: Document())

    with pytest.raises(
        ValueError,
        match=(
            "sayfa 1: 'bölüm 1' ve 'bölüm 2' "
            "çakışıyor — profili netleştirin"
        ),
    ):
        extract_pdf_rows(b"pdf", sections)
