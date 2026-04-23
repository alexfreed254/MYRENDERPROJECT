import pytz
from datetime import datetime

EAT = pytz.timezone('Africa/Nairobi')

def now_eat():
    """Return current datetime in East Africa Time (UTC+3, Nairobi)."""
    return datetime.now(EAT)

def now_eat_naive():
    """Return current EAT datetime without tzinfo (for DB storage as TIMESTAMP)."""
    return datetime.now(EAT).replace(tzinfo=None)
