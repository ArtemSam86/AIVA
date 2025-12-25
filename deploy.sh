#!/bin/bash
set -e

echo "╔════════════════════════════════════════════════╗"
echo "║  VisionVoice: Pi Zero 2W + IMX500 + UPS HAT  ║"
echo "╚════════════════════════════════════════════════╝"
echo ""

# Проверка архитектуры
ARCH=$(uname -m)
echo "✓ Архитектура: $ARCH"

# Проверка RAM
TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
echo "✓ RAM: ${TOTAL_RAM}MB"

if [ "$TOTAL_RAM" -lt 400 ]; then
    echo "❌ Недостаточно RAM"
    exit 1
fi

# SWAP конфигурация
echo "💾 Настройка SWAP (1.5GB)..."
sudo dphys-swapfile swapoff || true
sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1536/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
echo "✓ SWAP настроен"

# Обновление системы
echo "📦 Обновление пакетов..."
sudo apt update
sudo apt upgrade -y

# Установка зависимостей
echo "📦 Установка зависимостей..."
sudo apt install -y \
    build-essential \
    pkg-config \
    libssl-dev \
    libudev-dev \
    python3 \
    python3-pip \
    python3-picamera2 \
    python3-smbus2 \
    i2c-tools \
    libasound2-dev \
    alsa-utils \
    git \
    wget

# Python зависимости
echo "🐍 Установка Python библиотек..."
pip3 install --no-cache-dir smbus2

# Включение I2C
echo "🔧 Настройка I2C..."
if ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
    echo "dtparam=i2c_arm=on" | sudo tee -a /boot/firmware/config.txt
elif ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null; then
    echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt
fi

# Проверка I2C
if i2cdetect -y 1 | grep -q "42"; then
    echo "✓ UPS HAT C обнаружен на адресе 0x42"
else
    echo "⚠️  UPS HAT C не обнаружен. Проверьте подключение."
fi

# Установка Rust
if ! command -v rustc &> /dev/null; then
    echo "🦀 Установка Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
    source "$HOME/.cargo/env"
else
    echo "✓ Rust установлен"
fi

# Установка Piper TTS
echo "🔊 Установка Piper TTS..."
if ! command -v piper &> /dev/null; then
    PIPER_VERSION="2023.11.14-2"
    
    if [ "$ARCH" == "armv7l" ]; then
        PIPER_ARCH="armv7l"
    else
        PIPER_ARCH="arm64"
    fi
    
    wget "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_${PIPER_ARCH}.tar.gz" -O /tmp/piper.tar.gz
    sudo tar -xzf /tmp/piper.tar.gz -C /usr/local/
    sudo ln -sf /usr/local/piper/piper /usr/local/bin/piper
    rm /tmp/piper.tar.gz
fi

# Создание структуры
echo "📁 Создание директорий..."
mkdir -p logs scripts voice_models/piper

# Скачивание легковесной TTS модели
if [ ! -f "voice_models/piper/ru_RU-dmitri-low.onnx" ]; then
    echo "📥 Скачивание модели TTS..."
    wget "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/low/ru_RU-dmitri-low.onnx" \
        -O voice_models/piper/ru_RU-dmitri-low.onnx
    wget "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/low/ru_RU-dmitri-low.onnx.json" \
        -O voice_models/piper/ru_RU-dmitri-low.onnx.json
fi

# Настройка камеры
echo "📷 Настройка камеры..."
if ! grep -q "camera_auto_detect=1" /boot/firmware/config.txt 2>/dev/null && \
   ! grep -q "camera_auto_detect=1" /boot/config.txt 2>/dev/null; then
    echo "camera_auto_detect=1" | sudo tee -a /boot/firmware/config.txt || \
    echo "camera_auto_detect=1" | sudo tee -a /boot/config.txt
fi

# GPU память
if ! grep -q "gpu_mem=" /boot/firmware/config.txt 2>/dev/null && \
   ! grep -q "gpu_mem=" /boot/config.txt 2>/dev/null; then
    echo "gpu_mem=128" | sudo tee -a /boot/firmware/config.txt || \
    echo "gpu_mem=128" | sudo tee -a /boot/config.txt
fi

# Права пользователя
echo "👤 Настройка прав..."
sudo usermod -a -G video,i2c,gpio,audio $USER

# Сборка проекта
echo "🔨 Сборка Rust проекта..."
echo "⏰ Это займет 30-60 минут на Pi Zero 2W..."
export CARGO_BUILD_JOBS=1
cargo build --release

# Systemd сервис
echo "⚙️  Создание systemd сервиса..."
sudo tee /etc/systemd/system/visionvoice.service > /dev/null << EOF
[Unit]
Description=VisionVoice AI Camera Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/target/release/vision_voice_zero_imx500
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

# Ограничение ресурсов
MemoryMax=400M
CPUQuota=90%

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

# Тестовый скрипт для UPS
chmod +x scripts/ups_monitor.py

echo ""
echo "╔════════════════════════════════════════════════╗"
echo "║           ✅ Установка завершена!             ║"
echo "╚════════════════════════════════════════════════╝"
echo ""
echo "📝 Следующие шаги:"
echo ""
echo "1. ОБЯЗАТЕЛЬНО перезагрузитесь:"
echo "   sudo reboot"
echo ""
echo "2. После перезагрузки проверьте:"
echo "   - Камеру: libcamera-hello --list-cameras"
echo "   - I2C: i2cdetect -y 1"
echo "   - UPS: python3 scripts/ups_monitor.py"
echo ""
echo "3. Запуск сервиса:"
echo "   sudo systemctl start visionvoice"
echo "   sudo systemctl enable visionvoice"
echo ""
echo "4. Мониторинг:"
echo "   sudo systemctl status visionvoice"
echo "   journalctl -u visionvoice -f"
echo ""
echo "⚠️  ВАЖНО:"
echo "   - Используйте качественное питание 5V/3A"
echo "   - Подключите активное охлаждение"
echo "   - Температура: vcgencmd measure_temp"
echo ""