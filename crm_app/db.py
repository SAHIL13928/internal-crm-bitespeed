import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DB_PATH = os.environ.get("CRM_DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "crm.db"))
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    future=True,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
