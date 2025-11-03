from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Paper trading database (default)
PAPER_DATABASE_URL = "sqlite:///./data.db"
# Real trading database (separate)
REAL_DATABASE_URL = "sqlite:///./real_trading.db"

# Create engines for both databases
paper_engine = create_engine(
    PAPER_DATABASE_URL, connect_args={"check_same_thread": False}
)
real_engine = create_engine(
    REAL_DATABASE_URL, connect_args={"check_same_thread": False}
)

# Create sessionmakers for both databases
PaperSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=paper_engine)
RealSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=real_engine)

# Keep SessionLocal for backward compatibility (defaults to paper trading)
SessionLocal = PaperSessionLocal

Base = declarative_base()


def get_db_for_mode(trade_mode: str = "paper"):
    """
    Get database session based on trade_mode.
    
    Args:
        trade_mode: "real" for real trading database, "paper" (or anything else) for paper trading database
    
    Returns:
        Database session
    """
    if trade_mode == "real":
        db = RealSessionLocal()
    else:
        db = PaperSessionLocal()
    
    try:
        yield db
    finally:
        db.close()


def get_session_for_mode(trade_mode: str = "paper"):
    """
    Get a database session object (not a generator) based on trade_mode.
    
    Args:
        trade_mode: "real" for real trading database, "paper" (or anything else) for paper trading database
    
    Returns:
        Database session object
    """
    if trade_mode == "real":
        return RealSessionLocal()
    else:
        return PaperSessionLocal()


def get_db():
    """Default database session (paper trading for backward compatibility)"""
    db = PaperSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_account_db(account):
    """
    Get database session for a specific account based on its trade_mode.
    
    Args:
        account: Account object with trade_mode attribute
    
    Returns:
        Database session
    """
    trade_mode = getattr(account, 'trade_mode', 'paper')
    return get_session_for_mode(trade_mode)
