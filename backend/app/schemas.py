from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator, model_validator
from .business_time import business_today


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _iso_date(value: str | None, field_name: str, *, optional: bool = False) -> str | None:
    value = _clean_optional(value)
    if value is None:
        if optional:
            return None
        raise ValueError(f'{field_name} zorunludur')
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(
        f'{field_name} geçersiz. Beklenen biçim: YYYY-AA-GG veya GG.AA.YYYY'
    )


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    tax_number: str | None = None
    opening_balance: Decimal = Decimal("0.00")
    owner_name: str | None = None
    risk_limit: Decimal = Field(default=Decimal("0.00"), ge=0)
    payment_term_days: int = Field(default=0, ge=0, le=3650)
    notes: str | None = None
    is_active: bool = True

    @field_validator('name')
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = ' '.join(value.split())
        if not value:
            raise ValueError('İsim / ünvan zorunludur')
        return value

    @field_validator('phone', 'email', 'address', 'tax_number', 'owner_name', 'notes')
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class ProductUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    product_code: str | None = None
    barcode: str | None = None
    purchase_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    sale_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    vat_rate: int = Field(default=20, ge=0, le=100)
    stock: Decimal = Decimal("0.0000")
    unit: str = Field(default='Adet', min_length=1, max_length=30)
    category: str | None = None
    location: str | None = None
    oem_number: str | None = None
    alternative_oem: str | None = None
    brand: str | None = None
    manufacturer: str | None = None
    compatible_models: str | None = None
    technical_notes: str | None = None
    # Satışa kapatma. Kolon ve POS/hızlı-seçim filtreleri (COALESCE(p.active,
    # TRUE)=TRUE) zaten vardı, ama bu alan şemada olmadığı için değeri
    # değiştirmenin hiçbir yolu yoktu: ürün detayı "Pasif Ürün" rozetini
    # gösteriyor, hiç kimse pasife alamıyordu.
    active: bool = True

    @field_validator('name', 'unit')
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        value = ' '.join(value.split())
        if not value:
            raise ValueError('Alan boş bırakılamaz')
        return value

    @field_validator('product_code', 'barcode', 'category', 'location', 'oem_number', 'alternative_oem', 'brand', 'manufacturer', 'compatible_models', 'technical_notes')
    @classmethod
    def clean_product_text(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class TransactionItem(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    vat_rate: int = Field(ge=0, le=100)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class PaymentAllocation(BaseModel):
    payment_method: str
    amount: Decimal = Field(gt=0)
    account_id: int | None = Field(default=None, gt=0)


class TransactionCreate(BaseModel):
    entity_id: int = Field(gt=0)
    transaction_date: str
    document_no: str | None = None
    due_date: str | None = None
    note: str | None = None
    warehouse_id: int | None = None
    branch_id: int | None = None
    status: str = 'completed'
    payment_method: str = 'credit'
    payment_term: str = 'PESIN'
    harvest_calendar_id: int | None = Field(default=None, gt=0)
    due_date_override_reason: str | None = Field(default=None, max_length=500)
    paid_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    payment_account_id: int | None = Field(default=None, gt=0)
    payment_allocations: list[PaymentAllocation] = Field(default_factory=list)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    discount_amount: Decimal | None = Field(default=None, ge=0)
    items: list[TransactionItem] = Field(min_length=1)

    @field_validator('transaction_date')
    @classmethod
    def validate_transaction_date(cls, value: str) -> str:
        return str(_iso_date(value, 'Belge tarihi'))

    @field_validator('due_date')
    @classmethod
    def validate_due_date(cls, value: str | None) -> str | None:
        return _iso_date(value, 'Vade tarihi', optional=True)

    @field_validator('document_no', 'note', 'due_date_override_reason')
    @classmethod
    def clean_transaction_text(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator('payment_term')
    @classmethod
    def validate_payment_term(cls, value: str) -> str:
        value = (value or 'PESIN').strip().upper()
        if value not in {'PESIN', 'HARMAN_VADELI'}:
            raise ValueError('Ödeme vadesi PESIN veya HARMAN_VADELI olmalıdır')
        return value

    @model_validator(mode='after')
    def validate_document(self):
        if self.due_date and self.due_date < self.transaction_date:
            raise ValueError('Vade tarihi belge tarihinden önce olamaz')
        if self.due_date and self.harvest_calendar_id:
            raise ValueError('Vade tarihi ile sezon takvimi aynı anda seçilemez')
        if self.payment_allocations:
            allocation_total = sum((item.amount for item in self.payment_allocations), Decimal('0.00'))
            if self.paid_amount not in {Decimal('0'), allocation_total}:
                raise ValueError('Ödenen tutar ile ödeme dağılımı toplamı uyuşmuyor')
            self.paid_amount = allocation_total
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError('Aynı ürün belgede birden fazla satırda bulunamaz; miktarları tek satırda birleştirin')
        return self


class PosSaleItem(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    # Fits NUMERIC(18,2) derived totals and rejects free/overflowing tampered
    # requests before SQLite/PostgreSQL can diverge.
    unit_price: Decimal = Field(gt=0, le=Decimal("100000000"))
    # Per-line discount, same contract as TransactionItem.discount_percent, so the
    # counter can knock money off one line without touching the rest.
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class PosSaleCreate(BaseModel):
    items: list[PosSaleItem] = Field(min_length=1)
    payment_type: str
    # Omitted preserves the historical POS behavior: non-credit sales are paid
    # in full and credit sales are left fully open. Supplying an amount enables
    # an atomic partial collection on a named customer sale.
    paid_amount: Decimal | None = Field(default=None, ge=0)
    note: str | None = None
    warehouse_id: int | None = Field(default=None, gt=0)
    # None means the walk-in retail customer ("Perakende Satış"). A named
    # customer is mandatory for credit sales; that rule lives in the router so it
    # answers 400 rather than a 422 validation envelope.
    customer_id: int | None = Field(default=None, gt=0)
    # Document-level discount, applied after the line discounts. Same contract as
    # TransactionCreate.discount_percent.
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    discount_amount: Decimal | None = Field(default=None, ge=0)

    @field_validator("payment_type")
    @classmethod
    def validate_payment_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"cash", "card", "bank_transfer", "credit"}:
            raise ValueError("Geçersiz ödeme yöntemi")
        return normalized

    @field_validator("note")
    @classmethod
    def clean_pos_note(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @model_validator(mode="after")
    def validate_unique_products(self):
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("Aynı ürün birden fazla satırda bulunamaz")
        return self


class PaymentCreate(BaseModel):
    entity_type: str
    entity_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0)
    payment_date: str
    note: str | None = None
    payment_method: str = 'cash'
    account_id: int | None = None
    reference_type: str | None = None
    reference_id: int | None = Field(default=None, gt=0)

    @field_validator('payment_date')
    @classmethod
    def validate_payment_date(cls, value: str) -> str:
        return str(_iso_date(value, 'İşlem tarihi'))

    @field_validator('note')
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class ProductCreate(ProductUpdate):
    pass


class StockAdjust(BaseModel):
    mode: str = 'add'
    warehouse_id: int | None = None
    quantity: Decimal
    movement_date: str
    note: str | None = None


class BulkPriceUpdate(BaseModel):
    field: str = 'sale_price'
    method: str = 'percent'
    value: Decimal
    product_ids: list[int] | None = None


class BulkStockUpdate(BaseModel):
    method: str = 'add'
    warehouse_id: int | None = None
    value: Decimal
    movement_date: str
    note: str | None = None
    product_ids: list[int] | None = None


class WarehouseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    code: str | None = None
    branch_id: int | None = None
    is_default: bool = False


class StockTransferItem(BaseModel):
    product_id: int
    quantity: Decimal = Field(gt=0)


class StockTransferCreate(BaseModel):
    source_warehouse_id: int
    target_warehouse_id: int
    transfer_date: str
    note: str | None = None
    items: list[StockTransferItem] = Field(min_length=1)


class CriticalStockUpdate(BaseModel):
    product_id: int
    warehouse_id: int
    critical_stock: Decimal = Field(ge=0)


class WorkflowDocumentCreate(BaseModel):
    entity_id: int
    document_date: str
    valid_until: str | None = None
    delivery_date: str | None = None
    document_no: str | None = None
    warehouse_id: int | None = None
    status: str = 'draft'
    note: str | None = None
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    source_type: str | None = None
    source_id: int | None = None
    items: list[TransactionItem] = Field(min_length=1)


class FinancialAccountCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    account_type: str = 'cash'
    currency: str = 'TRY'
    opening_balance: Decimal = Decimal("0.00")
    bank_name: str | None = None
    iban: str | None = None
    is_active: bool = True


class FinancialTransactionCreate(BaseModel):
    account_id: int
    txn_date: str
    direction: str
    amount: Decimal = Field(gt=0)
    category: str | None = None
    payment_method: str | None = None
    description: str | None = None


class FinancialTransferCreate(BaseModel):
    source_account_id: int
    target_account_id: int
    txn_date: str
    amount: Decimal = Field(gt=0)
    description: str | None = None


class FinancialInstrumentCreate(BaseModel):
    instrument_type: str
    direction: str
    entity_type: str | None = None
    entity_id: int | None = None
    amount: Decimal = Field(gt=0)
    issue_date: str | None = None
    due_date: str
    serial_no: str | None = None
    bank_name: str | None = None
    account_no: str | None = None
    note: str | None = None


class FinancialInstrumentStatusUpdate(BaseModel):
    status: str
    account_id: int | None = None
    transaction_date: str | None = None


# --- V3.0 Machine Cards --------------------------------------------------------

MACHINE_STATUSES = {"active", "maintenance", "idle", "sold", "scrapped"}

# working_hours -> machines.working_hours NUMERIC(12,2). Bound to the column
# maximum so oversized input is a clean HTTP 422 on both SQLite and PostgreSQL
# rather than a NUMERIC overflow (DataError/500) on PostgreSQL.
MAX_MACHINE_WORKING_HOURS = Decimal("9999999999.99")


class MachineCreate(BaseModel):
    customer_id: int | None = Field(default=None, gt=0)
    brand: str = Field(min_length=1, max_length=160)
    manufacturer: str | None = None
    model: str = Field(min_length=1, max_length=160)
    variant: str | None = None
    serial_number: str | None = None
    chassis_number: str | None = None
    registration_number: str | None = None
    engine_number: str | None = None
    model_year: int | None = None
    working_hours: Decimal = Field(default=Decimal("0.00"), ge=0, le=MAX_MACHINE_WORKING_HOURS)
    status: str = "active"
    notes: str | None = None
    is_active: bool = True

    @field_validator("brand", "model")
    @classmethod
    def clean_required(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Marka ve model zorunludur")
        return value

    @field_validator(
        "manufacturer",
        "variant",
        "serial_number",
        "chassis_number",
        "registration_number",
        "engine_number",
        "notes",
    )
    @classmethod
    def clean_optional_identifiers(cls, value: str | None) -> str | None:
        # Empty optional identifiers normalise to NULL so they never collide with
        # the per-company chassis/serial uniqueness rule.
        return _clean_optional(value)

    @field_validator("status")
    @classmethod
    def clean_status(cls, value: str) -> str:
        value = (value or "").strip().lower()
        if value not in MACHINE_STATUSES:
            raise ValueError("Geçersiz makine durumu")
        return value

    @field_validator("model_year")
    @classmethod
    def check_model_year(cls, value: int | None) -> int | None:
        if value is None:
            return None
        current_year = business_today().year
        if value < 1900 or value > current_year + 1:
            raise ValueError("Model yılı geçersiz")
        return value


class MachineUpdate(MachineCreate):
    """A full replacement of an existing machine card (PUT)."""


class MachineStatusUpdate(BaseModel):
    is_active: bool
