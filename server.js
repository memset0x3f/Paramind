import { WebSocketServer } from 'ws';
import { createServer } from 'http';

const pairs = new Map(); // key: clientId, value: pairedClientId
const clients = new Map(); // key: clientId, value: { ws, pairedWith }

const server = createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ 
    status: 'ok', 
    clients: clients.size,
    pairs: pairs.size / 2 
  }));
});

const wss = new WebSocketServer({ server });

wss.on('connection', (ws) => {
  const clientId = generateId();
  console.log('客户端连接:', clientId);

  clients.set(clientId, { ws, pairedWith: null });

  send(ws, { type: 'welcome', clientId });

  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data);
      handleMessage(clientId, msg);
    } catch (error) {
      console.log('消息解析错误:', error);
    }
  });

  ws.on('close', () => {
    console.log('客户端断开:', clientId);
    cleanupClient(clientId);
  });

  tryPairClient(clientId);
});

function handleMessage(clientId, msg) {
  const client = clients.get(clientId);
  if (!client) return;

  switch (msg.type) {
    case 'pair':
      tryPairClient(clientId);
      break;

    case 'punch':
      if (client.pairedWith && clients.has(client.pairedWith)) {
        const targetWs = clients.get(client.pairedWith).ws;
        send(targetWs, {
          type: 'punch',
          from: clientId,
          address: msg.address // { ip, port }
        });
      }
      break;

    case 'message':
      if (client.pairedWith && clients.has(client.pairedWith)) {
        const targetWs = clients.get(client.pairedWith).ws;
        send(targetWs, {
          type: 'message',
          from: clientId,
          text: msg.text
        });
      }
      break;
  }
}

function tryPairClient(clientId) {
  if (clients.get(clientId).pairedWith) return;

  for (const [otherId, otherClient] of clients.entries()) {
    if (otherId !== clientId && !otherClient.pairedWith) {
      pairClients(clientId, otherId);
      return;
    }
  }

  send(clients.get(clientId).ws, { 
    type: 'waiting', 
    message: 'waiting for a partner to pair...' 
  });
}

function pairClients(clientA, clientB) {
  const clientAObj = clients.get(clientA);
  const clientBObj = clients.get(clientB);

  clientAObj.pairedWith = clientB;
  clientBObj.pairedWith = clientA;

  pairs.set(clientA, clientB);
  pairs.set(clientB, clientA);

  send(clientAObj.ws, {
    type: 'paired',
    partner: clientB,
    message: 'Paired successfully! You can start punching now.'
  });

  send(clientBObj.ws, {
    type: 'paired',
    partner: clientA,
    message: 'Paired successfully! You can start punching now.'
  });

  console.log('Paired:', clientA, '<=>', clientB);
}

function cleanupClient(clientId) {
  const client = clients.get(clientId);
  if (!client) return;

  if (client.pairedWith && clients.has(client.pairedWith)) {
    const partnerWs = clients.get(client.pairedWith).ws;
    send(partnerWs, {
      type: 'partner_left',
      message: 'Partner has disconnected.'
    });
    
    clients.get(client.pairedWith).pairedWith = null;
    pairs.delete(client.pairedWith);
  }

  pairs.delete(clientId);
  clients.delete(clientId);
  console.log('Deleting client:', clientId);
}

function send(ws, data) {
  if (ws.readyState === 1) { // OPEN
    ws.send(JSON.stringify(data));
  }
}

function generateId() {
  return Math.random().toString(36).substring(2, 10);
}

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`🚀 Signal Server Running on ${PORT}`);
  console.log(`📡 WebSocket: ws://localhost:${PORT}`);
  console.log(`🌐 HTTP: http://localhost:${PORT}`);
});