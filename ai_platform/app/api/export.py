"""Spreadsheet export of rows shown in the chat results panel.

Formats the rows it is sent and nothing else. No database or file is read, so
the endpoint exposes no data the caller does not already hold.
"""

from __future__ import annotations

from io import BytesIO
from typing import Annotated

from fastapi import APIRouter
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_platform.backend.clock import working_date

MAX_COLUMNS = 60
MAX_ROWS = 20_000
MAX_CELL_CHARS = 2_000
EXCEL_EXACT_DIGITS = 15
"""Longest integer Excel stores without rounding; longer ones are written as text."""

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

CellValue = Annotated[str, Field(max_length=MAX_CELL_CHARS)] | int | float | bool | None

router = APIRouter(prefix="/api/export", tags=["export"])


class SheetRequest(BaseModel):
    """Rows to write, aligned to their column names.

    Attributes
    ----------
    columns : list of str
        Header names in display order.
    rows : list of list
        One list per row, each the same length as ``columns``.
    noun : str
        What a row is called; becomes the sheet title and the file name stem.
    """

    model_config = ConfigDict(extra="forbid")

    columns: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        min_length=1, max_length=MAX_COLUMNS
    )
    rows: list[list[CellValue]] = Field(max_length=MAX_ROWS)
    noun: str = Field(pattern=r"^[a-z]{1,20}$")

    @model_validator(mode="after")
    def check_row_widths(self) -> SheetRequest:
        """Reject any row whose length differs from the header.

        Returns
        -------
        SheetRequest
            The validated request.

        Raises
        ------
        ValueError
            If a row is wider or narrower than ``columns``.
        """
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            raise ValueError("every row must have one value per column")
        return self


def to_cell_value(value: str | float | bool | None) -> str | float | bool | None:
    """Convert a value so Excel stores it without rounding.

    Parameters
    ----------
    value : str, float, bool or None
        Value from the request. Integers arrive as ``int``.

    Returns
    -------
    str, float, bool or None
        The value unchanged, or its decimal text for an integer too long for Excel.
    """
    is_long_integer = (
        isinstance(value, int)
        and not isinstance(value, bool)
        and len(str(abs(value))) > EXCEL_EXACT_DIGITS
    )
    return str(value) if is_long_integer else value


def build_workbook(sheet: SheetRequest) -> bytes:
    """Render the request as an ``.xlsx`` file.

    Parameters
    ----------
    sheet : SheetRequest
        Validated columns and rows.

    Returns
    -------
    bytes
        The workbook, with a bold header row frozen in place.
    """
    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet(title=sheet.noun)
    worksheet.freeze_panes = "A2"

    header_font = Font(bold=True)
    header_cells = []
    for column in sheet.columns:
        cell = WriteOnlyCell(worksheet, value=column)
        cell.font = header_font
        header_cells.append(cell)
    worksheet.append(header_cells)

    for row in sheet.rows:
        worksheet.append([to_cell_value(value) for value in row])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@router.post("/xlsx")
def export_xlsx(sheet: SheetRequest) -> Response:
    """Return the posted rows as a spreadsheet download.

    Parameters
    ----------
    sheet : SheetRequest
        Columns and rows to write.

    Returns
    -------
    Response
        The ``.xlsx`` file, named after the rows and the working date.
    """
    filename = f"{sheet.noun}_{working_date():%Y-%m-%d}.xlsx"
    return Response(
        content=build_workbook(sheet),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
