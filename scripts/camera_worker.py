#!/usr/bin/env python3
import sys
import json
import argparse
import signal
import logging
import gc
from typing import List, Dict, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/camera_worker.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

sys.stdout.reconfigure(line_buffering=True)

# COCO labels для IMX500 MobileNet SSD
COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table",
    "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
]

class CameraWorker:
    def __init__(self, config_path: str = "config.toml"):
        self.running = True
        self.picam2 = None
        self.frame_count = 0
        
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
    def signal_handler(self, signum, frame):
        logger.info(f"Получен сигнал {signum}, завершение работы...")
        self.running = False
        
    def initialize_camera(self):
        #Инициализация камеры с IMX500
        try:
            from picamera2 import Picamera2
            from picamera2.devices import IMX500
            from picamera2.devices.imx500 import NetworkIntrinsics
            
            logger.info("Инициализация IMX500...")
            
            # Проверка наличия IMX500
            imx500 = IMX500()
            
            # Загрузка модели MobileNet SSD
            intrinsics = imx500.network_intrinsics or NetworkIntrinsics()
            intrinsics.task = "object detection"
            
            self.picam2 = Picamera2(imx500.camera_num)
            
            # Конфигурация с поддержкой IMX500
            config = self.picam2.create_preview_configuration(
                main={"size": (640, 480), "format": "RGB888"},
                lores={"size": (320, 240)},
                buffer_count=2  # Минимум для Pi Zero
            )
            
            self.picam2.configure(config)
            
            # Запуск камеры
            self.picam2.start()
            
            # Прогрев
            import time
            time.sleep(2)
            
            logger.info("✓ IMX500 инициализирована")
            logger.info(f"✓ Модель: MobileNet SSD COCO")
            
        except ImportError as e:
            logger.error("IMX500 не поддерживается. Убедитесь, что установлена последняя версия picamera2")
            logger.error("Установите: sudo apt install python3-picamera2")
            raise
        except Exception as e:
            logger.error(f"Ошибка инициализации камеры: {e}")
            raise
            
    def detect_objects(self) -> List[Dict[str, Any]]:
        #Выполнение детекции объектов через IMX500
        try:
            # Захват кадра с метаданными
            metadata = self.picam2.capture_metadata()
            
            detections = []
            
            # Парсинг результатов IMX500
            if "Detection" in metadata:
                raw_detections = metadata["Detection"]
                
                for det in raw_detections:
                    # Формат IMX500: [class_id, confidence, x, y, width, height]
                    if len(det) >= 6:
                        class_id = int(det[0])
                        confidence = float(det[1])
                        x = float(det[2])
                        y = float(det[3])
                        width = float(det[4])
                        height = float(det[5])
                        
                        # Получаем метку класса
                        label = COCO_LABELS[class_id] if class_id < len(COCO_LABELS) else f"class_{class_id}"
                        
                        detections.append({
                            "label": label,
                            "confidence": confidence,
                            "bbox": {
                                "x": x,
                                "y": y,
                                "width": width,
                                "height": height
                            }
                        })
            
            # Периодическая очистка памяти
            self.frame_count += 1
            if self.frame_count % 15 == 0:
                gc.collect()
            
            return detections
            
        except Exception as e:
            logger.error(f"Ошибка детекции: {e}")
            return []
            
    def run(self):
        #Основной цикл обработки команд
        try:
            self.initialize_camera()
            
            logger.info("Камера готова к работе. Ожидание команд...")
            
            for line in sys.stdin:
                if not self.running:
                    break
                    
                command = line.strip()
                
                if command == "detect":
                    detections = self.detect_objects()
                    print(json.dumps(detections), flush=True)
                    
                elif command == "exit":
                    logger.info("Получена команда выхода")
                    self.running = False
                    break
                    
                else:
                    logger.warning(f"Неизвестная команда: {command}")
                    print(json.dumps({"error": "unknown_command"}), flush=True)
                    
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}", exc_info=True)
            print(json.dumps({"error": str(e)}), flush=True)
            
        finally:
            self.shutdown()
            
    def shutdown(self):
        #Корректное завершение работы
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
                logger.info("✓ Камера остановлена")
            except Exception as e:
                logger.error(f"Ошибка при остановке камеры: {e}")
        
        gc.collect()

def main():
    parser = argparse.ArgumentParser(description='Camera Worker для IMX500')
    parser.add_argument('--config', default='config.toml', help='Путь к файлу конфигурации')
    args = parser.parse_args()
    
    worker = CameraWorker(args.config)
    worker.run()

if __name__ == "__main__":
    main()