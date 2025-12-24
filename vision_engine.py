#!/usr/bin/env python3
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