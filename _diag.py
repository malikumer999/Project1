import sqlite3

conn = sqlite3.connect("data/jobs.db")
rows = conn.execute(
    "SELECT source_query, posted_to_discord, COUNT(*) FROM jobs "
    "WHERE lower(source_query) LIKE '%seo%' GROUP BY source_query, posted_to_discord"
).fetchall()
print("SEO rows:", rows)
print("total jobs:", conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
print("all source_queries and counts:")
for row in conn.execute(
    "SELECT source_query, COUNT(*) FROM jobs GROUP BY source_query ORDER BY source_query"
).fetchall():
    print("  ", row)
conn.close()
