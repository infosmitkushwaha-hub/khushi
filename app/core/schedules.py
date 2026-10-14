"""Schedule day sets shared by the scheduler and Sarthi's missed-run check."""

# python-telegram-bot JobQueue days: 0 = Sunday ... 6 = Saturday
DAYS = {"daily": (0, 1, 2, 3, 4, 5, 6), "mon-fri": (1, 2, 3, 4, 5), "mon": (1,), "sun": (0,)}
# Python datetime.weekday(): 0 = Monday ... 6 = Sunday
DAYS_PY = {"daily": set(range(7)), "mon-fri": {0, 1, 2, 3, 4}, "mon": {0}, "sun": {6}}
