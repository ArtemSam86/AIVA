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