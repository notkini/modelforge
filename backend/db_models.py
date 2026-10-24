from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

def now_utc(): return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    picture_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='active', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    conversations: Mapped[list['Conversation']] = relationship(back_populates='owner', cascade='all, delete-orphan')

class Conversation(Base):
    __tablename__ = 'conversations'
    __table_args__ = (UniqueConstraint('owner_id', 'client_import_id', name='uq_conversation_owner_import'), Index('ix_conversation_owner_updated', 'owner_id', 'updated_at'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(240), default='New creation')
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    client_import_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    owner: Mapped[User] = relationship(back_populates='conversations')
    messages: Mapped[list['Message']] = relationship(back_populates='conversation', cascade='all, delete-orphan', order_by='Message.created_at')

class Message(Base):
    __tablename__ = 'messages'
    __table_args__ = (CheckConstraint("role in ('user','assistant','system')", name='ck_message_role'), Index('ix_message_conversation_created', 'conversation_id', 'created_at'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    metadata_json: Mapped[dict | None] = mapped_column('metadata', JSON, nullable=True)
    conversation: Mapped[Conversation] = relationship(back_populates='messages')

class GeneratedModel(Base):
    __tablename__ = 'generated_models'
    __table_args__ = (Index('ix_model_owner_created', 'owner_id', 'created_at'), Index('ix_model_conversation_created', 'conversation_id', 'created_at'), UniqueConstraint('conversation_id', 'version', name='uq_model_conversation_version'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'), nullable=True)
    parent_version_id: Mapped[str | None] = mapped_column(ForeignKey('generated_models.id', ondelete='SET NULL'), nullable=True)
    shape_type: Mapped[str] = mapped_column(String(30))
    generation_method: Mapped[str] = mapped_column(String(40))
    parameters_json: Mapped[dict] = mapped_column('parameters', JSON)
    validation_json: Mapped[dict] = mapped_column('validation', JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    storage_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user: Mapped[User] = relationship()

class AuthNonce(Base):
    __tablename__ = 'auth_nonces'
    nonce_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
