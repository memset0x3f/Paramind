const WebSocket = require('ws');
const http = require('http');

class P2PSignalingServer {
    constructor(port = 8080) {
        this.port = port;
        this.clients = new Map(); // uuid -> ClientInfo
        this.groups = new Map(); // groupId -> Set of uuids
        
        this.server = http.createServer();
        this.wss = new WebSocket.Server({ server: this.server });
        
        this.setupWebSocket();
        this.start();
    }

    setupWebSocket() {
        this.wss.on('connection', (ws, req) => {
            console.log(`New client connected from ${req.socket.remoteAddress}`);
            
            // 设置消息处理器
            ws.on('message', (data) => {
                try {
                    const message = JSON.parse(data.toString());
                    this.handleMessage(ws, message, req);
                } catch (error) {
                    console.error('Error parsing message:', error);
                    this.sendError(ws, 'Invalid JSON format');
                }
            });

            // 处理连接关闭
            ws.on('close', () => {
                this.handleDisconnect(ws);
            });

            // 处理错误
            ws.on('error', (error) => {
                console.error('WebSocket error:', error);
                this.handleDisconnect(ws);
            });
        });
    }

    handleMessage(ws, message, req) {
        const { type } = message;

        if (!type) {
            this.sendError(ws, 'Missing message type');
            return;
        }

        switch (type) {
            case 'register':
                this.handleRegister(ws, message, req);
                break;
            case 'punch_request':
                this.handlePunchRequest(ws, message);
                break;
            case 'heartbeat':
                this.handleHeartbeat(ws, message);
                break;
            default:
                this.sendError(ws, `Unknown message type: ${type}`);
        }
    }

    handleRegister(ws, message, req) {
        const { uuid, groupId, publicIp, publicPort } = message;

        if (!uuid || !groupId || !publicIp || !publicPort) {
            this.sendError(ws, 'Missing required fields: uuid, groupId, publicIp, publicPort');
            return;
        }

        if (!this.isValidUUID(uuid)) {
            this.sendError(ws, 'Invalid UUID format');
            return;
        }

        if (!this.clients.has(uuid)) {
          const clientInfo = {
              uuid,
              groupId,
              ws,
              publicIp,
              publicPort,
              internalIp: this.extractPublicIp(req.socket.remoteAddress),
              internalPort: req.socket.remotePort,
              lastSeen: Date.now()
          };
          
          this.clients.set(uuid, clientInfo);
        }

        if (!this.groups.has(groupId)) {
            this.groups.set(groupId, new Set());
        }
        this.groups.get(groupId).add(uuid);

        console.log(`Client registered - UUID: ${uuid}, Group: ${groupId}`);
        console.log(`Public address: ${publicIp}:${publicPort}`);
        console.log(`Internal address: ${req.socket.remoteAddress}:${req.socket.remotePort}`);
        
        this.sendAllPeersToClient(uuid);
        
        this.notifyNewClient(groupId, uuid);
        
        this.sendSuccess(ws, 'Registration successful');
    }

    sendAllPeersToClient(clientUuid) {
        const clientInfo = this.clients.get(clientUuid);
        if (!clientInfo) return;

        const { groupId } = clientInfo;
        const groupClients = this.groups.get(groupId);
        
        if (!groupClients || groupClients.size <= 1) {
            return;
        }

        const peers = [];
        groupClients.forEach(uuid => {
            if (uuid !== clientUuid) {
                const peer = this.clients.get(uuid);
                if (peer) {
                    peers.push({
                        uuid: peer.uuid,
                        public_address: {
                            ip: peer.publicIp,
                            port: peer.publicPort
                        },
                        internal_address: {
                            ip: peer.internalIp,
                            port: peer.internalPort
                        }
                    });
                }
            }
        });

        const message = {
            type: 'all_peers',
            group_id: groupId,
            peers: peers
        };

        this.sendJson(clientInfo.ws, message);
        console.log(`Sent ${peers.length} peers to client ${clientUuid}`);
    }

    notifyNewClient(groupId, newClientUuid) {
        const groupClients = this.groups.get(groupId);
        if (!groupClients) return;

        const newClient = this.clients.get(newClientUuid);
        if (!newClient) return;
        
        groupClients.forEach(uuid => {
            if (uuid !== newClientUuid) {
                const client = this.clients.get(uuid);
                if (client) {
                    const message = {
                        type: 'new_peer',
                        peer: {
                            uuid: newClient.uuid,
                            public_address: {
                                ip: newClient.publicIp,
                                port: newClient.publicPort
                            },
                            internal_address: {
                                ip: newClient.internalIp,
                                port: newClient.internalPort
                            }
                        },
                        group_id: groupId
                    };
                    this.sendJson(client.ws, message);
                }
            }
        });
    }

    handlePunchRequest(ws, message) {
        const { uuid, target_uuids } = message;

        if (!uuid || !target_uuids || !Array.isArray(target_uuids)) {
            this.sendError(ws, 'Missing required fields: uuid, target_uuids (array)');
            return;
        }

        const requester = this.clients.get(uuid);
        if (!requester) {
            this.sendError(ws, 'Client not registered');
            return;
        }

        console.log(`Punch request from ${uuid} for targets: ${target_uuids.join(', ')}`);

        target_uuids.forEach(targetUuid => {
            this.notifyPunchRequest(requester, targetUuid);
        });

        this.sendSuccess(ws, `Punch requests sent to ${target_uuids.length} clients`);
    }

    notifyPunchRequest(requester, targetUuid) {
        const targetClient = this.clients.get(targetUuid);
        if (!targetClient) {
            console.log(`Target client ${targetUuid} not found`);
            return;
        }

        const message = {
            type: 'punch_notification',
            requester: {
                uuid: requester.uuid,
                public_address: {
                    ip: requester.publicIp,
                    port: requester.publicPort
                },
                internal_address: {
                    ip: requester.internalIp,
                    port: requester.internalPort
                }
            },
            group_id: requester.groupId
        };

        this.sendJson(targetClient.ws, message);
        console.log(`Notified ${targetUuid} about punch request from ${requester.uuid}`);
    }

    handleHeartbeat(ws, message) {
        const { uuid } = message;

        if (!uuid) {
            this.sendError(ws, 'Missing UUID');
            return;
        }

        if (this.clients.has(uuid)) {
            this.updateLastSeen(uuid);
            this.sendSuccess(ws, 'Heartbeat acknowledged');
        } else {
            this.sendError(ws, 'Client not registered');
        }
    }

    handleDisconnect(ws) {
        let disconnectedUuid = null;
        for (const [uuid, client] of this.clients.entries()) {
            if (client.ws === ws) {
                disconnectedUuid = uuid;
                break;
            }
        }

        if (disconnectedUuid) {
            const clientInfo = this.clients.get(disconnectedUuid);
            
            if (clientInfo) {
                const { groupId } = clientInfo;
                
                if (this.groups.has(groupId)) {
                    this.groups.get(groupId).delete(disconnectedUuid);
                    
                    if (this.groups.get(groupId).size === 0) {
                        this.groups.delete(groupId);
                    } else {
                        this.notifyPeerDisconnected(groupId, disconnectedUuid);
                    }
                }
                
                console.log(`Client disconnected: ${disconnectedUuid} from group ${groupId}`);
            }
            
            this.clients.delete(disconnectedUuid);
        }
    }

    notifyPeerDisconnected(groupId, disconnectedUuid) {
        const groupClients = this.groups.get(groupId);
        if (!groupClients) return;

        groupClients.forEach(uuid => {
            const client = this.clients.get(uuid);
            if (client) {
                const message = {
                    type: 'peer_disconnected',
                    peer_uuid: disconnectedUuid,
                    group_id: groupId
                };
                this.sendJson(client.ws, message);
            }
        });
    }

    updateLastSeen(uuid) {
        const clientInfo = this.clients.get(uuid);
        if (clientInfo) {
            clientInfo.lastSeen = Date.now();
        }
    }

    extractPublicIp(address) {
        if (address.startsWith('::ffff:')) {
            return address.substring(7);
        }
        return address;
    }

    isValidUUID(uuid) {
        const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
        return uuidRegex.test(uuid);
    }

    sendSuccess(ws, message) {
        this.sendJson(ws, {
            type: 'success',
            message: message
        });
    }

    sendError(ws, message) {
        this.sendJson(ws, {
            type: 'error',
            message: message
        });
    }

    sendJson(ws, data) {
        if (ws.readyState === WebSocket.OPEN) {
            try {
                ws.send(JSON.stringify(data));
            } catch (error) {
                console.error('Error sending message:', error);
            }
        }
    }

    start() {
        this.server.listen(this.port, () => {
            console.log(`P2P Signaling Server running on port ${this.port}`);
            console.log(`Clients will receive all peers on connect and can request punches`);
        });

        setInterval(() => this.cleanupClients(), 60000);
    }

    cleanupClients() {
        const now = Date.now();
        const timeout = 5 * 60 * 1000;

        for (const [uuid, info] of this.clients.entries()) {
            if (now - info.lastSeen > timeout) {
                console.log(`Cleaning up expired client: ${uuid} from group ${info.groupId}`);
                this.handleDisconnect(info.ws);
            }
        }
    }
}

const server = new P2PSignalingServer(process.env.PORT || 8080);