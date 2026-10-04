import sqlite3
import os


# ==================================================
# DATABASE PATH
# ==================================================

DATABASE_PATH = "database/medtrack.db"


# ==================================================
# CREATE DATABASE
# ==================================================

def create_database():

    # Make sure the database folder exists
    os.makedirs("database", exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    cursor = connection.cursor()

    # ==================================================
    # USERS TABLE
    # ==================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    """)

    # ==================================================
    # MEDICATIONS TABLE
    # ==================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            medication_name TEXT NOT NULL,
            dosage TEXT NOT NULL,
            instructions TEXT,
            start_date TEXT,
            end_date TEXT,
            time_to_take TEXT,
            frequency TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # ==================================================
    # PRESCRIPTIONS TABLE
    # ==================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prescriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            prescription_name TEXT NOT NULL,
            doctor_name TEXT,
            issue_date TEXT,
            expiry_date TEXT,
            refill_date TEXT,
            notes TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # ==================================================
    # DOSE RECORDS TABLE
    # ==================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dose_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            medication_id INTEGER NOT NULL,

            scheduled_date TEXT NOT NULL,

            scheduled_time TEXT NOT NULL,

            status TEXT NOT NULL DEFAULT 'Pending',

            taken_at TEXT,

            snoozed_until TEXT,

            FOREIGN KEY (user_id)
                REFERENCES users (id),

            FOREIGN KEY (medication_id)
                REFERENCES medications (id)
        )
    """)

    connection.commit()
    connection.close()

    print("Database created successfully!")
    print("All required tables are ready.")


# ==================================================
# RUN DATABASE CREATION
# ==================================================

if __name__ == "__main__":
    create_database()