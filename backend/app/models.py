from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared registry for the application's database tables."""


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("name ~ '[^[:space:]]'", name="ck_datasets_name_not_blank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_datasets_owner"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TextSample(Base):
    __tablename__ = "text_samples"
    __table_args__ = (
        UniqueConstraint("dataset_id", "id", name="uq_text_samples_dataset_id_id"),
        CheckConstraint("text ~ '[^[:space:]]'", name="ck_text_samples_text_not_blank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source_date: Mapped[date | None] = mapped_column(Date)


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("dataset_id", "id", name="uq_topics_dataset_id_id"),
        CheckConstraint("name ~ '[^[:space:]]'", name="ck_topics_name_not_blank"),
        CheckConstraint(
            "jsonb_typeof(matching_phrases) = 'array'",
            name="ck_topics_matching_phrases_is_array",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    matching_phrases: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=sql_text("'[]'::jsonb")
    )


Index(
    "uq_topics_dataset_normalized_name",
    Topic.dataset_id,
    func.lower(func.regexp_replace(Topic.name, "^[[:space:]]+|[[:space:]]+$", "", "g")),
    unique=True,
)


class SentimentResult(Base):
    __tablename__ = "sentiment_results"
    __table_args__ = (
        # Both foreign keys share dataset_id, so cross-dataset results are impossible.
        ForeignKeyConstraint(
            ["dataset_id", "sample_id"],
            ["text_samples.dataset_id", "text_samples.id"],
            name="fk_sentiment_results_sample",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "topic_id"],
            ["topics.dataset_id", "topics.id"],
            name="fk_sentiment_results_topic",
            ondelete="CASCADE",
        ),
        UniqueConstraint("sample_id", "topic_id", name="uq_sentiment_results_sample_topic"),
        CheckConstraint(
            "label IN ('positive', 'neutral', 'negative')",
            name="ck_sentiment_results_label",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_sentiment_results_confidence",
        ),
        Index("ix_sentiment_results_dataset_topic", "dataset_id", "topic_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column()
    sample_id: Mapped[int] = mapped_column()
    topic_id: Mapped[int] = mapped_column()
    label: Mapped[str] = mapped_column(String(8))
    confidence: Mapped[Decimal] = mapped_column(Numeric)
    model_name: Mapped[str] = mapped_column(Text)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    evidence_text: Mapped[str] = mapped_column(Text)
