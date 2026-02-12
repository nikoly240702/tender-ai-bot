"""merge heads

Revision ID: 283d540b9b7d
Revises: 20260210_sheets_exp, e5f6g7h8i9j0
Create Date: 2026-02-12 15:02:24.796403

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '283d540b9b7d'
down_revision: Union[str, Sequence[str], None] = ('20260210_sheets_exp', 'e5f6g7h8i9j0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
