"""Built during the Meta Global AI Developer Hackathon (new work).

encrypt stored Instagram tokens; add ig_reel.analysis_level

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-11-04

* ig_reel.analysis_level: how a reel was read (full video frames / thumbnail and caption / caption only).
* Existing plain-text values of users.instagram_access_token are re-saved Fernet-encrypted. This needs TOKEN_ENCRYPTION_KEY in
  backend/.env or the environment. Without it the migration stops when there are tokens to encrypt (set the key, run it again);
  with no tokens stored it passes. The app also reads plain tokens transparently and encrypts one on first use.
"""
import sqlalchemy as sa
from alembic import op

revision = "o5p6q7r8s9t0"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ig_reel", sa.Column("analysis_level", sa.String(20), nullable=True))
    from app.studio.instagram import tokencrypt
    conn = op.get_bind()
    plain = conn.execute(sa.text(
        "SELECT count(*) FROM users WHERE instagram_access_token IS NOT NULL AND instagram_access_token <> '' "
        "AND instagram_access_token NOT LIKE :p"), {"p": tokencrypt.PREFIX + "%"}).scalar()
    if plain:
        if not tokencrypt.key_configured():
            raise RuntimeError(f"{plain} Instagram token(s) are stored in plain text: set TOKEN_ENCRYPTION_KEY "
                               "(python -m app.studio.instagram.tokencrypt generate) and run the migration again.")
        tokencrypt.encrypt_existing(conn)


def downgrade() -> None:
    from app.studio.instagram import tokencrypt
    conn = op.get_bind()
    tokencrypt.decrypt_existing(conn)   # back to plain text (needs the key if any token is encrypted)
    op.drop_column("ig_reel", "analysis_level")
