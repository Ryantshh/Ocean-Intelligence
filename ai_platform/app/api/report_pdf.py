"""Portable PDF from a bounded, client-supplied report snapshot; no database writes."""
from datetime import date, datetime
from io import BytesIO
from xml.sax.saxutils import escape

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

router = APIRouter(prefix="/api/manager-report", tags=["manager report"])


class Region(BaseModel):
    region: str = Field(max_length=250)
    orders: int = Field(ge=0)
    previous_orders: int = Field(ge=0)


class Fleet(BaseModel):
    status: str = Field(max_length=50)
    count: int = Field(ge=0)


class Order(BaseModel):
    order_id: str = Field(max_length=80)
    order_seq: int
    cargo_type: str | None = Field(None, max_length=500)
    load_zone: str | None = Field(None, max_length=500)
    laycan_start: str | None = Field(None, max_length=50)
    laycan_end: str | None = Field(None, max_length=50)
    cargo_weight_min: float | None = None
    cargo_weight_max: float | None = None


class Snapshot(BaseModel):
    start: date
    end: date
    previous_start: date
    previous_end: date
    region: str = Field(max_length=100)
    generated_at: datetime
    report_id: str = Field(max_length=64)
    reference: dict[str, str] = Field(max_length=2)
    metrics: dict[str, float | None] = Field(max_length=30)
    regions: list[Region] = Field(max_length=12)
    fleet: list[Fleet] = Field(max_length=10)
    orders: list[Order] = Field(max_length=20)


class ExportRequest(BaseModel):
    report: Snapshot
    notes: str = Field("", max_length=4000)


def render_pdf(payload: ExportRequest) -> bytes:
    r = payload.report
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles['Normal'].fontSize = 9
    styles['Normal'].leading = 13
    styles['Normal'].textColor = colors.HexColor('#273342')
    styles['Heading1'].textColor = colors.HexColor('#24523f')
    styles['Heading2'].textColor = colors.HexColor('#356d97')
    styles['Heading2'].fontSize = 13
    styles['Heading2'].spaceBefore = 16
    styles['Heading2'].spaceAfter = 8
    story = []

    def p(text, style='Normal'):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style])

    def section(title):
        story.append(p(title, 'Heading2'))

    def number(key):
        value = r.metrics.get(key)
        return f'{value:,.0f}' if value is not None else 'Not available'

    def table(rows, widths):
        t = Table([[p(cell) for cell in row] for row in rows], colWidths=widths, repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e7eef4')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f6f7f8')]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('LINEBELOW', (0, 0), (-1, 0), .5, colors.HexColor('#9cabb8')),
        ]))
        story.append(t)

    story.extend([p('CARGILL OCEAN TRANSPORTATION'), p('Commercial management brief', 'Heading1'),
                  p(f'{r.start} to {r.end} | {r.region or "All regions"}'),
                  p(f'Comparison: {r.previous_start} to {r.previous_end}'),
                  p(f'Snapshot generated: {r.generated_at.isoformat()}'), Spacer(1, 14)])
    section('Executive summary')
    orders = r.metrics.get('orders') or 0
    previous = r.metrics.get('previous_orders') or 0
    change = f'{(orders-previous)/previous*100:+.1f}%' if previous else 'no percentage baseline'
    open_vessels = sum(row.count for row in r.fleet if row.status == 'OPEN')
    story.append(p(f'{orders:,.0f} orders received versus {previous:,.0f} in the previous period ({change}). '
                   f'{open_vessels:,} vessels are currently open in scope; this is a current snapshot, not historical supply.'))
    story.append(p(f'Valid, complete cargo-weight ranges: {number("complete_weight")} orders. '
                   f'Combined range: {number("weight_min")} to {number("weight_max")} metric tonnes. '
                   'This represents potential demand, not booked cargo.'))
    section('Management review actions')
    actions = [
        ('invalid_ranges', 'Validate inconsistent date or weight ranges.'),
        ('due_soon', 'Confirm execution for laycans ending within seven simulated days.'),
        ('elapsed_laycans', 'Confirm fulfilment, cancellation or revised dates for elapsed laycans.'),
        ('missing_weight', 'Complete missing cargo-weight endpoints.'),
        ('missing_laycan', 'Confirm missing laycan endpoints.'),
        ('missing_cargo', 'Confirm cargo requirements.'),
        ('repeated_ids', 'Ask traders to interpret repeated IDs; occurrences may be legitimate shipments.'),
    ]
    active = [(key, action) for key, action in actions if r.metrics.get(key)]
    for key, action in active:
        unit = 'IDs' if key == 'repeated_ids' else 'order(s)'
        story.append(p(f'{number(key)} {unit}: {action}'))
    if not active:
        story.append(p('No issues found by the checks in this scope. This is not a complete risk assessment.'))
    story.append(p('Action counts can overlap. Deadline checks use the current order simulation date. Elapsed windows do not prove overdue shipments.'))
    if payload.notes.strip():
        section('Manager notes')
        story.append(p(payload.notes))
    section('Regional demand comparison')
    table([['Load-region label', 'Selected period', 'Previous period']] +
          [[row.region, f'{row.orders:,}', f'{row.previous_orders:,}'] for row in r.regions], [285, 105, 105])
    story.append(p('Top 12 labels. Combined region labels stay grouped; repeated order occurrences count separately.'))
    section('Current fleet availability')
    table([['Status', 'Vessels']] + [[row.status, str(row.count)] for row in r.fleet], [330, 165])
    def weight_range(order):
        lower = f'{order.cargo_weight_min:,.0f}' if order.cargo_weight_min is not None else 'Not supplied'
        upper = f'{order.cargo_weight_max:,.0f}' if order.cargo_weight_max is not None else 'Not supplied'
        return f'{lower}\nto {upper}'

    section('Supporting order sample')
    story.append(p(f'First {len(r.orders)} of {orders:,.0f} received orders, ordered by laycan start. '
                   'Browse all evidence in the application. This export is a snapshot of the report summary and original sample.'))
    table([['Order / occurrence', 'Cargo / load region', 'Laycan', 'Weight (mt)']] + [
        [f'{o.order_id} / {o.order_seq}', f'{o.cargo_type or "Not supplied"}\n{o.load_zone or "Not supplied"}',
         f'{(o.laycan_start or "Not supplied")[:10]}\nto {(o.laycan_end or "Not supplied")[:10]}',
         weight_range(o)]
        for o in r.orders], [125, 170, 95, 105])
    section('Source and interpretation')
    story.append(p('Source: gold order records and current fleet status view. Metrics are database calculations. '
                   'Historical counts reflect currently stored records and may change after edits. '
                   'Profit ranking, confirmed vessel matches and historical fleet reconstruction are not included. '
                   'Manager notes are user-authored and are not verified calculations.'))
    for key in ('orders_now', 'tonnage_now'):
        story.append(p(f'{key}: {r.reference.get(key, "Not supplied")[:80]}'))
    story.append(p(f'Report ID: {r.report_id}'))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#526274'))
        canvas.drawString(50, 26, f'Ocean Intelligence | {r.start} to {r.end}')
        canvas.drawRightString(A4[0]-50, 26, f'Page {doc.page}')
        canvas.restoreState()

    SimpleDocTemplate(buffer, pagesize=A4, rightMargin=50, leftMargin=50,
                      topMargin=38, bottomMargin=45, title='Ocean Intelligence Manager Report').build(
                          story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


@router.post('/pdf')
def download_pdf(payload: ExportRequest):
    data = render_pdf(payload)
    filename = f'ocean-manager-report-{payload.report.start}-{payload.report.end}.pdf'
    return Response(data, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{filename}"'})
