from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def make_session_factory(database_url):
    engine = create_engine(database_url)
    return sessionmaker(bind=engine)
