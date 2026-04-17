
import sqlite3
import os

def check_schema():
    db_path = r"d:\Edutainer\intelligent-adaptive-mock\data\mock_platform.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(endpoints);")
    columns = cursor.fetchall()
    print(f"Columns: {columns}")
    conn.close()

if __name__ == "__main__":
    check_schema()
