// ws_client.c — WebSocket istemcisi (Sifresiz PCM Modu, Gercek Zamanli Akis)
// esp_websocket_client (ESP-IDF 5.x) kullanır.

#include "ws_client.h"
#include "config.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_websocket_client.h"
#include "esp_log.h"
#include <string.h>
#include <stdio.h>

static const char *TAG = "ws_client";

// ─── Event bitleri ────────────────────────────────────────────────────────────
#define WS_BIT_CONNECTED  BIT0

static EventGroupHandle_t            s_eg     = NULL;
static esp_websocket_client_handle_t s_client = NULL;

static ws_audio_cb_t s_audio_cb = NULL;
static ws_text_cb_t  s_text_cb  = NULL;

// ─── WebSocket event handler ──────────────────────────────────────────────────
static void ws_event(void *arg, esp_event_base_t base,
                      int32_t event_id, void *event_data)
{
    esp_websocket_event_data_t *ev = (esp_websocket_event_data_t *)event_data;

    switch (event_id) {

    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "WebSocket baglandi");
        xEventGroupSetBits(s_eg, WS_BIT_CONNECTED);
        break;

    case WEBSOCKET_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "WebSocket baglantisi kesildi");
        xEventGroupClearBits(s_eg, WS_BIT_CONNECTED);
        break;

    case WEBSOCKET_EVENT_DATA:
        if (!ev || !ev->data_ptr || ev->data_len <= 0) break;

        if (ev->op_code == 0x01) {
            // ── Text frame ───────────────────────────────────────────────────
            if (s_text_cb) {
                s_text_cb(ev->data_ptr, ev->data_len);
            }

        } else if (ev->op_code == 0x02 || ev->op_code == 0x00) {
            // ── Binary frame (ilk parça 0x02, devam parçası 0x00) ────────────
            // Gelen ses paketini biriktirmeden doğrudan gerçek zamanlı oynatıyoruz!
            // Bu sayede SRAM'de 112KB ayırmaya gerek kalmaz, bellek tüketimi 0 olur.
            if (s_audio_cb) {
                s_audio_cb((const uint8_t *)ev->data_ptr, ev->data_len);
            }
        }
        break;

    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGE(TAG, "WebSocket hata");
        break;

    default:
        break;
    }
}

// ─── Genel API ────────────────────────────────────────────────────────────────
esp_err_t ws_client_init(ws_audio_cb_t audio_cb, ws_text_cb_t text_cb)
{
    s_audio_cb = audio_cb;
    s_text_cb  = text_cb;

    s_eg = xEventGroupCreate();
    if (!s_eg) return ESP_ERR_NO_MEM;

    esp_websocket_client_config_t cfg = {
        .uri                  = SERVER_WS_URI,
        .buffer_size          = 8192,
        .reconnect_timeout_ms = 5000,
        .network_timeout_ms   = 10000,
    };

    s_client = esp_websocket_client_init(&cfg);
    if (!s_client) return ESP_FAIL;

    ESP_ERROR_CHECK(esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY,
                                                   ws_event, NULL));
    ESP_ERROR_CHECK(esp_websocket_client_start(s_client));

    ESP_LOGI(TAG, "WebSocket baslatildi (sifresiz PCM, anlik akis): %s", SERVER_WS_URI);
    return ESP_OK;
}

esp_err_t ws_client_send_audio(const uint8_t *pcm, size_t pcm_len)
{
    if (!ws_client_is_connected())
        return ESP_ERR_INVALID_STATE;

    int r = esp_websocket_client_send_bin(s_client, (const char *)pcm,
                                           (int)pcm_len, pdMS_TO_TICKS(15000));

    if (r < 0) { ESP_LOGE(TAG, "Ses gonderilemedi"); return ESP_FAIL; }
    ESP_LOGI(TAG, "Ses gonderildi: %zu byte", pcm_len);
    return ESP_OK;
}

esp_err_t ws_client_send_text(const char *json)
{
    if (!ws_client_is_connected()) return ESP_ERR_INVALID_STATE;
    int n = (int)strlen(json);
    return esp_websocket_client_send_text(s_client, json, n,
                                           pdMS_TO_TICKS(3000)) >= 0
        ? ESP_OK : ESP_FAIL;
}

bool ws_client_is_connected(void)
{
    return s_eg && (xEventGroupGetBits(s_eg) & WS_BIT_CONNECTED);
}

void ws_client_deinit(void)
{
    if (s_client) {
        esp_websocket_client_stop(s_client);
        esp_websocket_client_destroy(s_client);
        s_client = NULL;
    }
    if (s_eg)     { vEventGroupDelete(s_eg); s_eg = NULL; }
}
