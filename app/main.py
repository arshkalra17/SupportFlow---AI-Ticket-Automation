# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Depends, Header, HTTPException, status
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, ConfigDict, EmailStr
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

# pyrefly: ignore [missing-import]
from app.queue import enqueue_ticket_processing
from app.database import engine, Base, get_db
from app.models import Ticket, Customer
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_customer,
)
from app.tools import authorize_and_get_order_status
from app.graph import run_supportflow
from app.idempotency import execute_with_idempotency
from app.observability import init_observability, traced_span, get_customer_identifier
from app.middleware import TraceMiddleware
from app.health import router as health_router
from fastapi.middleware.cors import CORSMiddleware

# Ensure tables are created
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Support Flow API")

app.add_middleware(TraceMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Include health check endpoints
app.include_router(health_router)


@app.on_event("startup")
def startup_event():
    """Initialize observability on application startup."""
    init_observability()


@app.on_event("shutdown")
def shutdown_event():
    """Cleanup on application shutdown."""
    # Close database connections
    engine.dispose()
    # Close Redis connections
    from app.queue import redis_client
    redis_client.close()


# ── Pydantic Schemas ───────────────────────────────────────────────────

class UserRegister(BaseModel):
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CustomerResponse(BaseModel):
    id: int
    email: str

    class Config:
        from_attributes = True


class SupportProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


class SupportProcessResponse(BaseModel):
    final_response: str
    classification: dict | None = None
    tool_calls: list[dict] = []
    approval_status: str | None = None
    retrieved_documents: list[dict] | None = None
    error: str | None = None
    idempotency_replayed: bool = False


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


# ── Authentication Endpoints ───────────────────────────────────────────

@app.post("/auth/register", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
def register_customer(data: UserRegister, db: Session = Depends(get_db)):
    """Registers a new customer with hashed password storage."""
    existing = db.query(Customer).filter(Customer.email == data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered",
        )

    pwd_hash = hash_password(data.password)
    customer = Customer(
        email=data.email,
        password_hash=pwd_hash,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@app.post("/auth/login", response_model=TokenResponse)
def login_customer(data: UserLogin, db: Session = Depends(get_db)):
    """Authenticates credentials and issues a signed JWT access token."""
    customer = db.query(Customer).filter(Customer.email == data.email).first()
    if not customer or not verify_password(data.password, customer.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(customer_id=customer.id)
    return TokenResponse(access_token=token)


# ── LangGraph Workflow Endpoint (JWT Authenticated & Idempotent) ────────

@app.post("/support/process", response_model=SupportProcessResponse)
def process_support_message(
    data: SupportProcessRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    current_customer: Customer = Depends(get_current_customer),
):
    """Processes a customer message through the LangGraph AI workflow with idempotency support.

    Identity is derived strictly from the verified JWT token via `get_current_customer()`.
    Supports optional `Idempotency-Key` header for safe request retries.
    """
    with traced_span(
        "supportflow.process_request",
        attributes={
            "customer_id": get_customer_identifier(current_customer.id),
            "has_idempotency_key": idempotency_key is not None,
        }
    ) as span:
        def action():
            return run_supportflow(
                customer_message=data.message,
                authenticated_customer_id=current_customer.id,
            )

        state, is_replayed = execute_with_idempotency(
            customer_id=current_customer.id,
            idempotency_key=idempotency_key,
            operation_type="SUPPORT_PROCESS",
            request_params={"message": data.message},
            action_fn=action,
        )

        if span:
            span.set_attribute("idempotency_replayed", is_replayed)

        return SupportProcessResponse(
            final_response=state.get("final_response", ""),
            classification=state.get("classification"),
            tool_calls=state.get("tool_calls", []),
            approval_status=state.get("approval_status"),
            retrieved_documents=state.get("retrieved_documents"),
            error=state.get("error"),
            idempotency_replayed=is_replayed,
        )


# ── Protected Order Endpoint (JWT -> Existing Auth Layer Boundary) ────

@app.get("/orders/{order_id}")
def get_order(
    order_id: int,
    current_customer: Customer = Depends(get_current_customer),
):
    """Protected order status endpoint.

    Extracts identity strictly from verified JWT via `get_current_customer()`
    and passes it to `app.tools.authorize_and_get_order_status()`, which enforces
    existing resource ownership authorization rules.
    """
    authorized, err_msg, result = authorize_and_get_order_status(
        authenticated_customer_id=current_customer.id,
        order_id=order_id,
    )

    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=err_msg,
        )

    return result


# ── Ticket Endpoints ───────────────────────────────────────────────────

@app.post("/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(ticket_data: TicketCreate, db: Session = Depends(get_db)):
    ticket = Ticket(
        customer_message=ticket_data.message,
        status="PENDING"
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

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
