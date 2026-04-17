
import asyncio
import sqlite3
import os

async def check_db():
    db_path = r"d:\Edutainer\intelligent-adaptive-mock\data\mock_platform.db"
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"Tables: {tables}")
    
    for table_name in [t[0] for t in tables]:
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"Table {table_name}: {count} rows")
        
        if table_name == 'endpoints':
            cursor.execute("SELECT * FROM endpoints")
            rows = cursor.fetchall()
            print(f"Endpoints: {rows}")
            
    conn.close()

if __name__ == "__main__":
    asyncio.run(check_db())
