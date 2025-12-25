#!/usr/bin/env python3
import sys
import argparse
import logging
import subprocess
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/tts_worker.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

class TtsWorker:
    def __init__(self, model_path: str, sample_rate: int = 16000):
        self.model_path = Path(model_path)
        self.sample_rate = sample_rate
        
        if not self.model_path.exists():
            raise FileNotFoundError(f"Модель TTS не найдена: {model_path}")
            
    def speak(self, text: str) -> bool:
        #Синтез речи через Piper (оптимизировано)
        try:
            logger.info(f"Синтез речи: {text}")
            
            piper_process = subprocess.Popen(
                [
                    "piper",
                    "--model", str(self.model_path),
                    "--output_raw",
                    "--length_scale", "1.1",
                    "--noise_scale", "0.667",
                    "--noise_w", "0.8"
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            audio_data, piper_err = piper_process.communicate(
                input=text.encode('utf-8'),
                timeout=15
            )
            
            if piper_process.returncode != 0:
                logger.error(f"Ошибка Piper: {piper_err.decode()}")
                return False
            
            # Воспроизведение с минимальной задержкой
            aplay_process = subprocess.Popen(
                [
                    "aplay",
                    "-r", str(self.sample_rate),
                    "-f", "S16_LE",
                    "-t", "raw",
                    "-q",
                    "--buffer-size", "512"
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            aplay_out, aplay_err = aplay_process.communicate(
                input=audio_data,
                timeout=20
            )
            
            if aplay_process.returncode != 0:
                logger.error(f"Ошибка aplay: {aplay_err.decode()}")
                return False
                
            logger.info("✓ Синтез завершен")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Таймаут при синтезе речи")
            return False
        except Exception as e:
            logger.error(f"Ошибка синтеза речи: {e}", exc_info=True)
            return False

def main():
    parser = argparse.ArgumentParser(description='TTS Worker')
    parser.add_argument('--model', required=True, help='Путь к модели Piper')
    parser.add_argument('--sample-rate', type=int, default=16000, help='Частота дискретизации')
    parser.add_argument('--text', required=True, help='Текст для озвучивания')
    args = parser.parse_args()
    
    try:
        worker = TtsWorker(args.model, args.sample_rate)
        success = worker.speak(args.text)
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()