"""Initial account, conversation, model and session tables."""
from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('users',sa.Column('id',sa.String(36),primary_key=True),sa.Column('google_sub',sa.String(255),nullable=False),sa.Column('email',sa.String(320),nullable=False),sa.Column('display_name',sa.String(200),nullable=False),sa.Column('picture_url',sa.String(2048)),sa.Column('status',sa.String(20),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('last_login_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_users_google_sub','users',['google_sub'],unique=True); op.create_index('ix_users_email','users',['email']); op.create_index('ix_users_status','users',['status'])
    op.create_table('conversations',sa.Column('id',sa.String(36),primary_key=True),sa.Column('owner_id',sa.String(36),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),sa.Column('title',sa.String(240),nullable=False),sa.Column('archived',sa.Boolean(),nullable=False),sa.Column('client_import_id',sa.String(100)),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('owner_id','client_import_id',name='uq_conversation_owner_import'))
    op.create_index('ix_conversations_owner_id','conversations',['owner_id']); op.create_index('ix_conversations_archived','conversations',['archived']); op.create_index('ix_conversation_owner_updated','conversations',['owner_id','updated_at'])
    op.create_table('messages',sa.Column('id',sa.String(36),primary_key=True),sa.Column('conversation_id',sa.String(36),sa.ForeignKey('conversations.id',ondelete='CASCADE'),nullable=False),sa.Column('role',sa.String(12),nullable=False),sa.Column('text',sa.Text(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('metadata',sa.JSON()),sa.CheckConstraint("role in ('user','assistant','system')",name='ck_message_role'))
    op.create_index('ix_messages_conversation_id','messages',['conversation_id']); op.create_index('ix_message_conversation_created','messages',['conversation_id','created_at'])
    op.create_table('generated_models',sa.Column('id',sa.String(36),primary_key=True),sa.Column('owner_id',sa.String(36),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),sa.Column('conversation_id',sa.String(36),sa.ForeignKey('conversations.id',ondelete='CASCADE')),sa.Column('message_id',sa.String(36),sa.ForeignKey('messages.id',ondelete='SET NULL')),sa.Column('parent_version_id',sa.String(36),sa.ForeignKey('generated_models.id',ondelete='SET NULL')),sa.Column('shape_type',sa.String(30),nullable=False),sa.Column('generation_method',sa.String(40),nullable=False),sa.Column('parameters',sa.JSON(),nullable=False),sa.Column('validation',sa.JSON(),nullable=False),sa.Column('version',sa.Integer(),nullable=False),sa.Column('storage_key',sa.String(100)),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('conversation_id','version',name='uq_model_conversation_version'),sa.UniqueConstraint('storage_key'))
    op.create_index('ix_generated_models_owner_id','generated_models',['owner_id']); op.create_index('ix_model_owner_created','generated_models',['owner_id','created_at']); op.create_index('ix_model_conversation_created','generated_models',['conversation_id','created_at'])
    op.create_table('auth_sessions',sa.Column('id',sa.String(36),primary_key=True),sa.Column('user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),sa.Column('token_hash',sa.String(64),nullable=False),sa.Column('csrf_hash',sa.String(64),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_auth_sessions_user_id','auth_sessions',['user_id']); op.create_index('ix_auth_sessions_token_hash','auth_sessions',['token_hash'],unique=True); op.create_index('ix_auth_sessions_expires_at','auth_sessions',['expires_at'])
    op.create_table('auth_nonces',sa.Column('nonce_hash',sa.String(64),primary_key=True),sa.Column('csrf_hash',sa.String(64),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False)); op.create_index('ix_auth_nonces_expires_at','auth_nonces',['expires_at'])

def downgrade():
    op.drop_table('auth_nonces'); op.drop_table('auth_sessions'); op.drop_table('generated_models'); op.drop_table('messages'); op.drop_table('conversations'); op.drop_table('users')
