from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from ..change_history import list_history, restore_deleted
from ..db import get_db
from ..tenancy import company_id

router = APIRouter(prefix="/history", tags=["history"])

@router.get("/{entity_type}/{entity_id}")
def history(entity_type: str, entity_id: int, request: Request, limit: int = 200, db: Session = Depends(get_db)):
    return list_history(db, company_id(request), entity_type, entity_id, limit)

@router.post("/restore/{log_id}")
def restore(log_id: int, request: Request, db: Session = Depends(get_db)):
    result = restore_deleted(db, request, company_id=company_id(request), log_id=log_id)
    db.commit()
    return result
