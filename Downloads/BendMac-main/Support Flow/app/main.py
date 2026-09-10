# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Depends, HTTPException, status
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

# pyrefly: ignore [missing-import]
from app.queue import enqueue_ticket_processing
from app.database import engine, Base, get_db
from app.models import Ticket

# Ensure tables are created
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Support Flow API")


class TicketCreate(BaseModel):
    message: str


class TicketResponse(BaseModel):
    id: int
    status: str
    category: str | None = None
    priority: str | None = None

    class Config:
        from_attributes = True


class TicketDetail(BaseModel):
    id: int
    customer_message: str
    status: str
    category: str | None = None
    priority: str | None = None

    class Config:
        from_attributes = True


@app.post("/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(ticket_data: TicketCreate, db: Session = Depends(get_db)):
    # 1. Create and commit ticket with status PENDING
    ticket = Ticket(
        customer_message=ticket_data.message,
        status="PENDING"
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    # 2. Enqueue ticket processing job into Redis asynchronously
    try:
        enqueue_ticket_processing(ticket.id)
    except Exception as err:
        ticket.status = "QUEUE_FAILED"
        db.commit()
        db.refresh(ticket)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ticket created (ID: {ticket.id}) but queue enqueueing failed: {err}"
        )

    # 3. Return ticket immediately with status PENDING
    return ticket



@app.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket with id {ticket_id} not found"
        )
    return ticket


