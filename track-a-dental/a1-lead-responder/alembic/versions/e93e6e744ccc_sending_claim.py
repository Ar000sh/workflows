"""sending claim

Revision ID: e93e6e744ccc
Revises: a30306790587
Create Date: 2026-10-24 03:32:12.443256
"""
from alembic import op
import sqlalchemy as sa


revision = 'e93e6e744ccc'
down_revision = 'a30306790587'
branch_labels = None
depends_on = None

# The status lists are written out (not imported from the models) so this revision keeps meaning
# the same thing after the models change again.
OLD_REPLY_STATUSES = "status IN ('drafted', 'approved', 'sent', 'rejected', 'failed')"
NEW_REPLY_STATUSES = "status IN ('drafted', 'approved', 'sending', 'sent', 'rejected', 'failed')"


# Autogenerate cannot see CHECK constraints, so the status CHECK is dropped and recreated by hand.
def upgrade() -> None:
    op.add_column('leads', sa.Column('mailbox_key', sa.Text(), nullable=True))
    op.add_column('leads', sa.Column('send_suppressed', sa.Boolean(), server_default='false', nullable=False))
    op.create_index('ix_leads_mailbox_key_created_at', 'leads', ['mailbox_key', 'created_at'], unique=False)
    op.add_column('replies', sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('replies', sa.Column('send_attempts', sa.Integer(), server_default='0', nullable=False))
    op.create_check_constraint('ck_replies_send_attempts', 'replies', 'send_attempts >= 0')
    op.drop_constraint('ck_replies_status', 'replies', type_='check')
    op.create_check_constraint('ck_replies_status', 'replies', NEW_REPLY_STATUSES)


# "sending" means outcome unknown; the old schema has no such status, so those rows become
# "failed" first (otherwise the old CHECK would refuse to be created on them).
def downgrade() -> None:
    op.execute("UPDATE replies SET status = 'failed' WHERE status = 'sending'")
    op.drop_constraint('ck_replies_status', 'replies', type_='check')
    op.create_check_constraint('ck_replies_status', 'replies', OLD_REPLY_STATUSES)
    op.drop_constraint('ck_replies_send_attempts', 'replies', type_='check')
    op.drop_column('replies', 'send_attempts')
    op.drop_column('replies', 'claimed_at')
    op.drop_index('ix_leads_mailbox_key_created_at', table_name='leads')
    op.drop_column('leads', 'send_suppressed')
    op.drop_column('leads', 'mailbox_key')
