#!/usr/bin/env python3
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