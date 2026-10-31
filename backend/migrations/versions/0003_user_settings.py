"""Per-user provider settings (encrypted API key)."""
from alembic import op
import sqlalchemy as sa

revision = '0003_user_settings'
down_revision = '0002_local_password_auth'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'user_settings',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True, unique=True),
        sa.Column('provider', sa.String(20), nullable=False, server_default='local'),
        sa.Column('api_key_encrypted', sa.Text(), nullable=True),
        sa.Column('connection_status', sa.String(20), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

def downgrade():
    op.drop_table('user_settings')
