// Renderer process script for Electron-Python communication testing

// Utility function to make API requests
async function apiRequest(method, endpoint, data = null) {
  try {
    const result = await window.electronAPI.apiRequest(method, endpoint, data);
    return result;
  } catch (error) {
    return {
      success: false,
      error: error.message
    };
  }
}

// Utility function to display results
function displayResult(elementId, result, isSuccess = null) {
  const element = document.getElementById(elementId);
  element.style.display = 'block';
  
  if (isSuccess === null) {
    isSuccess = result.success;
  }
  
  element.className = `result ${isSuccess ? 'success' : 'error'}`;
  element.textContent = JSON.stringify(result, null, 2);
}

// Test connection to Python backend
async function testConnection() {
  const result = await apiRequest('GET', '/api/health');
  displayResult('connectionResult', result);
  
  // Update connection status
  const statusElement = document.getElementById('connectionStatus');
  if (result.success) {
    statusElement.textContent = '已连接';
    statusElement.className = 'status connected';
  } else {
    statusElement.textContent = '未连接';
    statusElement.className = 'status disconnected';
  }
}

// Send a message to the backend
async function sendMessage() {
  const messageInput = document.getElementById('messageInput');
  const message = messageInput.value.trim();
  
  if (!message) {
    displayResult('messageResult', {
      success: false,
      error: '请输入消息内容'
    });
    return;
  }
  
  const result = await apiRequest('POST', '/api/message', {
    message: message,
    from: 'electron-frontend'
  });
  
  displayResult('messageResult', result);
  
  if (result.success) {
    messageInput.value = '';
  }
}

// Get all messages
async function getMessages() {
  const result = await apiRequest('GET', '/api/messages');
  displayResult('messageResult', result);
}

// Get current counter value
async function getCounter() {
  const result = await apiRequest('GET', '/api/counter');
  displayResult('counterResult', result);
}

// Increment counter
async function incrementCounter() {
  const incrementInput = document.getElementById('counterIncrement');
  const increment = parseInt(incrementInput.value) || 1;
  
  const result = await apiRequest('POST', '/api/counter', {
    increment: increment
  });
  
  displayResult('counterResult', result);
}

// Perform calculation
async function calculate() {
  const operation = document.getElementById('operation').value;
  const numberA = parseFloat(document.getElementById('numberA').value) || 0;
  const numberB = parseFloat(document.getElementById('numberB').value) || 0;
  
  const result = await apiRequest('POST', '/api/calculate', {
    operation: operation,
    a: numberA,
    b: numberB
  });
  
  displayResult('calculateResult', result);
}

// Add a user
async function addUser() {
  const userName = document.getElementById('userName').value.trim();
  const userEmail = document.getElementById('userEmail').value.trim();
  
  if (!userName) {
    displayResult('userResult', {
      success: false,
      error: '请输入用户名'
    });
    return;
  }
  
  const result = await apiRequest('POST', '/api/user', {
    name: userName,
    email: userEmail
  });
  
  displayResult('userResult', result);
  
  if (result.success) {
    document.getElementById('userName').value = '';
    document.getElementById('userEmail').value = '';
  }
}

// Get all users
async function getUsers() {
  const result = await apiRequest('GET', '/api/users');
  displayResult('userResult', result);
}

// Auto-test connection on page load
document.addEventListener('DOMContentLoaded', function() {
  // Test connection after a short delay to allow the app to fully load
  setTimeout(testConnection, 1000);
});
