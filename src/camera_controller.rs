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

    pub async fn detect(&mut self) -> Result<Vec<Detection>> {
        let stdin = self.stdin.as_ref()
            .context("Stdin камеры не доступен")?;
        let stdout = self.stdout.as_ref().as_mut()
            .context("Stdout камеры не доступен")?;

        let mut stdin = stdin;
        stdin.write_all(b"detect
").await
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
            let _ = stdin.write_all(b"exit
").await;
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