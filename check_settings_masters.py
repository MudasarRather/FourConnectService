"""Read-only probe: confirm Phase B settings tables/columns/seed landed."""
import os, re, platform
_ur = platform.uname_result("Windows", "localhost", "11", "10.0.26200", "AMD64")
_ur.__dict__["processor"] = "Intel"; platform._uname_cache = _ur
platform._Processor.get = staticmethod(lambda: "Intel")
import psycopg2

def db_url():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    for line in open(p, encoding="utf-8"):
        s = line.strip()
        if s.startswith("DATABASE_URL") and "=" in s:
            return s.partition("=")[2].strip().strip('"').strip("'")
    return ""

m = re.search(r"postgresql(?:\+\w+)?://(.*?):(.*?)@(.*?):(\d+)/(.*)", db_url())
u, pw, h, port, dbn = m.groups()
c = psycopg2.connect(dbname=dbn, user=u, password=pw, host=h, port=port); cur = c.cursor()

for t in ("hr_employment_type_master", "hr_employee_category_master", "hr_separation_reason_master",
          "hr_notification_rules"):
    cur.execute(f"SELECT count(*) FROM {t}")
    print(f"{t}: {cur.fetchone()[0]} rows")

cur.execute("SELECT category, count(*) FROM hr_separation_reason_master GROUP BY category ORDER BY category")
print("separation by category:", dict(cur.fetchall()))

# New columns present on existing master tables?
cur.execute("""SELECT table_name, column_name FROM information_schema.columns
               WHERE (table_name='hr_departments' AND column_name='cost_center')
                  OR (table_name='hr_designations' AND column_name IN ('reporting_to_designation_id','approval_authority'))
                  OR (table_name='hr_grades' AND column_name='eligibility')
                  OR (table_name='hr_work_locations' AND column_name IN ('code','timezone','weekly_off_pattern'))
               ORDER BY table_name, column_name""")
print("new columns:", cur.fetchall())

# Compatibility: do existing employees' enum values all have a master row?
cur.execute("""SELECT e.employment_type::text, m.code
               FROM (SELECT DISTINCT employment_type FROM hr_employees WHERE employment_type IS NOT NULL) e
               LEFT JOIN hr_employment_type_master m ON m.code = e.employment_type::text""")
rows = cur.fetchall()
missing = [r[0] for r in rows if r[1] is None]
print(f"employment_type values in use: {len(rows)}; without a master row: {missing or 'none'}")

cur.close(); c.close()
print("OK")
