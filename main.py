from fastapi import FastAPI, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import mysql.connector
import requests
from datetime import datetime, timedelta
import math
from typing import List, Dict
from fastapi import WebSocket, WebSocketDisconnect

app = FastAPI()

# Global chat manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, room_id: str):
        await websocket.accept()
        self.active_connections[room_id] = websocket

    def disconnect(self, room_id: str):
        if room_id in self.active_connections:
            del self.active_connections[room_id]

    async def send_message(self, room_id: str, message: str, sender: str):
        if room_id in self.active_connections:
            try:
                await self.active_connections[room_id].send_json({
                    "message": message,
                    "sender": sender,
                    "timestamp": datetime.now().isoformat()
                })
            except:
                self.disconnect(room_id)

manager = ConnectionManager()

@app.websocket("/ws/chat/{room_id}")
async def websocket_chat(websocket: WebSocket, room_id: str):
    await manager.connect(websocket, room_id)
    try:
        while True:
            data = await websocket.receive_json()
            await manager.send_message(room_id, data["message"], data["sender"])
    except WebSocketDisconnect:
        manager.disconnect(room_id)

# Create chat room endpoint
@app.post("/api/chat-room")
async def create_chat_room(donor_id: int = Form(...), request_id: str = Form(...)):
    room_id = f"chat-{donor_id}-{request_id}"
    return {"room_id": room_id, "ws_url": f"ws://localhost:8000/ws/chat/{room_id}"}


OLLAMA_URL = "http://localhost:11434/api/generate"

def rank_donors_ai(patient_lat: float, patient_lon: float, blood_group: str, donors):
    # Calculate REAL distances using Haversine formula
    results = []
    for d in donors:
        # Haversine distance (km)
        lat1, lon1 = math.radians(patient_lat), math.radians(patient_lon)
        lat2, lon2 = math.radians(d.get('latitude', 25.5941)), math.radians(d.get('longitude', 85.1376))
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        distance_km = 6371 * c  # Earth radius
        
        score = (100 / (distance_km + 1)) + (d.get('credits', 0) * 0.5) + (d.get('donations', 0) * 10)
        results.append({
            **d, 'distance_km': round(distance_km, 1), 'ai_score': round(score, 1)
        })
    
    # Sort by AI score (closer + higher credits = better)
    return sorted(results, key=lambda x: x['ai_score'], reverse=True)[:3]

app.mount("/static", StaticFiles(directory="static"), name="static")

def get_db():
    try:
        return mysql.connector.connect(host="localhost", user="root", password="akmalmoak3468", database="lifedrops")
    except:
        conn = mysql.connector.connect(host="localhost", user="root", password="akmalmoak3468")
        conn.cursor().execute("CREATE DATABASE IF NOT EXISTS lifedrops")
        conn.close()
        return mysql.connector.connect(host="localhost", user="root", password="akmalmoak3468", database="lifedrops")

@app.on_event("startup")
def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT PRIMARY KEY AUTO_INCREMENT, 
            name VARCHAR(100), role VARCHAR(20), blood_group VARCHAR(10), 
            location VARCHAR(100), password VARCHAR(100), 
            latitude DECIMAL(10,6), longitude DECIMAL(11,6)
        )
    """)
    
    # Alerts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INT PRIMARY KEY AUTO_INCREMENT, message TEXT, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 🔥 FIXED donor_wallet - UNIQUE user_id instead of PRIMARY KEY
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS donor_wallet (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT UNIQUE,
            credits INT DEFAULT 0,
            verified_donations INT DEFAULT 0,
            last_verified_donation TIMESTAMP NULL,
            next_eligible_date TIMESTAMP NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Verified donations log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS verified_donations (
            id INT PRIMARY KEY AUTO_INCREMENT,
            donor_id INT,
            patient_name VARCHAR(100),
            blood_group VARCHAR(10),
            donation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            verified_by VARCHAR(100),
            credits_awarded INT,
            FOREIGN KEY (donor_id) REFERENCES users(id)
        )
    """)
    
    conn.commit()
    conn.close()

# LANDING PAGES
@app.get("/") 
async def landing(): 
    return FileResponse("templates/landing.html")
@app.get("/app") 
async def notebook():
    return FileResponse("templates/notebook.html")
@app.get("/login") 
async def login_page(): 
    return FileResponse("templates/login.html")
@app.get("/register") 
async def register_page(): 
    return FileResponse("templates/register.html")
@app.get("/dashboard")
async def dashboard_page():
    return FileResponse("templates/dashboard.html")
@app.get("/donor") 
async def donor_page(): 
    return FileResponse("templates/donor.html")

@app.post("/api/login")
async def api_login(name: str = Form(...), password: str = Form(...)):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE name=%s AND password=%s LIMIT 1", (name, password))
        user = cursor.fetchone()
        conn.close()
        if user:
            role = user[2] if len(user) > 2 else "Patient"
            return {"success": True, "user": list(user), "role": role, "dashboard": "/donor" if role == "Donor" else "/dashboard"}
        return {"success": False, "error": "Invalid credentials"}
    except: return {"success": False, "error": "Server error"}

@app.post("/api/register")
async def api_register(name: str = Form(...), role: str = Form(...), blood_group: str = Form(...), location: str = Form("Patna"), password: str = Form(...), latitude: float = Form(25.5941), longitude: float = Form(85.1376)):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (name, role, blood_group, location, password, latitude, longitude) VALUES (%s,%s,%s,%s,%s,%s,%s)", (name, role, blood_group, location, password, latitude, longitude))
        user_id = cursor.lastrowid
        
        if role == "Donor":
            cursor.execute("INSERT IGNORE INTO donor_wallet (user_id) VALUES (%s)", (user_id,))
        
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception as e:
        if 'conn' in locals(): conn.rollback(); conn.close()
        return {"success": False, "error": "Registration failed"}

# 🔥 Get wallet info
@app.get("/api/wallet/{user_id}")
async def get_wallet(user_id: int):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT credits, verified_donations, last_verified_donation, next_eligible_date FROM donor_wallet WHERE user_id = %s", (user_id,))
        wallet = cursor.fetchone()
        conn.close()
        if wallet:
            eligible = not wallet[3] or datetime.now() > wallet[3]
            return {
                "credits": wallet[0] or 0,
                "donations": wallet[1] or 0,
                "last_donation": str(wallet[2]) if wallet[2] else None,
                "eligible_next": eligible
            }
        return {"credits": 0, "donations": 0, "eligible_next": True}
    except:
        return {"credits": 0, "donations": 0, "eligible_next": True}

# 🔥 VERIFY DONATION (Hospital/Admin)
@app.post("/api/verify-donation")
async def verify_donation(donor_id: int = Form(...), patient_name: str = Form(...), blood_group: str = Form(...), verified_by: str = Form(...), credits_awarded: int = Form(50)):
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Check eligibility (90-day cooldown)
        cursor.execute("SELECT next_eligible_date FROM donor_wallet WHERE user_id = %s", (donor_id,))
        eligibility = cursor.fetchone()
        if eligibility and eligibility[0] and datetime.now() < eligibility[0]:
            conn.close()
            return {"success": False, "error": "Donor not eligible. 90-day cooldown active."}
        
        # Get donor name
        cursor.execute("SELECT name FROM users WHERE id = %s", (donor_id,))
        donor_name = cursor.fetchone()
        if not donor_name:
            conn.close()
            return {"success": False, "error": "Donor not found"}
        
        # Award credits
        cursor.execute("""
            INSERT INTO donor_wallet (user_id, credits, verified_donations, last_verified_donation, next_eligible_date)
            VALUES (%s, %s, 1, CURRENT_TIMESTAMP, DATE_ADD(CURRENT_TIMESTAMP, INTERVAL 90 DAY))
            ON DUPLICATE KEY UPDATE
                credits = credits + %s,
                verified_donations = verified_donations + 1,
                last_verified_donation = CURRENT_TIMESTAMP,
                next_eligible_date = DATE_ADD(CURRENT_TIMESTAMP, INTERVAL 90 DAY)
        """, (donor_id, credits_awarded, credits_awarded))
        
        # Log donation
        cursor.execute("INSERT INTO verified_donations (donor_id, patient_name, blood_group, verified_by, credits_awarded) VALUES (%s,%s,%s,%s,%s)", (donor_id, patient_name, blood_group, verified_by, credits_awarded))
        
        conn.commit()
        conn.close()
        return {"success": True, "message": f"✅ {donor_name[0]} awarded {credits_awarded} credits!"}
    except Exception as e:
        if 'conn' in locals(): conn.rollback(); conn.close()
        return {"success": False, "error": "Verification failed"}

@app.post("/api/donors")
async def api_donors(blood: str = Form(...), location: str = Form(...), lat: float = Form(25.5941), lon: float = Form(85.1376)):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.name, u.location, u.blood_group, u.latitude, u.longitude, u.id, 
                   COALESCE(w.credits, 0), COALESCE(w.verified_donations, 0)
            FROM users u LEFT JOIN donor_wallet w ON u.id = w.user_id 
            WHERE u.role='Donor' AND u.blood_group=%s
        """, (blood,))
        donors = cursor.fetchall()
        conn.close()

        donors_list = [{
            "name": d[0], "location": d[1], "blood_group": d[2], 
            "latitude": d[3] or 25.5941, "longitude": d[4] or 85.1376,
            "id": d[5], "credits": d[6], "donations": d[7]
        } for d in donors]
        
        # AI uses REAL GPS coordinates!
        top_donors = rank_donors_ai(lat, lon, blood, donors_list)
        return {"donors": top_donors}
    except: 
        return {"donors": []}


@app.post("/api/alert") 
async def api_alert(message: str = Form(...)): 
    try:
         conn = get_db(); cursor = conn.cursor(); cursor.execute("INSERT INTO alerts (message) VALUES (%s)", (message,)); conn.commit(); conn.close(); return {"success": True}
    except:
        return {"success": False}

@app.get("/api/alerts") 
async def api_alerts():
    try: conn = get_db(); cursor = conn.cursor(); cursor.execute("SELECT id, message FROM alerts ORDER BY id DESC LIMIT 3"); alerts = [{"id": r[0], "msg": r[1]} for r in cursor.fetchall()]; conn.close(); return {"alerts": alerts}
    except: return {"alerts": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app,  host="127.0.0.1", port=8000)








