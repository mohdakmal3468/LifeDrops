import mysql.connector

try:
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="akmalmoak3468",
        database="lifedrops"
    )
    print("Connected!")
    conn.close()
except Exception as e:
    print("Error:", e)