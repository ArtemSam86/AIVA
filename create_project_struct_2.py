import os

files = {
    # ==================== КОНФИГУРАЦИЯ ====================
    "config.toml": """
[system]
name = "AIVA Zero2W + IMX500"
version = "3.5"
log_level = "info"
# Single-threaded для экономии RAM
max_threads = 1

[camera]
# IMX500 аппаратное ускорение
width = 640
height = 480
model = "imx500_mobilenet_ssd"
# Доступные модели: mobilenet_ssd, efficientdet_lite0
detection_threshold = 0.55
inference_timeout = 5
# Пропуск кадров для экономии (IMX500 все равно быстрый)
frame_skip = 1
# Низкое разрешение для превью
preview_size = [320, 240]

[tts]
# Легковесная модель
model_path = "voice_models/piper/ru_RU-dmitri-low.onnx"
sample_rate = 16000
buffer_size = 512
max_phrase_length = 80
# Приоритетные сообщения (не прерывать)
priority_phrases = ["батарея", "выключение", "критично"]

[bluetooth]
enabled = false  # Экономия ресурсов
device_name = "aiva"

[power]
# Waveshare UPS HAT C (INA219)
enabled = true
i2c_bus = 1
i2c_address = 0x42
# Напряжения для 2x18650 (7.4V номинал)
shutdown_voltage = 6.4       # Критично низкое
warning_voltage = 6.8        # Предупреждение
full_voltage = 8.4           # Полный заряд
check_interval = 30          # Секунды
# Действия при низком заряде
auto_shutdown = true
warning_repeat_interval = 300  # 5 минут

[detection]
scan_interval = 15
cooldown_period = 8
enabled_classes = ["person", "car", "dog", "cat", "bird"]
max_detections = 3
# Уведомления о специфичных объектах
announce_person = true
announce_vehicle = true

[optimization]
# Агрессивная оптимизация для 512MB
use_swap = true
force_gc_interval = 20  # Каждые 20 циклов
cache_enabled = false   # Отключаем кэш для экономии RAM
low_power_mode = false  # Включать при <20% батареи
""",

    # ==================== RUST DEPENDENCIES ====================
    "Cargo.toml": """
[package]
name = "vision_voice_zero_imx500"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1.35", features = ["rt", "sync", "time", "process", "io-util", "signal"], default-features = false }
serde = { version = "1.0", features = ["derive"], default-features = false }
toml = { version = "0.8", default-features = false }
log = "0.4"
env_logger = { version = "0.11", default-features = false }
anyhow = "1.0"
serde_json = { version = "1.0", default-features = false }
# I2C для UPS HAT
i2cdev = "0.6"
byteorder = "1.5"

[profile.release]
opt-level = "z"
lto = true
strip = true
codegen-units = 1
panic = "abort"

[profile.dev]
opt-level = 1
""",

    # ==================== RUST MAIN ====================
    "src/main.rs": """
mod config;
mod camera_controller;
mod tts_controller;
mod power_monitor;

use anyhow::Result;
use log::{info, warn, error};
use tokio::signal;
use std::sync::Arc;
use tokio::sync::RwLock;

use config::Config;
use camera_controller::CameraController;
use tts_controller::TtsController;
use power_monitor::PowerMonitor;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or("info")
    ).init();

    print_banner();

    // Загрузка конфигурации
    let config = Config::load("config.toml")?;
    info!("✓ Конфигурация загружена");

    // Проверка системы
    check_system();

    // Создание контроллеров
    let camera = Arc::new(RwLock::new(
        CameraController::new(config.camera.clone()).await?
    ));
    let tts = Arc::new(TtsController::new(config.tts.clone())?);
    
    // Инициализация мониторинга питания
    let power_monitor = if config.power.enabled {
        Some(Arc::new(RwLock::new(
            PowerMonitor::new(config.power.clone())?
        )))
    } else {
        None
    };
    
    info!("✓ Контроллеры инициализированы");

    // Приветственное сообщение
    if let Err(e) = tts.speak("Система запущена").await {
        warn!("Ошибка TTS: {}", e);
    }

    // Запуск мониторинга питания
    let power_task = if let Some(pm) = power_monitor.clone() {
        let tts_clone = Arc::clone(&tts);
        let power_cfg = config.power.clone();
        
        Some(tokio::spawn(async move {
            power_monitoring_loop(pm, tts_clone, power_cfg).await
        }))
    } else {
        None
    };

    // Запуск основного цикла детекции
    let camera_clone = Arc::clone(&camera);
    let tts_clone = Arc::clone(&tts);
    let det_cfg = config.detection.clone();
    let opt_cfg = config.optimization.clone();

    let main_loop = tokio::spawn(async move {
        detection_loop(camera_clone, tts_clone, det_cfg, opt_cfg).await
    });

    // Ожидание сигнала завершения
    tokio::select! {
        _ = signal::ctrl_c() => {
            info!("📥 Получен сигнал завершения (Ctrl+C)");
        }
        result = main_loop => {
            match result {
                Ok(_) => info!("✓ Основной цикл завершен"),
                Err(e) => error!("❌ Ошибка основного цикла: {}", e),
            }
        }
    }

    // Graceful shutdown
    info!("🛑 Остановка системы...");
    
    if let Err(e) = tts.speak("Выключение").await {
        warn!("Ошибка TTS: {}", e);
    }
    
    let mut camera = camera.write().await;
    camera.shutdown().await?;
    
    if let Some(task) = power_task {
        task.abort();
    }
    
    info!("✓ Система остановлена");
    Ok(())
}

fn print_banner() {
    info!("╔════════════════════════════════════════╗");
    info!("║   AIVA Zero2W + IMX500                 ║");
    info!("║   Raspberry Pi Zero 2W                 ║");
    info!("║   + AI Camera IMX500                   ║");
    info!("║   + Waveshare UPS HAT C                ║");
    info!("╚════════════════════════════════════════╝");
}

fn check_system() {
    // Проверка памяти
    if let Ok(meminfo) = std::fs::read_to_string("/proc/meminfo") {
        for line in meminfo.lines() {
            if line.starts_with("MemTotal:") {
                if let Some(total) = line.split_whitespace().nth(1) {
                    if let Ok(kb) = total.parse::<u64>() {
                        let mb = kb / 1024;
                        info!("💾 Всего памяти: {} MB", mb);
                        if mb < 400 {
                            warn!("⚠️  Мало оперативной памяти!");
                        }
                    }
                }
            }
        }
    }

    // Проверка температуры
    if let Ok(temp) = std::fs::read_to_string("/sys/class/thermal/thermal_zone0/temp") {
        if let Ok(millidegrees) = temp.trim().parse::<f32>() {
            let celsius = millidegrees / 1000.0;
            info!("🌡️  Температура CPU: {:.1}°C", celsius);
            if celsius > 75.0 {
                warn!("⚠️  Высокая температура CPU!");
            }
        }
    }
}

async fn power_monitoring_loop(
    power_monitor: Arc<RwLock<PowerMonitor>>,
    tts: Arc<TtsController>,
    config: crate::config::PowerConfig,
) {
    let mut last_warning = std::time::Instant::now();
    
    loop {
        tokio::time::sleep(tokio::time::Duration::from_secs(config.check_interval)).await;
        
        let mut pm = power_monitor.write().await;
        match pm.read_status() {
            Ok(status) => {
                info!(
                    "🔋 Батарея: {:.2}V, {:.0}mA, {:.1}mW | {}",
                    status.voltage,
                    status.current,
                    status.power,
                    if status.charging { "⚡Зарядка" } else { "🔌Разрядка" }
                );
                
                // Критически низкое напряжение
                if status.voltage < config.shutdown_voltage {
                    error!("❌ Критически низкий заряд батареи!");
                    let _ = tts.speak_priority("Критически низкий заряд. Выключение.").await;
                    tokio::time::sleep(tokio::time::Duration::from_secs(3)).await;
                    
                    if config.auto_shutdown {
                        let _ = tokio::process::Command::new("sudo")
                            .arg("shutdown")
                            .arg("-h")
                            .arg("now")
                            .spawn();
                    }
                    break;
                }
                
                // Предупреждение
                if status.voltage < config.warning_voltage {
                    if last_warning.elapsed().as_secs() >= config.warning_repeat_interval {
                        warn!("⚠️  Низкий заряд батареи");
                        let _ = tts.speak("Низкий заряд батареи").await;
                        last_warning = std::time::Instant::now();
                    }
                }
            }
            Err(e) => {
                error!("Ошибка чтения UPS HAT: {}", e);
            }
        }
    }
}

async fn detection_loop(
    camera: Arc<RwLock<CameraController>>,
    tts: Arc<TtsController>,
    det_cfg: crate::config::DetectionConfig,
    opt_cfg: crate::config::OptimizationConfig,
) {
    let mut last_detection = std::time::Instant::now();
    let mut cycle_count = 0u32;
    
    loop {
        tokio::time::sleep(tokio::time::Duration::from_secs(det_cfg.scan_interval)).await;
        
        cycle_count += 1;
        
        // Периодическая очистка памяти
        if opt_cfg.force_gc_interval > 0 && cycle_count % opt_cfg.force_gc_interval == 0 {
            info!("🧹 Очистка памяти (цикл {})", cycle_count);
        }
        
        // Выполнение детекции
        let camera = camera.read().await;
        match camera.detect().await {
            Ok(detections) => {
                if !detections.is_empty() {
                    info!("📸 Обнаружено объектов: {}", detections.len());
                    
                    // Проверка cooldown
                    if last_detection.elapsed().as_secs() >= det_cfg.cooldown_period {
                        for detection in detections.iter().take(det_cfg.max_detections) {
                            // Фильтруем по enabled_classes
                            if det_cfg.enabled_classes.contains(&detection.label) {
                                let message = format_detection_message(detection, &det_cfg);
                                
                                if let Err(e) = tts.speak(&message).await {
                                    error!("Ошибка TTS: {}", e);
                                }
                                
                                // Пауза между озвучиваниями
                                tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
                            }
                        }
                        last_detection = std::time::Instant::now();
                    }
                }
            }
            Err(e) => {
                error!("Ошибка детекции: {}", e);
                tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
            }
        }
    }
}

fn format_detection_message(
    detection: &crate::camera_controller::Detection,
    config: &crate::config::DetectionConfig,
) -> String {
    let label_ru = match detection.label.as_str() {
        "person" => "человек",
        "car" => "машина",
        "dog" => "собака",
        "cat" => "кошка",
        "bird" => "птица",
        "bicycle" => "велосипед",
        "motorcycle" => "мотоцикл",
        _ => &detection.label,
    };
    
    if config.announce_person && detection.label == "person" {
        format!("Внимание! Обнаружен {}", label_ru)
    } else {
        format!("Обнаружен {}", label_ru)
    }
}
""",

    # ==================== CONFIG MODULE ====================
    "src/config.rs": """
use serde::Deserialize;
use anyhow::{Context, Result};
use std::fs;

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub system: SystemConfig,
    pub camera: CameraConfig,
    pub tts: TtsConfig,
    pub bluetooth: BluetoothConfig,
    pub power: PowerConfig,
    pub detection: DetectionConfig,
    pub optimization: OptimizationConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SystemConfig {
    pub name: String,
    pub version: String,
    pub log_level: String,
    pub max_threads: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CameraConfig {
    pub width: u32,
    pub height: u32,
    pub model: String,
    pub detection_threshold: f32,
    pub inference_timeout: u64,
    pub frame_skip: u32,
    pub preview_size: Vec<u32>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TtsConfig {
    pub model_path: String,
    pub sample_rate: u32,
    pub buffer_size: usize,
    pub max_phrase_length: usize,
    pub priority_phrases: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BluetoothConfig {
    pub enabled: bool,
    pub device_name: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PowerConfig {
    pub enabled: bool,
    pub i2c_bus: u8,
    pub i2c_address: u16,
    pub shutdown_voltage: f32,
    pub warning_voltage: f32,
    pub full_voltage: f32,
    pub check_interval: u64,
    pub auto_shutdown: bool,
    pub warning_repeat_interval: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DetectionConfig {
    pub scan_interval: u64,
    pub cooldown_period: u64,
    pub enabled_classes: Vec<String>,
    pub max_detections: usize,
    pub announce_person: bool,
    pub announce_vehicle: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct OptimizationConfig {
    pub use_swap: bool,
    pub force_gc_interval: u32,
    pub cache_enabled: bool,
    pub low_power_mode: bool,
}

impl Config {
    pub fn load(path: &str) -> Result<Self> {
        let content = fs::read_to_string(path)
            .context(format!("Не удалось прочитать файл конфигурации: {}", path))?;
        
        let config: Config = toml::from_str(&content)
            .context("Ошибка парсинга конфигурации")?;
        
        Ok(config)
    }
}
""",

    # ==================== CAMERA CONTROLLER (IMX500) ====================
    "src/camera_controller.rs": """
use anyhow::{Context, Result};
use log::{info, error};
use serde::{Deserialize, Serialize};
use tokio::process::{Command, Child, ChildStdin, ChildStdout};
use tokio::io::{AsyncWriteExt, AsyncBufReadExt, BufReader};
use std::process::Stdio;

use crate::config::CameraConfig;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Detection {
    pub label: String,
    pub confidence: f32,
    pub bbox: BoundingBox,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BoundingBox {
    pub x: f32,
    pub y: f32,
    pub width: f32,
    pub height: f32,
}

pub struct CameraController {
    config: CameraConfig,
    process: Option<Child>,
    stdin: Option<ChildStdin>,
    stdout: Option<BufReader<ChildStdout>>,
}

impl CameraController {
    pub async fn new(config: CameraConfig) -> Result<Self> {
        let mut controller = Self {
            config,
            process: None,
            stdin: None,
            stdout: None,
        };
        
        controller.start().await?;
        Ok(controller)
    }

    async fn start(&mut self) -> Result<()> {
        info!("🎥 Запуск AI Camera IMX500...");
        
        let mut child = Command::new("python3")
            .arg("scripts/camera_worker.py")
            .arg("--config")
            .arg("config.toml")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .context("Не удалось запустить camera_worker.py")?;

        let stdin = child.stdin.take()
            .context("Не удалось захватить stdin камеры")?;
        let stdout = child.stdout.take()
            .context("Не удалось захватить stdout камеры")?;

        self.process = Some(child);
        self.stdin = Some(stdin);
        self.stdout = Some(BufReader::new(stdout));

        // Даем время на инициализацию IMX500
        tokio::time::sleep(tokio::time::Duration::from_secs(3)).await;
        
        info!("✓ AI Camera IMX500 запущена");
        Ok(())
    }

    pub async fn detect(&self) -> Result<Vec<Detection>> {
        let stdin = self.stdin.as_ref()
            .context("Stdin камеры не доступен")?;
        let stdout = self.stdout.as_ref()
            .context("Stdout камеры не доступен")?;

        let mut stdin = stdin;
        stdin.write_all(b"detect\n").await
            .context("Не удалось отправить команду детекции")?;

        let mut line = String::new();
        let timeout = tokio::time::Duration::from_secs(self.config.inference_timeout);
        
        tokio::time::timeout(timeout, stdout.read_line(&mut line)).await
            .context("Таймаут при чтении результата детекции")??;

        if line.trim().is_empty() {
            return Ok(Vec::new());
        }

        let detections: Vec<Detection> = serde_json::from_str(line.trim())
            .context("Ошибка парсинга результата детекции")?;

        let filtered: Vec<Detection> = detections.into_iter()
            .filter(|d| d.confidence >= self.config.detection_threshold)
            .collect();

        Ok(filtered)
    }

    pub async fn shutdown(&mut self) -> Result<()> {
        if let Some(mut stdin) = self.stdin.take() {
            let _ = stdin.write_all(b"exit\n").await;
        }

        if let Some(mut process) = self.process.take() {
            tokio::time::timeout(
                tokio::time::Duration::from_secs(10),
                process.wait()
            ).await
                .context("Таймаут при остановке камеры")??;
        }

        info!("✓ Камера остановлена");
        Ok(())
    }
}
""",

    # ==================== TTS CONTROLLER ====================
    "src/tts_controller.rs": """
use anyhow::{Context, Result};
use log::{info, error};
use tokio::process::Command;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use crate::config::TtsConfig;

pub struct TtsController {
    config: TtsConfig,
    is_speaking: Arc<AtomicBool>,
}

impl TtsController {
    pub fn new(config: TtsConfig) -> Result<Self> {
        info!("🔊 Инициализация TTS");
        Ok(Self {
            config,
            is_speaking: Arc::new(AtomicBool::new(false)),
        })
    }

    pub async fn speak(&self, text: &str) -> Result<()> {
        // Пропускаем если уже говорим
        if self.is_speaking.load(Ordering::Relaxed) {
            return Ok(());
        }
        
        self.speak_internal(text, false).await
    }

    pub async fn speak_priority(&self, text: &str) -> Result<()> {
        // Приоритетное сообщение - не проверяем is_speaking
        self.speak_internal(text, true).await
    }

    async fn speak_internal(&self, text: &str, priority: bool) -> Result<()> {
        if !priority {
            self.is_speaking.store(true, Ordering::Relaxed);
        }

        let truncated = if text.len() > self.config.max_phrase_length {
            &text[..self.config.max_phrase_length]
        } else {
            text
        };
        
        info!("💬 TTS: {}", truncated);

        let result = Command::new("python3")
            .arg("scripts/tts_worker.py")
            .arg("--model")
            .arg(&self.config.model_path)
            .arg("--sample-rate")
            .arg(self.config.sample_rate.to_string())
            .arg("--text")
            .arg(truncated)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .status()
            .await
            .context("Не удалось запустить TTS worker");

        if !priority {
            self.is_speaking.store(false, Ordering::Relaxed);
        }

        match result {
            Ok(status) => {
                if !status.success() {
                    error!("TTS worker завершился с ошибкой: {:?}", status);
                }
                Ok(())
            }
            Err(e) => Err(e),
        }
    }
}
""",

    # ==================== POWER MONITOR (UPS HAT C) ====================
    "src/power_monitor.rs": """
use anyhow::{Context, Result};
use i2cdev::core::*;
use i2cdev::linux::LinuxI2CDevice;
use byteorder::{BigEndian, ByteOrder};
use log::{info, error};

use crate::config::PowerConfig;

// INA219 Registers
const INA219_REG_CONFIG: u8 = 0x00;
const INA219_REG_SHUNTVOLTAGE: u8 = 0x01;
const INA219_REG_BUSVOLTAGE: u8 = 0x02;
const INA219_REG_POWER: u8 = 0x03;
const INA219_REG_CURRENT: u8 = 0x04;
const INA219_REG_CALIBRATION: u8 = 0x05;

pub struct PowerStatus {
    pub voltage: f32,      // Вольты
    pub current: f32,      // Миллиамперы
    pub power: f32,        // Милливатты
    pub charging: bool,    // Зарядка или разрядка
    pub percentage: f32,   // Процент заряда
}

pub struct PowerMonitor {
    device: LinuxI2CDevice,
    config: PowerConfig,
}

impl PowerMonitor {
    pub fn new(config: PowerConfig) -> Result<Self> {
        let device_path = format!("/dev/i2c-{}", config.i2c_bus);
        let mut device = LinuxI2CDevice::new(&device_path, config.i2c_address)
            .context("Не удалось открыть I2C устройство")?;

        info!("🔌 Инициализация UPS HAT C (INA219)...");

        // Конфигурация INA219
        // 32V, ±3.2A range, 12-bit, 532µs conversion time
        let config_value: u16 = 0x219F;
        let config_bytes = config_value.to_be_bytes();
        device.smbus_write_i2c_block_data(INA219_REG_CONFIG, &config_bytes)
            .context("Не удалось настроить INA219")?;

        // Калибровка для 0.1 Ом шунта
        let calibration: u16 = 4096;
        let cal_bytes = calibration.to_be_bytes();
        device.smbus_write_i2c_block_data(INA219_REG_CALIBRATION, &cal_bytes)
            .context("Не удалось откалибровать INA219")?;

        info!("✓ UPS HAT C инициализирован");

        Ok(Self { device, config })
    }

    pub fn read_status(&mut self) -> Result<PowerStatus> {
        // Чтение напряжения шины (Bus Voltage)
        let mut bus_voltage_buf = [0u8; 2];
        self.device
            .smbus_read_i2c_block_data(INA219_REG_BUSVOLTAGE, &mut bus_voltage_buf)
            .context("Ошибка чтения напряжения")?;
        
        let bus_voltage_raw = BigEndian::read_u16(&bus_voltage_buf);
        let voltage = ((bus_voltage_raw >> 3) as f32) * 0.004; // LSB = 4mV

        // Чтение тока (Current)
        let mut current_buf = [0u8; 2];
        self.device
            .smbus_read_i2c_block_data(INA219_REG_CURRENT, &mut current_buf)
            .context("Ошибка чтения тока")?;
        
        let current_raw = BigEndian::read_i16(&current_buf);
        let current = (current_raw as f32) * 0.1; // LSB = 0.1mA

        // Чтение мощности (Power)
        let mut power_buf = [0u8; 2];
        self.device
            .smbus_read_i2c_block_data(INA219_REG_POWER, &mut power_buf)
            .context("Ошибка чтения мощности")?;
        
        let power_raw = BigEndian::read_u16(&power_buf);
        let power = (power_raw as f32) * 2.0; // LSB = 2mW

        // Определение режима (зарядка/разрядка)
        let charging = current > 0.0;

        // Расчет процента заряда (для 2x18650)
        let percentage = self.calculate_percentage(voltage);

        Ok(PowerStatus {
            voltage,
            current: current.abs(),
            power: power.abs(),
            charging,
            percentage,
        })
    }

    fn calculate_percentage(&self, voltage: f32) -> f32 {
        // Для 2x18650 последовательно:
        // 8.4V = 100%, 7.4V = 50%, 6.4V = 0%
        let min_v = self.config.shutdown_voltage;
        let max_v = self.config.full_voltage;
        
        let percentage = ((voltage - min_v) / (max_v - min_v)) * 100.0;
        percentage.max(0.0).min(100.0)
    }
}
""",

    # ==================== PYTHON CAMERA WORKER (IMX500) ====================
    "scripts/camera_worker.py": """#!/usr/bin/env python3
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
""",

    # ==================== PYTHON TTS WORKER ====================
    "scripts/tts_worker.py": """#!/usr/bin/env python3
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
""",

    # ==================== PYTHON UPS MONITOR STANDALONE ====================
    "scripts/ups_monitor.py": """#!/usr/bin/env python3
\"\"\"
Автономный монитор UPS HAT C
Для использования отдельно от основной программы
\"\"\"
import smbus2
import time
import sys

INA219_ADDRESS = 0x42
INA219_REG_BUSVOLTAGE = 0x02
INA219_REG_CURRENT = 0x04
INA219_REG_POWER = 0x03

def read_voltage(bus):
    data = bus.read_i2c_block_data(INA219_ADDRESS, INA219_REG_BUSVOLTAGE, 2)
    voltage = ((data[0] << 8) | data[1]) >> 3
    return voltage * 0.004

def read_current(bus):
    data = bus.read_i2c_block_data(INA219_ADDRESS, INA219_REG_CURRENT, 2)
    current = (data[0] << 8) | data[1]
    if current > 32767:
        current -= 65536
    return current * 0.1

def read_power(bus):
    data = bus.read_i2c_block_data(INA219_ADDRESS, INA219_REG_POWER, 2)
    power = (data[0] << 8) | data[1]
    return power * 2.0

def calculate_percentage(voltage):
    min_v = 6.4
    max_v = 8.4
    percentage = ((voltage - min_v) / (max_v - min_v)) * 100.0
    return max(0, min(100, percentage))

def main():
    try:
        bus = smbus2.SMBus(1)
        
        print("╔════════════════════════════════════════╗")
        print("║   Waveshare UPS HAT C Monitor          ║")
        print("╚════════════════════════════════════════╝")
        print()
        
        while True:
            voltage = read_voltage(bus)
            current = read_current(bus)
            power = read_power(bus)
            percentage = calculate_percentage(voltage)
            
            status = "⚡Зарядка" if current > 0 else "🔋Разрядка"
            
            print(f"\\r🔋 {percentage:5.1f}% | {voltage:.2f}V | {abs(current):6.0f}mA | {abs(power):6.1f}mW | {status}", end="")
            sys.stdout.flush()
            
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("\\n\\nМониторинг остановлен")
    except Exception as e:
        print(f"\\nОшибка: {e}")

if __name__ == "__main__":
    main()
""",

    # ==================== DEPLOYMENT SCRIPT ====================
    "deploy.sh": """#!/bin/bash
set -e

echo "╔════════════════════════════════════════════════╗"
echo "║  AIVA: Pi Zero 2W + IMX500 + UPS HAT           ║"
echo "╚════════════════════════════════════════════════╝"
echo ""

# Проверка архитектуры
ARCH=$(uname -m)
echo "✓ Архитектура: $ARCH"

# Проверка RAM
TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
echo "✓ RAM: ${TOTAL_RAM}MB"

if [ "$TOTAL_RAM" -lt 400 ]; then
    echo "❌ Недостаточно RAM"
    exit 1
fi

# SWAP конфигурация
echo "💾 Настройка SWAP (1.5GB)..."
sudo dphys-swapfile swapoff || true
sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1536/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
echo "✓ SWAP настроен"

# Обновление системы
echo "📦 Обновление пакетов..."
sudo apt update
sudo apt upgrade -y

# Установка зависимостей
echo "📦 Установка зависимостей..."
sudo apt install -y \\
    build-essential \\
    pkg-config \\
    libssl-dev \\
    libudev-dev \\
    python3 \\
    python3-pip \\
    python3-picamera2 \\
    python3-smbus2 \\
    i2c-tools \\
    libasound2-dev \\
    alsa-utils \\
    git \\
    wget

# Python зависимости
echo "🐍 Установка Python библиотек..."
pip3 install --no-cache-dir smbus2

# Включение I2C
echo "🔧 Настройка I2C..."
if ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
    echo "dtparam=i2c_arm=on" | sudo tee -a /boot/firmware/config.txt
elif ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null; then
    echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt
fi

# Проверка I2C
if i2cdetect -y 1 | grep -q "42"; then
    echo "✓ UPS HAT C обнаружен на адресе 0x42"
else
    echo "⚠️  UPS HAT C не обнаружен. Проверьте подключение."
fi

# Установка Rust
if ! command -v rustc &> /dev/null; then
    echo "🦀 Установка Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
    source "$HOME/.cargo/env"
else
    echo "✓ Rust установлен"
fi

# Установка Piper TTS
echo "🔊 Установка Piper TTS..."
if ! command -v piper &> /dev/null; then
    PIPER_VERSION="2023.11.14-2"
    
    if [ "$ARCH" == "armv7l" ]; then
        PIPER_ARCH="armv7l"
    else
        PIPER_ARCH="arm64"
    fi
    
    wget "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_${PIPER_ARCH}.tar.gz" -O /tmp/piper.tar.gz
    sudo tar -xzf /tmp/piper.tar.gz -C /usr/local/
    sudo ln -sf /usr/local/piper/piper /usr/local/bin/piper
    rm /tmp/piper.tar.gz
fi

# Создание структуры
echo "📁 Создание директорий..."
mkdir -p logs scripts voice_models/piper

# Скачивание легковесной TTS модели
if [ ! -f "voice_models/piper/ru_RU-dmitri-low.onnx" ]; then
    echo "📥 Скачивание модели TTS..."
    wget "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/low/ru_RU-dmitri-low.onnx" \\
        -O voice_models/piper/ru_RU-dmitri-low.onnx
    wget "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/low/ru_RU-dmitri-low.onnx.json" \\
        -O voice_models/piper/ru_RU-dmitri-low.onnx.json
fi

# Настройка камеры
echo "📷 Настройка камеры..."
if ! grep -q "camera_auto_detect=1" /boot/firmware/config.txt 2>/dev/null && \\
   ! grep -q "camera_auto_detect=1" /boot/config.txt 2>/dev/null; then
    echo "camera_auto_detect=1" | sudo tee -a /boot/firmware/config.txt || \\
    echo "camera_auto_detect=1" | sudo tee -a /boot/config.txt
fi

# GPU память
if ! grep -q "gpu_mem=" /boot/firmware/config.txt 2>/dev/null && \\
   ! grep -q "gpu_mem=" /boot/config.txt 2>/dev/null; then
    echo "gpu_mem=128" | sudo tee -a /boot/firmware/config.txt || \\
    echo "gpu_mem=128" | sudo tee -a /boot/config.txt
fi

# Права пользователя
echo "👤 Настройка прав..."
sudo usermod -a -G video,i2c,gpio,audio $USER

# Сборка проекта
echo "🔨 Сборка Rust проекта..."
echo "⏰ Это займет 30-60 минут на Pi Zero 2W..."
export CARGO_BUILD_JOBS=1
cargo build --release

# Systemd сервис
echo "⚙️  Создание systemd сервиса..."
sudo tee /etc/systemd/system/aiva.service > /dev/null << EOF
[Unit]
Description=AIVA AI Camera Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/target/release/vision_voice_zero_imx500
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

# Ограничение ресурсов
MemoryMax=400M
CPUQuota=90%

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

# Тестовый скрипт для UPS
chmod +x scripts/ups_monitor.py

echo ""
echo "╔════════════════════════════════════════════════╗"
echo "║           ✅ Установка завершена!             ║"
echo "╚════════════════════════════════════════════════╝"
echo ""
echo "📝 Следующие шаги:"
echo ""
echo "1. ОБЯЗАТЕЛЬНО перезагрузитесь:"
echo "   sudo reboot"
echo ""
echo "2. После перезагрузки проверьте:"
echo "   - Камеру: libcamera-hello --list-cameras"
echo "   - I2C: i2cdetect -y 1"
echo "   - UPS: python3 scripts/ups_monitor.py"
echo ""
echo "3. Запуск сервиса:"
echo "   sudo systemctl start aiva"
echo "   sudo systemctl enable aiva"
echo ""
echo "4. Мониторинг:"
echo "   sudo systemctl status aiva"
echo "   journalctl -u aiva -f"
echo ""
echo "⚠️  ВАЖНО:"
echo "   - Используйте качественное питание 5V/3A"
echo "   - Подключите активное охлаждение"
echo "   - Температура: vcgencmd measure_temp"
echo ""
""",

    # ==================== README ====================
    "README.md": """# 🎯 AIVA для Pi Zero 2W + IMX500 + UPS HAT C

Полнофункциональная система компьютерного зрения с автономным питанием.

## 🔧 Железо

- **Raspberry Pi Zero 2W** (512MB RAM, 4-core 1GHz)
- **Raspberry Pi AI Camera** (Sony IMX500 с аппаратным ML)
- **Waveshare UPS HAT C** (2x18650, INA219 мониторинг)

## ✨ Возможности

✅ **Аппаратное ускорение** - IMX500 выполняет инференс на сенсоре  
✅ **Мониторинг батареи** - Напряжение, ток, мощность, процент заряда  
✅ **Автоматическое выключение** - При критическом разряде  
✅ **Голосовые уведомления** - Piper TTS на русском  
✅ **Низкое энергопотребление** - Оптимизация для автономной работы  
✅ **Graceful shutdown** - Безопасное завершение работы  

## 📊 Производительность

| Параметр | Значение |
|----------|----------|
| Инференс IMX500 | ~30-50ms |
| TTS синтез | ~3-5s |
| RAM использование | ~200MB |
| Простой CPU | <5% |
| Энергопотребление | 400-600mA |
| Автономность | 4-8 часов (2x3000mAh) |

## 🚀 Быстрый старт

```bash
# Клонирование
git clone https://github.com/ArtemSam86/AIVA.git
cd aiva

# Установка (30-60 минут)
chmod +x deploy.sh
./deploy.sh

# ОБЯЗАТЕЛЬНАЯ перезагрузка
sudo reboot

# После перезагрузки - запуск
sudo systemctl start aiva
""",
}

# === ГЕНЕРАЦИЯ ФАЙЛОВ ===

def create_files():
    print("Генерация файлов проекта...")
    for filename, content in files.items():
        with open(filename, 'w') as f:
            f.write(content.strip())
        print(f"OK: {filename}")

if __name__ == "__main__":
    create_files()