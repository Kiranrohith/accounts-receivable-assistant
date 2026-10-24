"""Customer master data. Every business field is required; the tax ID (a GSTIN, a VAT number, a TRN...), e-mail and the
normalised name (trimmed, spaces collapsed, case-insensitive) are each unique.
Aliases are extra names or e-mail addresses a person has confirmed for a customer."""

import uuid

from sqlalchemy import CHAR, Boolean, Enum, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from data.models.postgres.base import AuditMixin, Base, uuid_pk
from data.models.postgres.enums import AliasSource, AliasType, TaxIdType


class Customer(Base, AuditMixin):
    __tablename__ = "customers"
    __table_args__ = (
        Index("uq_customers_email_lower", text("lower(email)"), unique=True),
        Index(
            "uq_customers_name_normalized",
            text(r"lower(regexp_replace(btrim(customer_name), '\s+', ' ', 'g'))"),
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_name: Mapped[str] = mapped_column(String, nullable=False)
    contact: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(
        String, nullable=False, comment="primary contact + L2 sender key"
    )
    tax_id: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        comment="the customer's registration number, stored normalized (upper case, no spaces, dots, hyphens or slashes): "
        "document -> customer deterministic key",
    )
    tax_id_type: Mapped[TaxIdType | None] = mapped_column(
        Enum(TaxIdType, name="tax_id_type", values_callable=lambda e: [m.value for m in e]),
        comment="what kind of number tax_id is (GSTIN, VAT, TRN, EIN, OTHER)",
    )
    deducts_tds: Mapped[bool] = mapped_column(Boolean, nullable=False)
    billing_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    country: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerAlias(Base, AuditMixin):
    __tablename__ = "customer_aliases"
    __table_args__ = (
        UniqueConstraint(
            "customer_id", "alias_type", "alias_value", name="uq_customer_aliases_per_customer"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    alias_type: Mapped[AliasType] = mapped_column(
        Enum(AliasType, name="alias_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    alias_value: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[AliasSource | None] = mapped_column(
        Enum(AliasSource, name="alias_source", values_callable=lambda e: [m.value for m in e])
    )
