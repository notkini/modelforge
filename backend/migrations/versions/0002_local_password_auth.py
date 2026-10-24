"""Allow password-authenticated local accounts."""
from alembic import op
import sqlalchemy as sa

revision = '0002_local_password_auth'
down_revision = '0001_initial'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('users') as batch:
        batch.alter_column('google_sub', existing_type=sa.String(255), nullable=True)
        batch.add_column(sa.Column('password_hash', sa.String(255), nullable=True))

def downgrade():
    bind=op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM users WHERE google_sub IS NULL LIMIT 1')).first():
        raise RuntimeError('Cannot downgrade while password-only accounts exist; deleting or converting them would lock users out.')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('password_hash')
        batch.alter_column('google_sub', existing_type=sa.String(255), nullable=False)
