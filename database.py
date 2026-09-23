import sqlite3

def get_db_connection():
    return sqlite3.connect('database.db')

def db_setup():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()