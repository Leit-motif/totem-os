#!/usr/bin/env python3
"""
Test script to verify Cloud SQL PostgreSQL connection and pgvector extension.
"""

import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def test_connection():
    """Test database connection and pgvector extension."""
    
    # Connection parameters (you'll need to fill these in)
    host = "YOUR_PUBLIC_IP_HERE"  # Replace with your Cloud SQL public IP
    port = 5432
    database = "totem"
    user = "postgres"
    password = "YOUR_PASSWORD_HERE"  # Replace with your password
    
    try:
        print("🔌 Connecting to Cloud SQL PostgreSQL...")
        
        # Connect to the database
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password
        )
        
        print("✅ Connected successfully!")
        
        # Test pgvector extension
        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        print(f"📊 PostgreSQL version: {version}")
        
        # Check if pgvector is installed
        cursor.execute("SELECT * FROM pg_extension WHERE extname = 'vector';")
        vector_ext = cursor.fetchone()
        
        if vector_ext:
            print("✅ pgvector extension is installed!")
            
            # Test vector operations
            cursor.execute("CREATE TABLE IF NOT EXISTS test_vectors (id serial PRIMARY KEY, embedding vector(3));")
            cursor.execute("INSERT INTO test_vectors (embedding) VALUES ('[1,2,3]'), ('[4,5,6]');")
            cursor.execute("SELECT id, embedding FROM test_vectors;")
            results = cursor.fetchall()
            print(f"✅ Vector operations working! Test data: {results}")
            
            # Clean up test table
            cursor.execute("DROP TABLE test_vectors;")
            print("🧹 Cleaned up test table")
            
        else:
            print("❌ pgvector extension not found!")
            print("Run: CREATE EXTENSION IF NOT EXISTS vector;")
        
        cursor.close()
        conn.close()
        print("✅ Connection test completed successfully!")
        
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        print("\n🔧 Troubleshooting tips:")
        print("1. Check if Cloud SQL instance is running")
        print("2. Verify the public IP address")
        print("3. Check if authorized networks include your IP")
        print("4. Verify username/password")
        print("5. Make sure pgvector extension is installed")

if __name__ == "__main__":
    test_connection()
