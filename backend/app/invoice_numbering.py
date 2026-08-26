from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session

def next_invoice_number(db: Session, company_id: int, branch_prefix: str="", *, now: datetime|None=None) -> str:
    year=(now or datetime.now(timezone.utc)).year
    prefix="".join(ch for ch in branch_prefix.upper().strip() if ch.isalnum())[:20]
    dialect=db.get_bind().dialect.name
    if dialect=="postgresql":
        value=db.execute(text("""INSERT INTO invoice_counters(company_id,year,branch_prefix,current_value)
            VALUES(:cid,:year,:prefix,1) ON CONFLICT(company_id,year,branch_prefix)
            DO UPDATE SET current_value=invoice_counters.current_value+1 RETURNING current_value"""),
            {"cid":company_id,"year":year,"prefix":prefix}).scalar_one()
    else:
        db.execute(text("INSERT OR IGNORE INTO invoice_counters(company_id,year,branch_prefix,current_value) VALUES(:cid,:year,:prefix,0)"),{"cid":company_id,"year":year,"prefix":prefix})
        value=db.execute(text("UPDATE invoice_counters SET current_value=current_value+1 WHERE company_id=:cid AND year=:year AND branch_prefix=:prefix RETURNING current_value"),{"cid":company_id,"year":year,"prefix":prefix}).scalar_one()
    return f"{prefix+'-' if prefix else ''}{year}-{int(value):06d}"
