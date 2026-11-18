import os
from sqlalchemy import create_engine, Integer, String, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

class Base(DeclarativeBase):
    pass

class Stream(Base):
    __tablename__ = 'streams'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String)
    remote: Mapped[dict] = mapped_column(JSON)
    baseline: Mapped[list] = mapped_column(JSON)
    draft: Mapped[list] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {'version_id_col': version}

engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:///./adrobot.db'), connect_args={'check_same_thread': False} if os.getenv('DATABASE_URL', 'sqlite:').startswith('sqlite:') else {})
Session = sessionmaker(engine, expire_on_commit=False)
