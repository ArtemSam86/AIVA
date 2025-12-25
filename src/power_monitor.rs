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
            .smbus_read_i2c_block_data(INA219_REG_BUSVOLTAGE, 2)
            .context("Ошибка чтения напряжения")?;
        
        let bus_voltage_raw = BigEndian::read_u16(&bus_voltage_buf);
        let voltage = ((bus_voltage_raw >> 3) as f32) * 0.004; // LSB = 4mV

        // Чтение тока (Current)
        let mut current_buf = [0u8; 2];
        self.device
            .smbus_read_i2c_block_data(INA219_REG_CURRENT, 2)
            .context("Ошибка чтения тока")?;
        
        let current_raw = BigEndian::read_i16(&current_buf);
        let current = (current_raw as f32) * 0.1; // LSB = 0.1mA

        // Чтение мощности (Power)
        let mut power_buf = [0u8; 2];
        self.device
            .smbus_read_i2c_block_data(INA219_REG_POWER, 2)
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