from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Email, User

DEMO_EMAILS = [
 {"external_id":"demo-invoice","sender":"billing@northstar-office.example","subject":"Invoice INV-2048 requires approval by July 21","received_at":datetime(2026,7,19,9,15),"body":"Please approve invoice INV-2048 for USD 1,280 by July 21, 2026. The invoice PDF is attached. Before approval, confirm that purchase order PO-774 appears in your records."},
 {"external_id":"demo-meeting","sender":"maya@acme.example","subject":"Choose a time for the supplier review","received_at":datetime(2026,7,19,10,5),"body":"Please confirm whether you can attend on July 22 at 10:00 AM or July 23 at 2:30 PM. Bring the June delivery metrics. Reply with your preferred time by July 20."},
 {"external_id":"demo-documents","sender":"compliance@harbor.example","subject":"Updated documents needed for vendor renewal","received_at":datetime(2026,7,19,11,30),"body":"To complete the vendor renewal, send your current W-9 form and proof of insurance. We need both documents by July 24, 2026. If either document is unavailable, tell us before the deadline."},
 {"external_id":"demo-info","sender":"operations@acme.example","subject":"Office access maintenance on Sunday","received_at":datetime(2026,7,19,12,20),"body":"The employee entrance will be unavailable on Sunday, July 26, from 8:00 AM until noon. Use the visitor entrance during that period. No response is required."},
 {"external_id":"demo-newsletter","sender":"updates@productweekly.example","subject":"This week's product and design stories","received_at":datetime(2026,7,19,13),"body":"This week: five interface trends, an interview with a design leader, and our recommended reading list. Visit https://productweekly.example/july to read the issue. You are receiving this monthly newsletter because you subscribed."},
]

def load_demo_emails(db: Session, user: User | None = None):
    from .auth import DEMO_USER_ID, ensure_demo_user
    if user is None:
        user = ensure_demo_user(db)
    if user.id != DEMO_USER_ID:
        raise ValueError("Demo emails may only be seeded for the dedicated demo user")
    existing = set(db.scalars(select(Email.external_id).where(Email.user_id == user.id)).all())
    for item in DEMO_EMAILS:
        if item["external_id"] not in existing:
            db.add(Email(**item, source="demo", user_id=user.id))
    db.commit()
    return list(db.scalars(select(Email).where(Email.user_id == user.id).order_by(Email.received_at.desc())).all())
