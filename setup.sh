#!/bin/bash
set -e

echo "=== УСТАНОВКА VISION VOICE (PI ZERO 2W) ==="

# 1. Обновление и системные пакеты
sudo apt update
sudo apt install -y python3-pip python3-venv python3-numpy python3-pil     python3-smbus python3-gpiozero i2c-tools     libcamera-apps pulseaudio pulseaudio-module-bluetooth     bluez python3-pyaudio libatlas-base-dev sox libsox-fmt-all

# 2. Настройка прав пользователя
sudo usermod -a -G video,audio,gpio,i2c,bluetooth $USER

# 3. Настройка Config.txt для IMX500 и памяти
CONFIG="/boot/firmware/config.txt"
if [ ! -f "$CONFIG" ]; then CONFIG="/boot/config.txt"; fi

echo "Настройка $CONFIG..."
if ! grep -q "dtparam=i2c_arm=on" "$CONFIG"; then echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG"; fi
# Увеличиваем память GPU для камеры
sudo sed -i 's/gpu_mem=.*/gpu_mem=128/' "$CONFIG" || echo "gpu_mem=128" | sudo tee -a "$CONFIG"

# 4. Создание venv
if [ ! -d "venv" ]; then
    echo "Создание venv..."
    python3 -m venv venv --system-site-packages
fi
source venv/bin/activate

# 5. Установка Python зависимостей
echo "Установка библиотек..."
pip install -r requirements.txt

# 6. Подготовка папок
mkdir -p voice_models logs vision_models/piper

# Скачивание Vosk (если нет)
if [ ! -d "voice_models/vosk-model-small-ru-0.22" ]; then
    echo "Скачивание модели распознавания речи (Vosk)..."
    wget -q https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip
    unzip -q vosk-model-small-ru-0.22.zip -d voice_models/
    rm vosk-model-small-ru-0.22.zip
fi

# Настройка PulseAudio
if ! grep -q "load-module module-switch-on-connect" /etc/pulse/default.pa; then
    echo "load-module module-switch-on-connect" | sudo tee -a /etc/pulse/default.pa
fi

echo ""
echo "=== ГОТОВО ==="
echo "1. Отредактируйте config.yaml (MAC адрес)"
echo "2. Скачайте модель Piper (ru_RU-irina-medium.onnx + .json) в voice_models/piper/"
echo "3. Перезагрузите: sudo reboot"