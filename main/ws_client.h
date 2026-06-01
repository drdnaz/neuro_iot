#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"

// ws_client'in aldığı ses yanıtı için callback
// data: ham PCM, len: byte sayısı
typedef void (*ws_audio_cb_t)(const uint8_t *pcm, size_t len);

// Transcript/durum mesajları için callback (JSON text frame)
typedef void (*ws_text_cb_t)(const char *json, size_t len);

/**
 * @brief  WebSocket bağlantısını başlatır.
 *
 * @param  audio_cb  Sunucudan ses yanıtı gelince çağrılır (ham PCM)
 * @param  text_cb   Sunucudan JSON metin frame'i gelince çağrılır
 */
esp_err_t ws_client_init(ws_audio_cb_t audio_cb, ws_text_cb_t text_cb);

/**
 * @brief  Ham ses verisini (PCM) sunucuya gönderir.
 *
 * @param  pcm         Ham PCM veri tamponu (16kHz mono)
 * @param  pcm_len     PCM veri uzunluğu (byte cinsinden)
 */
esp_err_t ws_client_send_audio(const uint8_t *pcm, size_t pcm_len);

/**
 * @brief  Bağlantı durumu
 */
bool ws_client_is_connected(void);

/**
 * @brief  Ham JSON text frame gönderir (sensör verisi vb.)
 */
esp_err_t ws_client_send_text(const char *json);

/**
 * @brief  WebSocket bağlantısını kapatır.
 */
void ws_client_deinit(void);
