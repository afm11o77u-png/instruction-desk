"""Rental foundation. Dedicated database; inventory is read-only."""
from .service import can_open, can_write, initialize, catalog, get, save
