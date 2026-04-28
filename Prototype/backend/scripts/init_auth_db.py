#!/usr/bin/env python3
"""
Script to initialize the auth.db SQLite database for authentication.
"""
from pathlib import Path
from backend.app.auth import init_auth_db
from backend.app.settings import DATA_DIR

def main():
    db_path = DATA_DIR / "auth.db"
    print(f"[init_auth_db] Initializing {db_path} ...")
    init_auth_db(db_path)
    print("[init_auth_db] Done.")

if __name__ == "__main__":
    main()
