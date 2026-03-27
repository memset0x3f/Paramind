#!/usr/bin/env python3
"""
Network configuration utility for the chat application
"""

import socket
import subprocess
import platform
import json
import os

def get_local_ip():
    """Get the local IP address of this machine"""
    try:
        # Connect to a remote server to get local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except:
        return "127.0.0.1"

def get_available_port(start_port=5001):
    """Find an available port starting from start_port"""
    for port in range(start_port, start_port + 100):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('', port))
            s.close()
            return port
        except:
            continue
    return None

def get_network_info():
    """Get comprehensive network information"""
    system = platform.system()
    local_ip = get_local_ip()
    
    info = {
        "local_ip": local_ip,
        "system": system,
        "available_ports": []
    }
    
    # Find available ports
    for port in range(5001, 5010):
        if get_available_port(port) == port:
            info["available_ports"].append(port)
    
    return info

def create_network_config():
    """Create network configuration file"""
    config = {
        "server_host": "0.0.0.0",
        "server_port": get_available_port(5001) or 5001,
        "local_ip": get_local_ip(),
        "auto_discovery": True,
        "max_connections": 50
    }
    
    with open('network_config.json', 'w') as f:
        json.dump(config, f, indent=2)
    
    return config

if __name__ == "__main__":
    print("Network Configuration Utility")
    print("=" * 40)
    
    info = get_network_info()
    print(f"Local IP: {info['local_ip']}")
    print(f"System: {info['system']}")
    print(f"Available ports: {info['available_ports']}")
    
    config = create_network_config()
    print(f"\nConfiguration saved to network_config.json")
    print(f"Server will run on: {config['local_ip']}:{config['server_port']}")
