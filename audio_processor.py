#!/usr/bin/env python3
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