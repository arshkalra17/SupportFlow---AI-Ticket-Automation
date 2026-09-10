from datetime import datetime
# pyrefly: ignore [missing-import]
from sqlalchemy import Column, DateTime, Integer, String, Text
from app.database import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    customer_message = Column(Text, nullable=False)
    status = Column(String, default="PENDING")
    category = Column(String, nullable=True)
    priority = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
