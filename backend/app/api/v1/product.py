"""Product router — list available trading products (SPOT, UMARGIN, CMARGIN, …)."""
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import RefProduct

router = APIRouter(prefix="/product", tags=["products"])


class ProductResponse(BaseModel):
    id: int
    name: str
    slug: str

    class Config:
        from_attributes = True


@router.get("", response_model=List[ProductResponse])
async def list_products(db: Session = Depends(get_db)):
    """Return all ref_product rows ordered by id."""
    rows = db.query(RefProduct).order_by(RefProduct.id).all()
    return [ProductResponse(id=r.id, name=r.name, slug=r.slug) for r in rows]
