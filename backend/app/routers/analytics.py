from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..business_time import business_now, business_today
from ..db import get_db
from ..money import HUNDRED, ZERO_MONEY, money
from ..document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
from ..tenancy import company_id

router = APIRouter(prefix='/analytics', tags=['analytics'])


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip().split(' ')[0]
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


@router.get('/insights')
def insights(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    today = business_today()
    current_start = today - timedelta(days=29)
    previous_start = today - timedelta(days=59)
    previous_end = today - timedelta(days=30)

    order_status_sql = accounting_document_status_sql(status_column='status', note_column='note')
    order_rows = db.execute(
        text(f"""
      SELECT id, customer_id, order_date, final_total, due_date, paid_amount, status, note
      FROM orders
      WHERE company_id=:cid
        AND {order_status_sql}
    """),
        {"cid": cid, "sales_import_note": SALES_IMPORT_NOTE},
    ).mappings().all()

    current_sales = ZERO_MONEY
    previous_sales = ZERO_MONEY
    latest_by_customer: dict[int, date] = {}
    overdue_total = ZERO_MONEY
    overdue_count = 0
    for row in order_rows:
        d = parse_date(row['order_date'])
        if d:
            if current_start <= d <= today:
                current_sales += money(row['final_total'])
            elif previous_start <= d <= previous_end:
                previous_sales += money(row['final_total'])
            current = latest_by_customer.get(int(row['customer_id']))
            if current is None or d > current:
                latest_by_customer[int(row['customer_id'])] = d
        due = parse_date(row['due_date'])
        remaining = money(row['final_total']) - money(row['paid_amount'])
        if due and due < today and remaining > ZERO_MONEY:
            overdue_count += 1
            overdue_total += remaining

    customers = db.execute(text("SELECT id,name FROM customers WHERE company_id=:cid"), {'cid': cid}).mappings().all()
    inactive_cutoff = today - timedelta(days=60)
    inactive = [
        {'id': row['id'], 'name': row['name'], 'last_sale_date': latest_by_customer.get(int(row['id'])).isoformat() if latest_by_customer.get(int(row['id'])) else None}
        for row in customers
        if latest_by_customer.get(int(row['id'])) is None or latest_by_customer[int(row['id'])] < inactive_cutoff
    ]
    inactive.sort(key=lambda x: (x['last_sale_date'] is not None, x['last_sale_date'] or '', x['name']))

    critical = db.execute(text("""
      SELECT id,name,stock,unit,COALESCE(NULLIF(critical_stock,0),minimum_stock,0) critical_level
      FROM products
      WHERE company_id=:cid AND COALESCE(active, TRUE)=TRUE
        AND COALESCE(stock,0) <= COALESCE(NULLIF(critical_stock,0),minimum_stock,0)
      ORDER BY stock ASC,name LIMIT 10
    """), {'cid': cid}).mappings().all()

    profitable = db.execute(
      text(f"""
      SELECT p.id,p.name,
             SUM(oi.quantity) quantity,
             SUM((oi.unit_price-COALESCE(p.purchase_price,0))*oi.quantity) estimated_profit
      FROM order_items oi
      JOIN orders o ON o.id=oi.order_id
      LEFT JOIN products p ON p.id=oi.product_id AND p.company_id=o.company_id
      WHERE o.company_id=:cid
        AND {accounting_document_status_sql(status_column='o.status', note_column='o.note')}
      GROUP BY p.id,p.name
      ORDER BY estimated_profit DESC LIMIT 10
    """), {"cid": cid, "sales_import_note": SALES_IMPORT_NOTE}).mappings().all()

    trend_percent = None
    if previous_sales > 0:
        trend_percent = round(((current_sales - previous_sales) / previous_sales) * HUNDRED, 1)

    recommendations = []
    if overdue_count:
        recommendations.append({'severity': 'warning', 'title': f'{overdue_count} gecikmiş alacak var', 'detail': f'Toplam {overdue_total:,.2f} ₺ tahsilat bekliyor.', 'path': '/satislar?status=overdue'})
    if critical:
        recommendations.append({'severity': 'error', 'title': f'{len(critical)} kritik stok ürünü', 'detail': 'Sipariş veya stok transferi planlayın.', 'path': '/urunler?critical=1'})
    if len(inactive):
        recommendations.append({'severity': 'info', 'title': f'{len(inactive)} müşteri 60 gündür alışveriş yapmadı', 'detail': 'Müşteri geri kazanım listesi hazır.', 'path': '/analizler'})
    if trend_percent is not None and trend_percent < -10:
        recommendations.append({'severity': 'warning', 'title': f'Satışlar %{abs(trend_percent):.1f} geriledi', 'detail': 'Son 30 gün önceki 30 güne göre daha düşük.', 'path': '/raporlar'})
    if not recommendations:
        recommendations.append({'severity': 'success', 'title': 'Kritik bir uyarı yok', 'detail': 'Satış, stok ve alacak göstergeleri normal aralıkta.', 'path': '/'})

    return {
        'generated_at': business_now().isoformat(timespec='seconds'),
        'sales_trend': {'current_30_days': round(current_sales,2), 'previous_30_days': round(previous_sales,2), 'change_percent': trend_percent},
        'overdue': {'count': overdue_count, 'total': round(overdue_total,2)},
        'inactive_customers': inactive[:20],
        'critical_products': [dict(x) for x in critical],
        'profitable_products': [dict(x) for x in profitable],
        'recommendations': recommendations,
    }
