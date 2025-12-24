#!/usr/bin/env python3
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