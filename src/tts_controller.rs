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