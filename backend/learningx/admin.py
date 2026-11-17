"""Trusted operator CLI: user provisioning and short-lived signed pilot tokens.

No public token-minting endpoint, no self-declared faculty role. Run on a secure
operator machine with the database and signing secret configured.
"""
import argparse
from .storage import init_db,SessionLocal,User
from .security import issue_token

def main():
    p=argparse.ArgumentParser(description="LearningX pilot operator commands")
    sub=p.add_subparsers(dest="action",required=True)
    a=sub.add_parser("create-user")
    a.add_argument("--email",required=True)
    a.add_argument("--role",choices=["student","faculty","admin"],required=True)
    b=sub.add_parser("issue-token")
    b.add_argument("--email",required=True)
    b.add_argument("--minutes",type=int,default=60)
    args=p.parse_args()
    init_db()
    with SessionLocal() as db:
        if args.action=="create-user":
            if db.query(User).filter_by(email=args.email.lower()).first():
                raise SystemExit("User already exists")
            u=User(email=args.email.lower(),role=args.role)
            db.add(u);db.commit()
            print("created user_id="+u.id+" role="+u.role)
        else:
            if not (1<=args.minutes<=240):raise SystemExit("Token lifetime must be 1..240 minutes")
            u=db.query(User).filter_by(email=args.email.lower()).first()
            if not u or not u.active:raise SystemExit("Unknown/inactive user")
            print(issue_token(u.id,args.minutes))

if __name__=="__main__":main()
