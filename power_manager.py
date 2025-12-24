#!/usr/bin/env python3
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