from db import get_connection

def test_connection():
    try:
        print("Attempting to connect to database...")
        conn = get_connection()
        print("✅ Connection successful!")
        
        cursor = conn.cursor()
        cursor.execute("SELECT 1 as test")
        result = cursor.fetchone()
        print(f"✅ Test query result: {result}")
        
        # Check if we can see the database name
        cursor.execute("SELECT DATABASE()")
        db_name = cursor.fetchone()
        print(f"✅ Connected to database: {db_name[0]}")
        
        cursor.close()
        conn.close()
        print("✅ Connection closed properly")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_connection()