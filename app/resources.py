import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import BusinessResource, Email

RESOURCE_TYPES = ["procedure", "policy", "role_directory", "template", "instruction"]
MAX_RESOURCE_CHARS = 12_000
MAX_SELECTED_RESOURCE_CHARS = 18_000
DEMO_RESOURCES = [
 {"title":"Expense reimbursement procedure","resource_type":"procedure","organization_team":"Finance","content":"Employees must submit expense reimbursement requests within 30 days of purchase. Receipts are required for expenses above USD 25."},
 {"title":"Employee responsibility directory","resource_type":"role_directory","organization_team":"Operations","content":"Vendor compliance documents are owned by the Operations Manager. Finance owns invoice approvals and purchase order verification."},
 {"title":"Invoice approval policy","resource_type":"policy","organization_team":"Finance","content":"Invoices above USD 1,000 require purchase order verification before approval. Finance must record the approval decision in the invoice register."},
]
STOPWORDS = {"the","and","for","from","with","this","that","your","must","before","after","within","into","are","our","has","have"}

def seed_demo_resources(db: Session):
    existing=set(db.scalars(select(BusinessResource.title)).all())
    for data in DEMO_RESOURCES:
        if data["title"] not in existing: db.add(BusinessResource(**data,enabled=True))
    db.commit()

def _terms(text):
    return {item for item in re.findall(r"[a-z0-9]+",text.casefold()) if len(item)>2 and item not in STOPWORDS}

def select_relevant_resources(db:Session,email:Email,limit=3):
    email_terms=_terms(f"{email.subject} {email.body}")
    resources=db.scalars(select(BusinessResource).where(BusinessResource.enabled.is_(True)).order_by(BusinessResource.id)).all()
    scored=[]
    for resource in resources:
        title_terms=_terms(f"{resource.title} {resource.resource_type} {resource.organization_team or ''}")
        score=3*len(email_terms&title_terms)+len(email_terms&_terms(resource.content))
        if score>=3: scored.append((score,resource))
    scored.sort(key=lambda item:(-item[0],item[1].id))
    selected=[]; total=0
    for _,resource in scored[:limit]:
        if total+len(resource.content)<=MAX_SELECTED_RESOURCE_CHARS:
            selected.append(resource); total+=len(resource.content)
    return selected

def resource_locator(resource,quote):
    start=resource.content.find(quote)
    if start<0: raise ValueError("Resource quote does not exactly match stored content")
    return start,start+len(quote)

def first_resource_sentence(resource):
    sentence=re.split(r"(?<=[.!?])\s+",resource.content.strip())[0]
    return sentence
