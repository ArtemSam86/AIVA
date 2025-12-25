#!/usr/bin/env python3
"""
Автономный монитор UPS HAT C
Для использования отдельно от основной программы
"""
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
            
            print(f"\r🔋 {percentage:5.1f}% | {voltage:.2f}V | {abs(current):6.0f}mA | {abs(power):6.1f}mW | {status}", end="")
            sys.stdout.flush()
            
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("\n\nМониторинг остановлен")
    except Exception as e:
        print(f"\nОшибка: {e}")

if __name__ == "__main__":
    main()