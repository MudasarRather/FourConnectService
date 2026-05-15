"""Idempotent DDL — drop `cost_center` and `budget_type` from projects.

Run:
    & "C:\\Users\\91700\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" drop_cost_center_budget_type.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import engine

DROPPED = ("cost_center", "budget_type")

with engine.begin() as conn:
    for col in DROPPED:
        conn.execute(text(f'ALTER TABLE projects DROP COLUMN IF EXISTS {col}'))
        print(f"  - dropped projects.{col} (or already absent)")
print("[migrate] cost_center + budget_type removed from projects")
