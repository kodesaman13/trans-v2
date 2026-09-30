import mysql.connector

def get_connection():
    return mysql.connector.connect(
        host="13.71.18.147",
        user="fatima",
        password="fatima135",
        port=3307,
        database="pdf2excel",
        ssl_disabled=True
    )