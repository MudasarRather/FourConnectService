
from app.database import SessionLocal
from app.models.user import User

def check_specific_admin():
    db = SessionLocal()
    try:
        target_email = "user@fourreck.com"
        admin = db.query(User).filter(User.email == target_email).first()
        
        if admin:
            print(f"User: {admin.full_name} ({admin.email})")
            print(f"Is Superuser: {admin.is_superuser}")
            
            if not admin.is_superuser:
                print(f"Promoting {target_email} to Superuser...")
                admin.is_superuser = True
                db.commit()
                print("Promotion successful.")
            else:
                print("User is already a Superuser.")
        else:
            print(f"User {target_email} not found!")

    finally:
        db.close()

if __name__ == "__main__":
    check_specific_admin()
