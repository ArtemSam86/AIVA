#!/usr/bin/env python3
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