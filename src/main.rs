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
    info!("║   VisionVoice Zero2W + IMX500         ║");
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
        let mut camera = camera.read().await;
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