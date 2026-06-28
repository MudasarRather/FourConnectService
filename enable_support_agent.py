"""Flag (or unflag) a user as a Support Desk agent so they can work the desk on
the /user panel. Mirrors ensure_admin.py.

Usage (from backend root):
    python enable_support_agent.py someone@fourreck.com          # grant
    python enable_support_agent.py someone@fourreck.com --off     # revoke
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.models.user import User


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python enable_support_agent.py <email> [--off]")
        return
    email = sys.argv[1].strip().lower()
    grant = "--off" not in sys.argv[2:]
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email.ilike(email)).first()
        if not user:
            print(f"User not found: {email}")
            return
        user.is_support_agent = grant
        db.commit()
        print(f"{'Granted' if grant else 'Revoked'} support-agent for {user.email} "
              f"(is_support_agent={user.is_support_agent}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
