import os
import sys

# === СОДЕРЖИМОЕ ФАЙЛОВ ===

files = {}

# 1. CONFIG.YAML
files['config.yaml'] = """
system:
  mode: "auto"
  threads:
    vision_enabled: true
    voice_enabled: true
    bluetooth_enabled: true

power:
  enabled: true
  i2c_bus: 1
  i2c_address: 0x42  # Стандарт для Waveshare UPS HAT C
  shutdown_voltage: 3.2
  warning_voltage: 3.5
  check_interval: 60

vision:
  camera_mode: "imx500"
  imx500:
    width: 640
    height: 480
    fps: 5
    # Проверьте этот путь на вашей OS (Bookworm)
    model_config: "/usr/share/rpi-camera-assets/imx500_mobilenet_ssd.json"
    confidence_threshold: 0.5

voice:
  speech_recognition:
    model_path: "./voice_models/vosk-model-small-ru-0.22"
    sample_rate: 16000
    trigger_words: ["ассистент", "помощник", "робот"]
    energy_threshold: 300
    
  text_to_speech:
    # Замените на имя скачанной модели
    model_path: "./voice_models/piper/ru_RU-irina-medium.onnx"
    config_path: "./voice_models/piper/ru_RU-irina-medium.onnx.json"
    speaker_id: 0
    
  commands:
    what_do_you_see: ["что видишь", "что там", "опиши", "посмотри"]
    stop_listening: ["стоп", "хватит", "отмена"]
    check_battery: ["заряд", "батарея", "питание"]
    
  responses:
    greeting: "Система готова."
    ready: "Слушаю."
    no_objects: "Ничего не вижу."
    processing: "Секунду..."
    error: "Ошибка системы."

bluetooth:
  headphones:
    mac_address: "XX:XX:XX:XX:XX:XX"  # <--- ВСТАВЬТЕ MAC АДРЕС ВАШИХ НАУШНИКОВ
    name: "Headphones"
    auto_connect: true
    reconnect_attempts: 5
  audio:
    profile: "headset_head_unit" # Важно для микрофона
    sample_rate: 16000
    channels: 1
"""

# 2. REQUIREMENTS.TXT
files['requirements.txt'] = """
PyYAML==6.0.1
vosk==0.3.45
piper-tts==1.2.0
pyaudio==0.2.13
SpeechRecognition==3.10.0
dbus-python==1.3.2
pulsectl==23.5.4
smbus2==0.4.2
gpiozero==2.0
psutil==5.9.6
# numpy и pillow ставим через apt (python3-numpy), здесь они для справки
"""

# 3. SETUP.SH
files['setup.sh'] = """#!/bin/bash
set -e

echo "=== УСТАНОВКА VISION VOICE (PI ZERO 2W) ==="

# 1. Обновление и системные пакеты
sudo apt update
sudo apt install -y python3-pip python3-venv python3-numpy python3-pil \
    python3-smbus python3-gpiozero i2c-tools \
    libcamera-apps pulseaudio pulseaudio-module-bluetooth \
    bluez python3-pyaudio libatlas-base-dev sox libsox-fmt-all

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
"""

# 4. VISION_ENGINE.PY
files['vision_engine.py'] = """#!/usr/bin/env python3
import time
import logging
import subprocess
import json
import os
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class VisionEngine:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.width = config['imx500']['width']
        self.height = config['imx500']['height']
        # Используем RAM диск для временного файла
        self.output_file = "/dev/shm/imx500_out.json"
        
        # Определение инструмента камеры (Bookworm использует rpicam-vid)
        self.cmd_tool = 'rpicam-vid' if os.path.exists('/usr/bin/rpicam-vid') else 'libcamera-vid'
        self.model_config = None

    def initialize(self) -> bool:
        logger.info(f"Инициализация Vision Engine ({self.cmd_tool})...")
        
        cfg_path = self.config['imx500']['model_config']
        if not os.path.exists(cfg_path):
            # Фолбэк на стандартный путь
            alt_path = "/usr/share/rpi-camera-assets/imx500_mobilenet_ssd.json"
            if os.path.exists(alt_path):
                self.model_config = alt_path
            else:
                logger.error("Не найден конфиг модели IMX500 (json)!")
                return False
        else:
            self.model_config = cfg_path
            
        logger.info(f"Используем конфиг модели: {self.model_config}")
        return True

    def process_frame(self) -> List[Dict[str, Any]]:
        if not self.model_config: return []
        
        try:
            if os.path.exists(self.output_file):
                os.remove(self.output_file)

            # Захват одного кадра с инференсом
            cmd = [
                self.cmd_tool,
                '-t', '1', # 1ms (мгновенно)
                '--width', str(self.width),
                '--height', str(self.height),
                '--post-process-file', self.model_config,
                '--output', self.output_file,
                '--nopreview',
                '--nclo'
            ]
            
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)

            detections = []
            if os.path.exists(self.output_file):
                with open(self.output_file, 'r') as f:
                    content = f.read()
                    if content:
                        try:
                            data = json.loads(content)
                            # Обработка разных форматов JSON от libcamera
                            raw = data.get('detections', []) if isinstance(data, dict) else data
                            
                            for d in raw:
                                conf = d.get('confidence', 0)
                                if conf >= self.config['imx500']['confidence_threshold']:
                                    label_raw = d.get('category', d.get('label', 0))
                                    label_rus = self._translate(label_raw)
                                    detections.append({'label': label_rus, 'conf': conf})
                        except json.JSONDecodeError:
                            pass
            return detections
        except Exception as e:
            logger.error(f"Vision error: {e}")
            return []

    def _translate(self, label):
        # Словарь COCO (упрощенный)
        d = {
            1: 'человек', 'person': 'человек',
            2: 'велосипед', 'bicycle': 'велосипед',
            3: 'машина', 'car': 'машина',
            44: 'бутылка', 'bottle': 'бутылка',
            62: 'стул', 'chair': 'стул',
            63: 'диван', 'couch': 'диван',
            64: 'горшок', 'potted plant': 'растение',
            67: 'стол', 'dining table': 'стол',
            72: 'телевизор', 'tv': 'телевизор',
            73: 'ноутбук', 'laptop': 'ноутбук',
            74: 'мышь', 'mouse': 'мышь',
            75: 'клавиатура', 'keyboard': 'клавиатура',
            76: 'телефон', 'cell phone': 'телефон'
        }
        return d.get(label, str(label))

    def format_detections_for_speech(self, detections):
        if not detections:
            return "Ничего не различаю."
        
        counts = {}
        for d in detections:
            l = d['label']
            counts[l] = counts.get(l, 0) + 1
            
        res = []
        for l, c in counts.items():
            res.append(f"{c} {l}")
        return "Вижу: " + ", ".join(res)

    def cleanup(self):
        pass
"""

# 5. VOICE_ASSISTANT.PY
files['voice_assistant.py'] = """#!/usr/bin/env python3
import time
import queue
import logging
import json
import threading
from pathlib import Path
import vosk
import pyaudio

logger = logging.getLogger(__name__)

class VoiceAssistant:
    def __init__(self, config, detection_queue, command_queue, audio_queue):
        self.config = config
        self.detection_queue = detection_queue
        self.command_queue = command_queue
        self.audio_queue = audio_queue
        
        self.vision_engine = None # Будет установлен из main.py
        self.running = False
        self.listening = False
        self.rec = None
        
    def initialize(self) -> bool:
        try:
            model_path = self.config['speech_recognition']['model_path']
            if not Path(model_path).exists():
                logger.error(f"Модель Vosk не найдена: {model_path}")
                return False
                
            logger.info("Загрузка модели Vosk...")
            model = vosk.Model(model_path)
            self.rec = vosk.KaldiRecognizer(model, 16000)
            self.running = True
            
            # Приветствие
            self.speak(self.config['responses']['greeting'])
            return True
        except Exception as e:
            logger.error(f"Ошибка Voice Init: {e}")
            return False

    def process(self):
        if not self.running: return
        
        # Слушаем микрофон
        try:
            p = pyaudio.PyAudio()
            # Индекс устройства по умолчанию (Bluetooth гарнитура должна быть default)
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=4000)
            stream.start_stream()
            
            self.listening = True
            logger.info("Ассистент слушает...")
            
            while self.running:
                data = stream.read(4000, exception_on_overflow=False)
                if self.rec.AcceptWaveform(data):
                    res = json.loads(self.rec.Result())
                    text = res.get('text', '')
                    if text:
                        logger.info(f"Распознано: {text}")
                        self._handle_command(text)
                        
            stream.stop_stream()
            stream.close()
            p.terminate()
            
        except Exception as e:
            logger.error(f"Ошибка аудиопотока: {e}")
            time.sleep(2)

    def _handle_command(self, text):
        triggers = self.config['speech_recognition']['trigger_words']
        cmds = self.config['commands']
        
        # Простая проверка на вхождение
        is_trigger = any(t in text for t in triggers)
        
        if is_trigger or True: # Для теста реагируем на всё или только на триггеры
            
            if any(c in text for c in cmds['what_do_you_see']):
                self.speak(self.config['responses']['processing'])
                self._action_vision()
                
            elif any(c in text for c in cmds['stop_listening']):
                self.speak("Останавливаюсь.")
                
            elif any(c in text for c in cmds['check_battery']):
                # Это событие можно отправить в main через очередь, но для простоты
                self.speak("Функция проверки заряда доступна в автоматическом режиме.")

    def _action_vision(self):
        if self.vision_engine:
            # Прямой вызов зрения
            dets = self.vision_engine.process_frame()
            text = self.vision_engine.format_detections_for_speech(dets)
            self.speak(text)
        else:
            self.speak("Модуль зрения не подключен.")

    def speak(self, text):
        if not text: return
        logger.info(f"Сказать: {text}")
        self.audio_queue.put({'type': 'tts', 'text': text})

    def cleanup(self):
        self.running = False
"""

# 6. AUDIO_PROCESSOR.PY
files['audio_processor.py'] = """#!/usr/bin/env python3
import time
import logging
import subprocess
import os
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

class AudioProcessor:
    def __init__(self, config):
        self.config = config
        self.running = False
        self.tts_model = None
        
    def initialize(self) -> bool:
        # Инициализация Piper TTS
        try:
            # Здесь мы не грузим модель в память python (piper имеет python bindings, но проще через CLI для Zero)
            # Или используем piper-tts библиотеку если установлена
            self.model_path = self.config['text_to_speech']['model_path']
            self.piper_bin = "piper" # Предполагаем что piper установлен в venv или систему
            
            # Проверка наличия файла модели
            if not os.path.exists(self.model_path):
                logger.warning("Модель TTS не найдена, звука не будет.")
                return False
                
            self.running = True
            return True
        except Exception as e:
            logger.error(f"TTS Init Error: {e}")
            return False

    def play_audio(self, item):
        if not self.running: return
        
        try:
            type_ = item.get('type')
            
            if type_ == 'tts':
                text = item.get('text')
                self._synthesize_and_play(text)
            elif type_ == 'file':
                path = item.get('path')
                self._play_wav(path)
                
        except Exception as e:
            logger.error(f"Ошибка воспроизведения: {e}")

    def _synthesize_and_play(self, text):
        # Используем Piper через subprocess для экономии памяти python
        # echo 'text' | piper --model ... --output_file - | aplay
        
        cmd_synth = [
            'piper',
            '--model', self.model_path,
            '--output-raw'
        ]
        
        # Для Pi Zero 2W + Bluetooth лучше писать в файл и играть aplay,
        # чем пайпить raw поток, так как aplay может захлебнуться.
        wav_file = "/tmp/tts_output.wav"
        
        try:
            # 1. Синтез
            with open(wav_file, 'w') as f:
                # Piper выводит raw audio (pcm), нужно сконвертировать или играть как raw
                # Проще сказать piper вывести wav
                cmd = f"echo '{text}' | piper --model {self.model_path} --output_file {wav_file}"
                os.system(cmd)
            
            # 2. Воспроизведение
            self._play_wav(wav_file)
            
        except Exception as e:
            logger.error(f"TTS Error: {e}")

    def _play_wav(self, path):
        if os.path.exists(path):
            # Используем paplay (PulseAudio Play) для Bluetooth совместимости
            subprocess.run(['paplay', path], timeout=10)

    def cleanup(self):
        self.running = False
"""

# 7. POWER_MANAGER.PY
files['power_manager.py'] = """#!/usr/bin/env python3
import time
import logging
import subprocess
from smbus2 import SMBus

logger = logging.getLogger(__name__)

class PowerManager:
    def __init__(self, config):
        self.config = config
        self.bus_id = config.get('i2c_bus', 1)
        self.address = config.get('i2c_address', 0x42)
        self.running = False
        self.last_warn = 0

    def initialize(self) -> bool:
        try:
            with SMBus(self.bus_id) as bus:
                # Калибровка INA219
                bus.write_word_data(self.address, 0x00, 0x399F)
            self.running = True
            logger.info("UPS Monitor (INA219) активен")
            return True
        except Exception as e:
            logger.error(f"UPS Monitor ошибка: {e}")
            return False

    def check_status(self, voice_assistant=None):
        if not self.running: return

        try:
            with SMBus(self.bus_id) as bus:
                val = bus.read_word_data(self.address, 0x02)
                val = ((val & 0xFF) << 8) | (val >> 8)
                voltage = (val >> 3) * 0.004
                
            if voltage < 0.1: return # Ошибка чтения
            
            # Выключение
            if voltage < self.config['shutdown_voltage']:
                logger.critical(f"Батарея {voltage:.2f}V. Выключение...")
                if voice_assistant:
                    voice_assistant.speak("Батарея разряжена. Выключаюсь.")
                    time.sleep(3)
                os.system("sudo shutdown -h now")
                
            # Предупреждение
            elif voltage < self.config['warning_voltage']:
                if time.time() - self.last_warn > 300: # раз в 5 мин
                    logger.warning(f"Низкий заряд: {voltage:.2f}V")
                    if voice_assistant:
                        voice_assistant.speak("Низкий заряд батареи.")
                    self.last_warn = time.time()
                    
        except Exception as e:
            logger.error(f"Ошибка чтения I2C: {e}")

    def cleanup(self):
        self.running = False
"""

# 8. BLUETOOTH_MANAGER.PY
files['bluetooth_manager.py'] = """#!/usr/bin/env python3
import time
import logging
import subprocess

logger = logging.getLogger(__name__)

class BluetoothManager:
    def __init__(self, config):
        self.config = config
        self.mac = config['headphones']['mac_address']
        
    def initialize(self):
        # Перезапуск bluetooth службы для надежности
        subprocess.run(['sudo', 'systemctl', 'restart', 'bluetooth'])
        time.sleep(2)
        
    def connect_headphones(self):
        logger.info(f"Подключение к {self.mac}...")
        try:
            # Trust
            subprocess.run(['bluetoothctl', 'trust', self.mac], timeout=5)
            # Connect
            res = subprocess.run(['bluetoothctl', 'connect', self.mac], capture_output=True, text=True, timeout=15)
            
            if "Connection successful" in res.stdout:
                logger.info("Bluetooth подключен.")
                time.sleep(2)
                self._set_profile()
                return True
            else:
                logger.warning(f"Ошибка подключения: {res.stdout}")
                return False
        except Exception as e:
            logger.error(f"BT Error: {e}")
            return False

    def _set_profile(self):
        # Принудительно ставим профиль гарнитуры (микрофон + звук)
        card = f"bluez_card.{self.mac.replace(':', '_')}"
        logger.info("Настройка аудио профиля...")
        
        # Пробуем Handsfree
        subprocess.run(['pactl', 'set-card-profile', card, 'handsfree_head_unit'])
        time.sleep(1)
        # Пробуем Headset (если handsfree не сработал)
        subprocess.run(['pactl', 'set-card-profile', card, 'headset_head_unit'])
        
        # Делаем дефолтным
        self._set_default('sinks')
        self._set_default('sources')

    def _set_default(self, type_):
        res = subprocess.run(['pactl', 'list', 'short', type_], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if 'bluez' in line:
                dev = line.split()[0]
                subprocess.run(['pactl', 'set-default-' + type_[:-1], dev])

    def is_connected(self):
        res = subprocess.run(['bluetoothctl', 'info', self.mac], capture_output=True, text=True)
        return "Connected: yes" in res.stdout

    def cleanup(self):
        pass
"""

# 9. MAIN.PY
files['main.py'] = """#!/usr/bin/env python3
import sys
import time
import threading
import queue
import logging
import yaml
from vision_engine import VisionEngine
from voice_assistant import VoiceAssistant
from bluetooth_manager import BluetoothManager
from power_manager import PowerManager
from audio_processor import AudioProcessor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler('system.log')]
)
logger = logging.getLogger("System")

class System:
    def __init__(self):
        with open("config.yaml") as f:
            self.config = yaml.safe_load(f)
            
        self.q_det = queue.Queue(maxsize=1)
        self.q_cmd = queue.Queue(maxsize=5)
        self.q_audio = queue.Queue(maxsize=5)
        
        self.power = PowerManager(self.config['power'])
        self.bt = BluetoothManager(self.config['bluetooth'])
        self.vision = VisionEngine(self.config['vision'])
        self.audio = AudioProcessor(self.config['voice']) # Config voice contains TTS info
        self.voice = VoiceAssistant(self.config['voice'], self.q_det, self.q_cmd, self.q_audio)
        
        self.running = True

    def run(self):
        # 1. Hardware Init
        self.power.initialize()
        
        # 2. Bluetooth (blocking)
        self.bt.initialize()
        if self.config['bluetooth']['headphones']['auto_connect']:
            self.bt.connect_headphones()
            
        # 3. Engines
        self.audio.initialize()
        self.vision.initialize()
        self.voice.initialize()
        
        # Связываем голос и зрение
        self.voice.vision_engine = self.vision
        
        # Запуск потоков
        t_voice = threading.Thread(target=self.voice.process, daemon=True)
        t_audio = threading.Thread(target=self._audio_loop, daemon=True)
        
        t_voice.start()
        t_audio.start()
        
        logger.info("Система запущена!")
        
        try:
            while self.running:
                # Главный цикл мониторинга
                self.power.check_status(self.voice)
                
                # Проверка BT
                if not self.bt.is_connected():
                    logger.warning("BT отключен. Реконнект...")
                    self.bt.connect_headphones()
                    
                time.sleep(5)
        except KeyboardInterrupt:
            self.cleanup()

    def _audio_loop(self):
        while self.running:
            try:
                item = self.q_audio.get(timeout=1)
                self.audio.play_audio(item)
            except queue.Empty:
                pass

    def cleanup(self):
        self.running = False
        self.voice.cleanup()
        self.vision.cleanup()
        self.audio.cleanup()
        self.power.cleanup()
        logger.info("Остановка.")

if __name__ == "__main__":
    System().run()
"""

# === ГЕНЕРАЦИЯ ФАЙЛОВ ===

def create_files():
    print("Генерация файлов проекта...")
    for filename, content in files.items():
        with open(filename, 'w') as f:
            f.write(content.strip())
        print(f"OK: {filename}")
        
    # Делаем скрипт установки исполняемым
    os.chmod("setup.sh", 0o755)
    print("\\nПроект создан!")
    print("Для установки запустите: ./setup.sh")

if __name__ == "__main__":
    create_files()
