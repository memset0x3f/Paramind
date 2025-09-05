// Chat room functionality for Electron-Python communication

let currentUsername = '';
let isConnected = false;
let messagePollingInterval = null;
let serverHost = 'localhost';
let serverPort = 5001;
let isHostMode = true;

// Utility function to make API requests
async function apiRequest(method, endpoint, data = null) {
  try {
    const result = await window.electronAPI.apiRequest(method, endpoint, data, serverHost, serverPort);
    return result;
  } catch (error) {
    return {
      success: false,
      error: error.message
    };
  }
}

// Join chat room
async function joinChat() {
  const usernameInput = document.getElementById('usernameInput');
  const loginError = document.getElementById('loginError');
  const username = usernameInput.value.trim();
  
  if (!username) {
    showLoginError('请输入用户名');
    return;
  }
  
  if (username.length < 2) {
    showLoginError('用户名至少需要2个字符');
    return;
  }
  
  // Get connection mode
  const mode = document.querySelector('input[name="mode"]:checked').value;
  isHostMode = mode === 'host';
  
  if (!isHostMode) {
    // Client mode - get server details
    const serverIPInput = document.getElementById('serverIPInput');
    const serverPortInput = document.getElementById('serverPortInput');
    
    const serverIP = serverIPInput.value.trim();
    const port = parseInt(serverPortInput.value) || 5001;
    
    if (!serverIP) {
      showLoginError('请输入服务器IP地址');
      return;
    }
    
    serverHost = serverIP;
    serverPort = port;
  }
  
  // Try to join chat
  const result = await apiRequest('POST', '/api/chat/join', { username });
  
  if (result.success) {
    currentUsername = username;
    isConnected = true;
    
    // Hide login overlay and show chat interface
    document.getElementById('loginOverlay').classList.add('hidden');
    document.getElementById('chatInterface').classList.remove('hidden');
    
    // Update UI
    document.getElementById('currentUsername').textContent = username;
    document.getElementById('connectionStatus').textContent = isHostMode ? '服务器模式' : `连接到 ${serverHost}:${serverPort}`;
    document.getElementById('messageInput').disabled = false;
    document.getElementById('sendButton').disabled = false;
    
    // Load existing messages and start polling
    await loadMessages();
    await updateOnlineUsers();
    startMessagePolling();
    
    // Focus on message input
    document.getElementById('messageInput').focus();
  } else {
    showLoginError(result.error || '加入聊天室失败');
  }
}

// Show login error
function showLoginError(message) {
  const loginError = document.getElementById('loginError');
  loginError.textContent = message;
  loginError.classList.remove('hidden');
}

// Send message
async function sendMessage() {
  const messageInput = document.getElementById('messageInput');
  const message = messageInput.value.trim();
  
  if (!message || !isConnected) {
    return;
  }
  
  const result = await apiRequest('POST', '/api/chat/send', {
    message: message,
    username: currentUsername
  });
  
  if (result.success) {
    messageInput.value = '';
    // Message will be added to chat via polling
  } else {
    console.error('Failed to send message:', result.error);
  }
}

// Load messages
async function loadMessages() {
  const result = await apiRequest('GET', '/api/chat/messages?limit=50');
  
  if (result.success) {
    const chatMessages = document.getElementById('chatMessages');
    chatMessages.innerHTML = '';
    
    result.data.messages.forEach(message => {
      addMessageToChat(message);
    });
    
    scrollToBottom();
  }
}

// Add message to chat display
function addMessageToChat(message) {
  const chatMessages = document.getElementById('chatMessages');
  const messageDiv = document.createElement('div');
  
  let messageClass = 'message ';
  if (message.type === 'system') {
    messageClass += 'system';
  } else if (message.from === currentUsername) {
    messageClass += 'own';
  } else {
    messageClass += 'other';
  }
  
  messageDiv.className = messageClass;
  
  const time = new Date(message.timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit'
  });
  
  if (message.type === 'system') {
    messageDiv.innerHTML = `
      <div class="message-text">${message.text}</div>
    `;
  } else {
    messageDiv.innerHTML = `
      <div class="message-header">${message.from} ${time}</div>
      <div class="message-text">${message.text}</div>
    `;
  }
  
  chatMessages.appendChild(messageDiv);
}

// Update online users list
async function updateOnlineUsers() {
  const result = await apiRequest('GET', '/api/chat/online');
  
  if (result.success) {
    const onlineUsersList = document.getElementById('onlineUsersList');
    const onlineCount = document.getElementById('onlineCount');
    
    onlineUsersList.innerHTML = '';
    onlineCount.textContent = result.data.online_users.length;
    
    result.data.online_users.forEach(user => {
      const userDiv = document.createElement('div');
      userDiv.className = 'user-item';
      userDiv.textContent = user.username;
      onlineUsersList.appendChild(userDiv);
    });
  }
}

// Start polling for new messages
function startMessagePolling() {
  if (messagePollingInterval) {
    clearInterval(messagePollingInterval);
  }
  
  messagePollingInterval = setInterval(async () => {
    if (isConnected) {
      await loadMessages();
      await updateOnlineUsers();
    }
  }, 1000); // Poll every second
}

// Stop polling
function stopMessagePolling() {
  if (messagePollingInterval) {
    clearInterval(messagePollingInterval);
    messagePollingInterval = null;
  }
}

// Scroll to bottom of chat
function scrollToBottom() {
  const chatMessages = document.getElementById('chatMessages');
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

// Handle Enter key in message input
document.addEventListener('DOMContentLoaded', function() {
  const messageInput = document.getElementById('messageInput');
  const usernameInput = document.getElementById('usernameInput');
  const serverIPInput = document.getElementById('serverIPInput');
  
  messageInput.addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
      sendMessage();
    }
  });
  
  usernameInput.addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
      joinChat();
    }
  });
  
  serverIPInput.addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
      joinChat();
    }
  });
  
  // Handle mode change
  const modeInputs = document.querySelectorAll('input[name="mode"]');
  modeInputs.forEach(input => {
    input.addEventListener('change', handleModeChange);
  });
  
  // Load network information
  loadNetworkInfo();
  
  // Focus on username input
  usernameInput.focus();
});

// Load network information
async function loadNetworkInfo() {
  try {
    const result = await window.electronAPI.getNetworkInfo();
    if (result.success) {
      const localIPs = result.data.localIPs;
      const primaryIP = localIPs.length > 0 ? localIPs[0] : '127.0.0.1';
      
      document.getElementById('localIP').textContent = primaryIP;
      document.getElementById('serverPort').textContent = result.data.serverPort;
      
      // Show all IPs in a tooltip or additional info
      if (localIPs.length > 1) {
        document.getElementById('localIP').title = `所有IP: ${localIPs.join(', ')}`;
      }
    }
  } catch (error) {
    console.error('Failed to load network info:', error);
  }
}

// Handle connection mode change
function handleModeChange() {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  const clientConnection = document.getElementById('clientConnection');
  
  if (mode === 'client') {
    clientConnection.classList.remove('hidden');
  } else {
    clientConnection.classList.add('hidden');
  }
}

// Handle window close - leave chat
window.addEventListener('beforeunload', async function() {
  if (isConnected && currentUsername) {
    await apiRequest('POST', '/api/chat/leave', { username: currentUsername });
  }
  stopMessagePolling();
});

// Handle page visibility change
document.addEventListener('visibilitychange', function() {
  if (document.hidden) {
    stopMessagePolling();
  } else if (isConnected) {
    startMessagePolling();
  }
});
