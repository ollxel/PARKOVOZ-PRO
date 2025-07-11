class ParkingMonitor {
    constructor() {
        this.canvas = document.getElementById('parkingCanvas');
        this.ctx = this.canvas.getContext('2d');
        this.grid = [];
        this.gridSize = 40;
        this.currentTool = 'parking-spot';
        this.isConstructorMode = false;
        this.parkingSpots = [];
        this.socket = null;
        this.isConnected = false;
        this.carData = [];
        this.lastUpdateTime = null;
        this.pingInterval = null;
        this.reconnectTimer = null;
        this.reconnectAttempts = 0;
        this.lastPongTime = 0;
        
        this.initializeCanvas();
        this.setupEventListeners();
        this.loadConfig();
        this.drawGrid();
    }

    initializeCanvas() {
        const rect = this.canvas.getBoundingClientRect();
        this.canvas.width = rect.width;
        this.canvas.height = rect.height;
        this.cols = Math.floor(this.canvas.width / this.gridSize);
        this.rows = Math.floor(this.canvas.height / this.gridSize);
        
        this.grid = Array(this.rows).fill().map(() => Array(this.cols).fill(null));
        
        for (let row = 1; row < this.rows - 1; row++) {
            for (let col = 1; col < this.cols - 1; col++) {
                if (row % 2 === 1 && col % 3 === 1) {
                    this.grid[row][col] = 'parking-spot';
                }
            }
        }
        
        for (let row = 0; row < this.rows; row++) {
            this.grid[row][0] = 'road';
            this.grid[row][this.cols - 1] = 'road';
        }
        for (let col = 0; col < this.cols; col++) {
            this.grid[0][col] = 'road';
            this.grid[this.rows - 1][col] = 'road';
        }
        
        this.grid[0][Math.floor(this.cols / 2)] = 'entrance';
    }

    setupEventListeners() {
        document.getElementById('constructorMode').addEventListener('click', () => {
            this.switchMode(true);
        });

        document.getElementById('monitoringMode').addEventListener('click', () => {
            this.switchMode(false);
        });

        document.querySelectorAll('.tool-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.currentTool = btn.dataset.type;
            });
        });

        this.canvas.addEventListener('click', (e) => {
            if (this.isConstructorMode) {
                const rect = this.canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                
                const col = Math.floor(x / this.gridSize);
                const row = Math.floor(y / this.gridSize);
                
                if (col >= 0 && col < this.cols && row >= 0 && row < this.rows) {
                    this.grid[row][col] = this.currentTool;
                    this.redraw();
                }
            }
        });

        document.getElementById('clearBtn').addEventListener('click', () => {
            this.grid = Array(this.rows).fill().map(() => Array(this.cols).fill(null));
            this.redraw();
        });

        document.getElementById('saveBtn').addEventListener('click', () => {
            const layout = {
                grid: this.grid,
                cols: this.cols,
                rows: this.rows,
                gridSize: this.gridSize
            };
            
            const dataStr = JSON.stringify(layout, null, 2);
            const dataBlob = new Blob([dataStr], {type: 'application/json'});
            const url = URL.createObjectURL(dataBlob);
            
            const link = document.createElement('a');
            link.href = url;
            link.download = 'parking_layout.json';
            link.click();
            
            URL.revokeObjectURL(url);
        });

        document.getElementById('loadBtn').addEventListener('click', () => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.json';
            
            input.onchange = e => {
                const file = e.target.files[0];
                if (!file) return;
                
                const reader = new FileReader();
                reader.onload = event => {
                    try {
                        const layout = JSON.parse(event.target.result);
                        
                        if (!layout.grid || !layout.cols || !layout.rows) {
                            throw new Error('Неверный формат файла');
                        }
                        
                        this.grid = layout.grid;
                        this.cols = layout.cols;
                        this.rows = layout.rows;
                        this.gridSize = layout.gridSize || 40;
                        
                        this.canvas.width = this.cols * this.gridSize;
                        this.canvas.height = this.rows * this.gridSize;
                        
                        this.redraw();
                    } catch (error) {
                        alert('Ошибка загрузки: ' + error.message);
                    }
                };
                reader.readAsText(file);
            };
            
            input.click();
        });

        document.getElementById('connectBtn').addEventListener('click', () => {
            this.connectToStream();
        });

        document.getElementById('disconnectBtn').addEventListener('click', () => {
            this.disconnectFromStream();
        });

        document.getElementById('streamUrl').addEventListener('change', () => {
            this.saveConfig();
        });
        
        document.getElementById('apiUrl').addEventListener('change', () => {
            this.saveConfig();
        });
    }

    switchMode(isConstructor) {
        this.isConstructorMode = isConstructor;
        
        if (isConstructor) {
            document.getElementById('constructorMode').classList.add('active');
            document.getElementById('monitoringMode').classList.remove('active');
            document.getElementById('constructorToolbar').style.display = 'block';
            document.getElementById('monitoringPanel').style.display = 'none';
            this.canvas.style.cursor = 'crosshair';
            this.stopMonitoring();
        } else {
            document.getElementById('monitoringMode').classList.add('active');
            document.getElementById('constructorMode').classList.remove('active');
            document.getElementById('constructorToolbar').style.display = 'none';
            document.getElementById('monitoringPanel').style.display = 'block';
            this.canvas.style.cursor = 'default';
        }
        
        this.redraw();
    }

    drawGrid() {
        this.ctx.strokeStyle = '#e0e0e0';
        this.ctx.lineWidth = 1;
        
        for (let i = 0; i <= this.cols; i++) {
            this.ctx.beginPath();
            this.ctx.moveTo(i * this.gridSize, 0);
            this.ctx.lineTo(i * this.gridSize, this.canvas.height);
            this.ctx.stroke();
        }
        
        for (let i = 0; i <= this.rows; i++) {
            this.ctx.beginPath();
            this.ctx.moveTo(0, i * this.gridSize);
            this.ctx.lineTo(this.canvas.width, i * this.gridSize);
            this.ctx.stroke();
        }
    }

    drawElement(row, col, type, occupied = false) {
        const x = col * this.gridSize;
        const y = row * this.gridSize;
        
        switch (type) {
            case 'parking-spot':
                this.ctx.fillStyle = occupied ? '#ffcdd2' : '#c8e6c9';
                this.ctx.fillRect(x + 2, y + 2, this.gridSize - 4, this.gridSize - 4);
                this.ctx.strokeStyle = occupied ? '#e53935' : '#43a047';
                this.ctx.lineWidth = 2;
                this.ctx.strokeRect(x + 2, y + 2, this.gridSize - 4, this.gridSize - 4);
                
                if (occupied) {
                    this.drawCar(x + this.gridSize/2, y + this.gridSize/2);
                } else {
                    const spotIndex = this.parkingSpots.findIndex(s => s.row === row && s.col === col);
                    if (spotIndex !== -1) {
                        this.ctx.fillStyle = '#2e7d32';
                        this.ctx.font = '12px Noto Sans';
                        this.ctx.textAlign = 'center';
                        this.ctx.fillText((spotIndex + 1).toString(), x + this.gridSize/2, y + this.gridSize/2 + 4);
                    }
                }
                break;
                
            case 'road':
                this.ctx.fillStyle = '#bdbdbd';
                this.ctx.fillRect(x + 2, y + 2, this.gridSize - 4, this.gridSize - 4);
                break;
                
            case 'wall':
                this.ctx.fillStyle = '#757575';
                this.ctx.fillRect(x + 2, y + 2, this.gridSize - 4, this.gridSize - 4);
                break;
                
            case 'entrance':
                this.ctx.fillStyle = '#66bb6a';
                this.ctx.fillRect(x + 2, y + 2, this.gridSize - 4, this.gridSize - 4);
                this.ctx.fillStyle = 'white';
                this.ctx.font = '16px Noto Sans';
                this.ctx.textAlign = 'center';
                this.ctx.fillText('↕', x + this.gridSize/2, y + this.gridSize/2 + 5);
                break;
        }
    }

    drawCar(x, y) {
        const carWidth = 22;
        const carHeight = 12;
        
        this.ctx.fillStyle = '#5c6bc0';
        this.ctx.fillRect(x - carWidth/2, y - carHeight/2, carWidth, carHeight);
        
        this.ctx.strokeStyle = '#3949ab';
        this.ctx.lineWidth = 1;
        this.ctx.strokeRect(x - carWidth/2, y - carHeight/2, carWidth, carHeight);
        
        this.ctx.fillStyle = '#bbdefb';
        this.ctx.fillRect(x - carWidth/2 + 2, y - carHeight/2 + 2, carWidth - 4, 3);
        this.ctx.fillRect(x - carWidth/2 + 2, y + carHeight/2 - 5, carWidth - 4, 3);
        
        this.ctx.fillStyle = '#212121';
        this.ctx.fillRect(x - carWidth/2 + 1, y - carHeight/2 - 1, 3, 2);
        this.ctx.fillRect(x + carWidth/2 - 4, y - carHeight/2 - 1, 3, 2);
        this.ctx.fillRect(x - carWidth/2 + 1, y + carHeight/2 - 1, 3, 2);
        this.ctx.fillRect(x + carWidth/2 - 4, y + carHeight/2 - 1, 3, 2);
    }

    redraw() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.drawGrid();
        
        this.parkingSpots = [];
        let freeCount = 0;
        let occupiedCount = 0;
        
        for (let row = 0; row < this.rows; row++) {
            for (let col = 0; col < this.cols; col++) {
                if (this.grid[row][col]) {
                    let occupied = false;
                    
                    if (this.grid[row][col] === 'parking-spot') {
                        this.parkingSpots.push({row, col});
                        
                        const spotIndex = this.parkingSpots.length - 1;
                        occupied = this.isSpotOccupied(spotIndex);
                        
                        if (occupied) occupiedCount++;
                        else freeCount++;
                    }
                    
                    this.drawElement(row, col, this.grid[row][col], occupied);
                }
            }
        }
        
        document.getElementById('freeCount').textContent = freeCount;
        document.getElementById('occupiedCount').textContent = occupiedCount;
        document.getElementById('lastUpdate').textContent = this.lastUpdateTime || '-';
    }

    isSpotOccupied(spotIndex) {
        if (!this.carData || this.carData.length === 0) return false;
        return spotIndex < this.carData.length && this.carData[spotIndex] === 1;
    }

    connectToStream() {
        const apiUrl = document.getElementById('apiUrl').value;
        
        if (!apiUrl) {
            this.updateStatus('Введите адрес WebSocket сервера!', 'error');
            return;
        }
        
        this.updateStatus('Подключение к серверу...', 'info');
        this.updateConnectionStatus(false, 'Подключение...');
        
        this.stopMonitoring();
        this.reconnectAttempts = 0;
        
        try {
            this.socket = new WebSocket(apiUrl);
            
            this.socket.onopen = () => {
                console.log('WebSocket соединение установлено');
                this.updateStatus('Соединение установлено', 'success');
                this.updateConnectionStatus(true, 'Подключено');
                this.reconnectAttempts = 0;
                this.lastPongTime = Date.now();
                
                // Запрос данных о парковке
                this.socket.send(JSON.stringify({ type: "request_data" }));
                
                // Запускаем проверку соединения
                this.startConnectionCheck();
            };
            
            this.socket.onmessage = (event) => {
                try {
                    // Обработка pong-сообщения
                    if (event.data === 'pong') {
                        this.lastPongTime = Date.now();
                        return;
                    }
                    
                    // Обработка JSON-сообщений
                    const data = JSON.parse(event.data);
                    
                    if (data.type === "parking_data") {
                        this.carData = Array.isArray(data.data) ? data.data : [];
                        this.lastUpdateTime = new Date().toLocaleTimeString();
                        
                        document.getElementById('freeCount').textContent = data.free ?? 0;
                        document.getElementById('occupiedCount').textContent = data.occupied ?? 0;
                        document.getElementById('lastUpdate').textContent = this.lastUpdateTime;
                        
                        this.redraw();
                    }
                } catch (e) {
                    console.error('Ошибка обработки данных:', e);
                }
            };
            
            this.socket.onerror = (error) => {
                console.error('WebSocket ошибка:', error);
                this.updateStatus('Ошибка соединения', 'error');
                this.updateConnectionStatus(false, 'Ошибка');
                this.reconnect();
            };
            
            this.socket.onclose = (event) => {
                console.log('WebSocket закрыт:', event.code, event.reason);
                this.stopConnectionCheck();
                
                if (event.code === 1011) {
                    this.updateStatus('Ошибка на сервере', 'error');
                } else {
                    this.updateStatus(`Соединение закрыто (${event.code})`, 'info');
                }
                
                this.updateConnectionStatus(false, 'Не подключено');
                this.reconnect();
            };
        } catch (e) {
            console.error('Ошибка создания WebSocket:', e);
            this.updateStatus('Ошибка подключения', 'error');
            this.reconnect();
        }
    }
    
    startConnectionCheck() {
        // Останавливаем предыдущий интервал, если был
        this.stopConnectionCheck();
        
        // Отправляем ping каждые 10 секунд
        this.pingInterval = setInterval(() => {
            if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                this.socket.send('ping');
                
                // Проверяем последний pong
                if (Date.now() - this.lastPongTime > 20000) {
                    console.warn('Pong не получен, переподключаемся');
                    this.reconnect();
                }
            }
        }, 10000);
    }
    
    stopConnectionCheck() {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    reconnect() {
        if (this.reconnectAttempts >= 5) {
            this.updateStatus('Превышено количество попыток подключения', 'error');
            return;
        }

        this.stopConnectionCheck();
        
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
        }
        
        this.reconnectAttempts++;
        this.updateStatus(`Попытка переподключения (${this.reconnectAttempts}/5)`, 'info');
        
        this.reconnectTimer = setTimeout(() => {
            this.connectToStream();
        }, 3000);
    }

    stopMonitoring() {
        if (this.socket) {
            this.socket.close();
            this.socket = null;
        }
        this.stopConnectionCheck();
        
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        
        this.updateConnectionStatus(false, 'Не подключено');
    }

    disconnectFromStream() {
        this.stopMonitoring();
        this.updateStatus('Мониторинг остановлен', 'info');
    }

    updateStatus(message, type = 'info') {
        const statusMsg = document.getElementById('statusMsg');
        statusMsg.textContent = message;
        statusMsg.className = `status ${type}`;
    }

    updateConnectionStatus(isConnected, message = '') {
        const statusIndicator = document.getElementById('statusIndicator');
        const connectionStatus = document.getElementById('connectionStatus');
        
        this.isConnected = isConnected;
        
        if (isConnected) {
            statusIndicator.className = 'status-indicator connected';
            connectionStatus.textContent = message || 'Подключено';
        } else {
            statusIndicator.className = 'status-indicator error';
            connectionStatus.textContent = message || 'Не подключено';
        }
    }

    saveConfig() {
        const config = {
            streamUrl: document.getElementById('streamUrl').value,
            apiUrl: document.getElementById('apiUrl').value
        };
        localStorage.setItem('parkingMonitorConfig', JSON.stringify(config));
    }

    loadConfig() {
        const config = JSON.parse(localStorage.getItem('parkingMonitorConfig')) || {};
        if (config.streamUrl) {
            document.getElementById('streamUrl').value = config.streamUrl;
        }
        if (config.apiUrl) {
            document.getElementById('apiUrl').value = config.apiUrl;
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const monitor = new ParkingMonitor();
    monitor.switchMode(false);
});