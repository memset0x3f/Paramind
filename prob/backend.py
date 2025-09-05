#!/usr/bin/env python3
"""
Python Flask backend server for testing communication with Electron frontend
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import json
import time
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Sample data storage
data_store = {
    "messages": [],
    "counter": 0,
    "users": [],
    "online_users": []
}

@app.route('/')
def home():
    """Home endpoint"""
    return jsonify({
        "message": "Python Flask Backend is running!",
        "timestamp": datetime.now().isoformat(),
        "status": "success"
    })

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "backend": "Python Flask",
        "version": "1.0.0"
    })

@app.route('/api/message', methods=['POST'])
def send_message():
    """Send a message to the backend"""
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({"error": "Message is required"}), 400
        
        message = {
            "id": len(data_store["messages"]) + 1,
            "text": data["message"],
            "timestamp": datetime.now().isoformat(),
            "from": data.get("from", "electron-frontend")
        }
        
        data_store["messages"].append(message)
        
        return jsonify({
            "status": "success",
            "message": "Message received",
            "data": message
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/messages', methods=['GET'])
def get_messages():
    """Get all messages"""
    return jsonify({
        "status": "success",
        "messages": data_store["messages"],
        "count": len(data_store["messages"])
    })

@app.route('/api/counter', methods=['GET', 'POST'])
def counter():
    """Counter endpoint for testing state management"""
    if request.method == 'POST':
        data = request.get_json()
        increment = data.get('increment', 1) if data else 1
        data_store["counter"] += increment
        
        return jsonify({
            "status": "success",
            "counter": data_store["counter"],
            "increment": increment
        })
    else:
        return jsonify({
            "status": "success",
            "counter": data_store["counter"]
        })

@app.route('/api/calculate', methods=['POST'])
def calculate():
    """Simple calculation endpoint"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        operation = data.get('operation')
        a = data.get('a', 0)
        b = data.get('b', 0)
        
        if operation == 'add':
            result = a + b
        elif operation == 'subtract':
            result = a - b
        elif operation == 'multiply':
            result = a * b
        elif operation == 'divide':
            if b == 0:
                return jsonify({"error": "Division by zero"}), 400
            result = a / b
        else:
            return jsonify({"error": "Invalid operation"}), 400
        
        return jsonify({
            "status": "success",
            "operation": operation,
            "a": a,
            "b": b,
            "result": result
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/user', methods=['POST'])
def add_user():
    """Add a user"""
    try:
        data = request.get_json()
        if not data or 'name' not in data:
            return jsonify({"error": "Name is required"}), 400
        
        user = {
            "id": len(data_store["users"]) + 1,
            "name": data["name"],
            "email": data.get("email", ""),
            "created_at": datetime.now().isoformat()
        }
        
        data_store["users"].append(user)
        
        return jsonify({
            "status": "success",
            "message": "User added",
            "user": user
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/users', methods=['GET'])
def get_users():
    """Get all users"""
    return jsonify({
        "status": "success",
        "users": data_store["users"],
        "count": len(data_store["users"])
    })

@app.route('/api/chat/join', methods=['POST'])
def join_chat():
    """Join the chat room"""
    try:
        data = request.get_json()
        if not data or 'username' not in data:
            return jsonify({"error": "Username is required"}), 400
        
        username = data["username"].strip()
        if not username:
            return jsonify({"error": "Username cannot be empty"}), 400
        
        # Check if username is already taken
        if any(user["username"] == username for user in data_store["online_users"]):
            return jsonify({"error": "Username already taken"}), 400
        
        user = {
            "username": username,
            "joined_at": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat()
        }
        
        data_store["online_users"].append(user)
        
        # Add system message
        system_message = {
            "id": len(data_store["messages"]) + 1,
            "text": f"{username} 加入了聊天室",
            "timestamp": datetime.now().isoformat(),
            "from": "system",
            "type": "system"
        }
        data_store["messages"].append(system_message)
        
        return jsonify({
            "status": "success",
            "message": "Joined chat successfully",
            "user": user,
            "online_users": data_store["online_users"]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat/leave', methods=['POST'])
def leave_chat():
    """Leave the chat room"""
    try:
        data = request.get_json()
        if not data or 'username' not in data:
            return jsonify({"error": "Username is required"}), 400
        
        username = data["username"]
        
        # Remove user from online users
        data_store["online_users"] = [
            user for user in data_store["online_users"] 
            if user["username"] != username
        ]
        
        # Add system message
        system_message = {
            "id": len(data_store["messages"]) + 1,
            "text": f"{username} 离开了聊天室",
            "timestamp": datetime.now().isoformat(),
            "from": "system",
            "type": "system"
        }
        data_store["messages"].append(system_message)
        
        return jsonify({
            "status": "success",
            "message": "Left chat successfully",
            "online_users": data_store["online_users"]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat/online', methods=['GET'])
def get_online_users():
    """Get online users"""
    return jsonify({
        "status": "success",
        "online_users": data_store["online_users"],
        "count": len(data_store["online_users"])
    })

@app.route('/api/chat/send', methods=['POST'])
def send_chat_message():
    """Send a chat message"""
    try:
        data = request.get_json()
        if not data or 'message' not in data or 'username' not in data:
            return jsonify({"error": "Message and username are required"}), 400
        
        message_text = data["message"].strip()
        username = data["username"]
        
        if not message_text:
            return jsonify({"error": "Message cannot be empty"}), 400
        
        # Check if user is online
        if not any(user["username"] == username for user in data_store["online_users"]):
            return jsonify({"error": "User not online"}), 400
        
        message = {
            "id": len(data_store["messages"]) + 1,
            "text": message_text,
            "timestamp": datetime.now().isoformat(),
            "from": username,
            "type": "chat"
        }
        
        data_store["messages"].append(message)
        
        return jsonify({
            "status": "success",
            "message": "Message sent",
            "data": message
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat/messages', methods=['GET'])
def get_chat_messages():
    """Get chat messages"""
    limit = request.args.get('limit', 50, type=int)
    messages = data_store["messages"][-limit:] if limit > 0 else data_store["messages"]
    
    return jsonify({
        "status": "success",
        "messages": messages,
        "count": len(messages),
        "total": len(data_store["messages"])
    })

if __name__ == '__main__':
    print("Starting Python Flask Backend Server...")
    print("Server will be available at: http://localhost:5001")
    print("API endpoints:")
    print("  GET  / - Home")
    print("  GET  /api/health - Health check")
    print("  POST /api/message - Send message")
    print("  GET  /api/messages - Get messages")
    print("  GET/POST /api/counter - Counter operations")
    print("  POST /api/calculate - Simple calculations")
    print("  POST /api/user - Add user")
    print("  GET  /api/users - Get users")
    print("  POST /api/chat/join - Join chat room")
    print("  POST /api/chat/leave - Leave chat room")
    print("  GET  /api/chat/online - Get online users")
    print("  POST /api/chat/send - Send chat message")
    print("  GET  /api/chat/messages - Get chat messages")
    
    app.run(host='0.0.0.0', port=5001, debug=False)
