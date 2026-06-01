#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "driver/spi_master.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "nvs_flash.h"
#include "rom/ets_sys.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "crypto.h"
#include "ws_client.h"
#include "config.h"

// ==========================================
// 0. WiFi VE SUNUCU YAPILANDIRMASI
// ==========================================
#define WIFI_SSID "abdulsamed"      // ← WiFi ağ adınızı yazın
#define WIFI_PASS "samet123"        // ← WiFi şifrenizi yazın
#define SERVER_HOST "10.205.95.216" // PC'nin IP adresi (otomatik bulundu)
#define SERVER_PORT 8080

static volatile bool s_wifi_connected = false;
static volatile bool s_recording =
    false; // Ses kaydı sırasında mikrofon çakışmasını önler

// ==========================================
// 1. PIN VE DONANIM TANIMLAMALARI
// ==========================================
#define PIR_PIN GPIO_NUM_1
#define GAS_CHANNEL ADC_CHANNEL_1 // MQ-135 (GPIO 2)
#define DS_PIN GPIO_NUM_4         // DS18B20 Sıcaklık Sensörü

// I2S INMP441 Mikrofon
#define I2S_MIC_SD GPIO_NUM_5
#define I2S_MIC_SCK GPIO_NUM_6
#define I2S_MIC_WS GPIO_NUM_7

// I2S MAX98357A Amplifikatör (Siren)
#define I2S_AMP_BCLK GPIO_NUM_16
#define I2S_AMP_LRC GPIO_NUM_17
#define I2S_AMP_DIN GPIO_NUM_15

// ILI9341 SPI Ekran Pinleri
#define PIN_NUM_MISO -1
#define PIN_NUM_MOSI 11
#define PIN_NUM_CLK 12 // Fiziksel bağlantınıza göre GPIO 12
#define PIN_NUM_CS 10
#define PIN_NUM_DC 9
#define PIN_NUM_RST 8

// EKRAN YAZILARI TERS / AYNALI İSE AŞAĞIDAKİ DEĞERLERDEN BİRİNİ DENEYİN:
// 0x08 -> Standart Dikey
// 0x48 -> Yatay Aynalanmış (Harfler sağdan sola yazıyorsa - ayna görüntüsü)
// 0x88 -> Dikey Baş Aşağı (Yazılar ters dönmüşse)
// 0xC8 -> 180 Derece Döndürülmüş (Hem baş aşağı hem aynalanmış ise)
#define LCD_MADCTR_VAL 0x88 // Soldan sağa düzgün okuma için 0x88 yapıldı!

// ==========================================
// 2. KÜRESEL DEĞİŞKENLER VE YAPILAR (FreeRTOS için)
// ==========================================
typedef struct {
  float temperature;
  int gas_raw;
  int audio_level;
  int motion_detected;
} sensor_data_t;

// Paylaşılan sensör verileri
sensor_data_t current_data = {
    .temperature = -99.0, .gas_raw = 0, .audio_level = 0, .motion_detected = 0};

// Veri okuma/yazma çakışmalarını önlemek için Spinlock/Mutex yapıları
portMUX_TYPE data_mux =
    portMUX_INITIALIZER_UNLOCKED; // Genel sensör veri kilidi
portMUX_TYPE ds_mux =
    portMUX_INITIALIZER_UNLOCKED; // 1-Wire hassas zamanlama kilidi

i2s_chan_handle_t rx_chan;   // Mikrofon Kanalı
i2s_chan_handle_t tx_chan;   // Siren/Amfi Kanalı
spi_device_handle_t spi_lcd; // LCD SPI Cihazı
SemaphoreHandle_t mic_sem = NULL; // Mikrofon erişim kilidi

volatile bool alarm_active = false;           // Sensör koşulları alarm gerektiriyor
volatile TickType_t last_llm_audio_tick = 0;  // Son LLM ses parçasının zamanı

// ─── WebSocket Callbacks ──────────────────────────────────────────────────────
void on_audio_received(const uint8_t *pcm, size_t len) {
  printf("[WS_AUDIO] Sunucudan %zu byte sifresiz PCM alindi, caliniyor...\n", len);
  last_llm_audio_tick = xTaskGetTickCount(); // alarm_sound_task bu sürede susar
  size_t written = 0;
  esp_err_t err = i2s_channel_write(tx_chan, pcm, len, &written, portMAX_DELAY);
  if (err != ESP_OK) {
    printf("[WS_AUDIO] I2S yazma hatasi: %s\n", esp_err_to_name(err));
  } else if (written != len) {
    printf("[WS_AUDIO] Eksik yazma: %zu/%zu byte\n", written, len);
  }
}

void on_text_received(const char *json, size_t len) {
  printf("[WS_TEXT] Gelen Mesaj: %.*s\n", (int)len, json);
}

// ==========================================
// 3. ENTEGRE 8x8 BITMAP FONT TABLOSU (U+0020 - U+005A)
// ==========================================
static const uint8_t font8x8_basic[59][8] = {
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}, // Boşluk (0x20)
    {0x18, 0x3C, 0x3C, 0x18, 0x18, 0x00, 0x18, 0x00}, // !
    {0x6C, 0x6C, 0x6C, 0x00, 0x00, 0x00, 0x00, 0x00}, // "
    {0x36, 0x36, 0x7F, 0x36, 0x7F, 0x36, 0x36, 0x00}, // #
    {0x0C, 0x3E, 0x03, 0x1E, 0x30, 0x1F, 0x0C, 0x00}, // $
    {0x00, 0x63, 0x33, 0x18, 0x0C, 0x66, 0x63, 0x00}, // %
    {0x1C, 0x36, 0x1C, 0x2E, 0x66, 0x3B, 0x1F, 0x00}, // &
    {0x06, 0x0E, 0x0C, 0x00, 0x00, 0x00, 0x00, 0x00}, // '
    {0x18, 0x0C, 0x06, 0x06, 0x06, 0x0C, 0x18, 0x00}, // (
    {0x18, 0x30, 0x60, 0x60, 0x60, 0x30, 0x18, 0x00}, // )
    {0x18, 0x7E, 0x3C, 0x7E, 0x18, 0x00, 0x00, 0x00}, // *
    {0x00, 0x18, 0x18, 0x7E, 0x18, 0x18, 0x00, 0x00}, // +
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x30}, // ,
    {0x00, 0x00, 0x00, 0x7E, 0x00, 0x00, 0x00, 0x00}, // -
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x00}, // .
    {0x00, 0x03, 0x06, 0x0C, 0x18, 0x30, 0x60, 0x00}, // /
    {0x3E, 0x63, 0x63, 0x63, 0x63, 0x63, 0x3E, 0x00}, // 0
    {0x0C, 0x0E, 0x0C, 0x0C, 0x0C, 0x0C, 0x3F, 0x00}, // 1
    {0x1E, 0x33, 0x30, 0x1C, 0x06, 0x33, 0x3F, 0x00}, // 2
    {0x1E, 0x33, 0x30, 0x1C, 0x30, 0x33, 0x1E, 0x00}, // 3
    {0x38, 0x3C, 0x36, 0x33, 0x7F, 0x30, 0x78, 0x00}, // 4
    {0x3F, 0x03, 0x1F, 0x30, 0x30, 0x33, 0x1E, 0x00}, // 5
    {0x1C, 0x06, 0x03, 0x1F, 0x33, 0x33, 0x1E, 0x00}, // 6
    {0x3F, 0x33, 0x30, 0x18, 0x0C, 0x0C, 0x0C, 0x00}, // 7
    {0x1E, 0x33, 0x33, 0x1E, 0x33, 0x33, 0x1E, 0x00}, // 8
    {0x1E, 0x33, 0x33, 0x3E, 0x30, 0x18, 0x0E, 0x00}, // 9
    {0x00, 0x18, 0x18, 0x00, 0x18, 0x18, 0x00, 0x00}, // :
    {0x00, 0x18, 0x18, 0x00, 0x18, 0x18, 0x30, 0x00}, // ;
    {0x18, 0x0C, 0x06, 0x03, 0x06, 0x0C, 0x18, 0x00}, // <
    {0x00, 0x00, 0x7E, 0x00, 0x7E, 0x00, 0x00, 0x00}, // =
    {0x18, 0x30, 0x60, 0xC0, 0x60, 0x30, 0x18, 0x00}, // >
    {0x1E, 0x33, 0x30, 0x18, 0x0C, 0x00, 0x0C, 0x00}, // ?
    {0x3E, 0x63, 0x7B, 0x7B, 0x7B, 0x03, 0x1E, 0x00}, // @
    {0x0C, 0x1E, 0x33, 0x33, 0x3F, 0x33, 0x33, 0x00}, // A
    {0x3F, 0x66, 0x66, 0x3E, 0x66, 0x66, 0x3F, 0x00}, // B
    {0x3E, 0x63, 0x03, 0x03, 0x03, 0x63, 0x3E, 0x00}, // C
    {0x3F, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3F, 0x00}, // D
    {0x3F, 0x06, 0x06, 0x3E, 0x06, 0x06, 0x3F, 0x00}, // E
    {0x3F, 0x06, 0x06, 0x3E, 0x06, 0x06, 0x06, 0x00}, // F
    {0x3E, 0x63, 0x03, 0x3B, 0x63, 0x63, 0x3E, 0x00}, // G
    {0x33, 0x33, 0x33, 0x3F, 0x33, 0x33, 0x33, 0x00}, // H
    {0x1E, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x1E, 0x00}, // I
    {0x78, 0x30, 0x30, 0x30, 0x30, 0x33, 0x1E, 0x00}, // J
    {0x33, 0x36, 0x3C, 0x1E, 0x3C, 0x36, 0x33, 0x00}, // K
    {0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x3F, 0x00}, // L
    {0x63, 0x77, 0x7F, 0x6B, 0x63, 0x63, 0x63, 0x00}, // M
    {0x33, 0x3B, 0x3F, 0x37, 0x33, 0x33, 0x33, 0x00}, // N
    {0x3E, 0x63, 0x63, 0x63, 0x63, 0x63, 0x3E, 0x00}, // O
    {0x3F, 0x66, 0x66, 0x3E, 0x06, 0x06, 0x06, 0x00}, // P
    {0x3E, 0x63, 0x63, 0x63, 0x6B, 0x37, 0x5E, 0x00}, // Q
    {0x3F, 0x66, 0x66, 0x3E, 0x36, 0x66, 0x63, 0x00}, // R
    {0x1E, 0x33, 0x07, 0x0E, 0x38, 0x33, 0x1E, 0x00}, // S
    {0x3F, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x00}, // T
    {0x33, 0x33, 0x33, 0x33, 0x33, 0x33, 0x1E, 0x00}, // U
    {0x33, 0x33, 0x33, 0x33, 0x33, 0x1E, 0x0C, 0x00}, // V
    {0x63, 0x63, 0x63, 0x6B, 0x7F, 0x77, 0x63, 0x00}, // W
    {0x63, 0x63, 0x36, 0x1C, 0x36, 0x63, 0x63, 0x00}, // X
    {0x33, 0x33, 0x33, 0x1E, 0x0C, 0x0C, 0x0C, 0x00}, // Y
    {0x3F, 0x30, 0x18, 0x0C, 0x06, 0x03, 0x3F, 0x00}  // Z
};

// ==========================================
// 4. SENSÖR VE AKTÜATÖR SÜRÜCÜ FONKSİYONLARI
// ==========================================

void init_amplifier() {
  i2s_chan_config_t chan_cfg =
      I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
  i2s_new_channel(&chan_cfg, &tx_chan, NULL);
  i2s_std_config_t std_cfg = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(
          22050), // TTS çıkışıyla eşleşir (22050Hz)
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                      I2S_SLOT_MODE_MONO),
      .gpio_cfg = {.mclk = I2S_GPIO_UNUSED,
                   .bclk = I2S_AMP_BCLK,
                   .ws = I2S_AMP_LRC,
                   .dout = I2S_AMP_DIN,
                   .din = I2S_GPIO_UNUSED}};
  i2s_channel_init_std_mode(tx_chan, &std_cfg);
  i2s_channel_enable(tx_chan);
}

void play_alarm_tone() {
  int16_t tone_buf[500];
  size_t bytes_written = 0;
  for (int i = 0; i < 500; i++) {
    tone_buf[i] = (int16_t)(10000 * sin(2 * M_PI * 880 * i / 22050));
  }
  for (int k = 0; k < 3; k++) {
    i2s_channel_write(tx_chan, tone_buf, sizeof(tone_buf), &bytes_written,
                      portMAX_DELAY);
    vTaskDelay(pdMS_TO_TICKS(100));
  }
}


void init_microphone() {
  if (!mic_sem) {
    mic_sem = xSemaphoreCreateMutex();
  }
  i2s_chan_config_t chan_cfg =
      I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  i2s_new_channel(&chan_cfg, NULL, &rx_chan);
  i2s_std_config_t std_cfg = {.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
                              .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
                                  I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
                              .gpio_cfg = {.mclk = I2S_GPIO_UNUSED,
                                           .bclk = I2S_MIC_SCK,
                                           .ws = I2S_MIC_WS,
                                           .din = I2S_MIC_SD}};
  std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT; // INMP441: L/R = GND -> Sol kanal
  i2s_channel_init_std_mode(rx_chan, &std_cfg);
  i2s_channel_enable(rx_chan);
}

int get_audio_level() {
  int max_level = 0;
  int32_t raw_samples[128]; // Buffer boyutu 128'e (8ms) çıkarıldı
  size_t bytes_read = 0;

  // El şıklatma gibi çok kısa sesleri (10-30ms) kaçırmamak için
  // ~160ms boyunca kesintisiz dinleme yapıp maksimum tepe (peak) değerini
  // buluyoruz
  if (mic_sem && xSemaphoreTake(mic_sem, pdMS_TO_TICKS(50)) == pdTRUE) {
    for (int k = 0; k < 20; k++) {
      i2s_channel_read(rx_chan, raw_samples, sizeof(raw_samples), &bytes_read,
                       portMAX_DELAY);
      int samples_read = bytes_read / sizeof(int32_t);
      int64_t total_energy = 0;
      for (int i = 0; i < samples_read; i++) {
        total_energy += abs(raw_samples[i] >> 12);
      }
      int current_level = (int)(total_energy / (samples_read ? samples_read : 1));
      if (current_level > max_level) {
        max_level = current_level;
      }
    }
    xSemaphoreGive(mic_sem);
  }
  return max_level;
}

// 1-Wire Isı Sensörü DS18B20 Sürücüsü (Kritik kesmelerle korunan hassas
// zamanlama)
int ds_reset() {
  portENTER_CRITICAL(&ds_mux);
  gpio_set_direction(DS_PIN, GPIO_MODE_OUTPUT);
  gpio_set_level(DS_PIN, 0);
  ets_delay_us(480);
  gpio_set_direction(DS_PIN, GPIO_MODE_INPUT);
  ets_delay_us(70);
  int presence = gpio_get_level(DS_PIN);
  ets_delay_us(410);
  portEXIT_CRITICAL(&ds_mux);
  return presence;
}

void ds_write_bit(int b) {
  portENTER_CRITICAL(&ds_mux);
  gpio_set_direction(DS_PIN, GPIO_MODE_OUTPUT);
  gpio_set_level(DS_PIN, 0);
  ets_delay_us(b ? 5 : 60);
  gpio_set_direction(DS_PIN, GPIO_MODE_INPUT);
  ets_delay_us(b ? 60 : 5);
  portEXIT_CRITICAL(&ds_mux);
}

int ds_read_bit() {
  portENTER_CRITICAL(&ds_mux);
  gpio_set_direction(DS_PIN, GPIO_MODE_OUTPUT);
  gpio_set_level(DS_PIN, 0);
  ets_delay_us(2);
  gpio_set_direction(DS_PIN, GPIO_MODE_INPUT);
  ets_delay_us(10);
  int b = gpio_get_level(DS_PIN);
  ets_delay_us(50);
  portEXIT_CRITICAL(&ds_mux);
  return b;
}

void ds_write_byte(int val) {
  for (int i = 0; i < 8; i++) {
    ds_write_bit(val & 0x01);
    val >>= 1;
  }
}

int ds_read_byte() {
  int val = 0;
  for (int i = 0; i < 8; i++) {
    if (ds_read_bit())
      val |= (1 << i);
  }
  return val;
}

float read_temperature() {
  if (ds_reset() == 0) {
    ds_write_byte(0xCC); // Skip ROM
    ds_write_byte(0x44); // Convert T
    // 750ms FreeRTOS uykusu (Task'ı askıya alır, ancak I2S kesmelerini
    // kilitlemez!)
    vTaskDelay(pdMS_TO_TICKS(750));

    ds_reset();
    ds_write_byte(0xCC); // Skip ROM
    ds_write_byte(0xBE); // Read Scratchpad
    int lsb = ds_read_byte();
    int msb = ds_read_byte();
    return ((msb << 8) | lsb) / 16.0;
  }
  return -99.0;
}

// ==========================================
// 5. SPI LCD (ILI9341) SÜRÜCÜ VE METİN ÇİZİM FONKSİYONLARI
// ==========================================

void lcd_cmd(spi_device_handle_t spi, const uint8_t cmd) {
  spi_transaction_t t;
  memset(&t, 0, sizeof(t));
  t.length = 8;
  t.tx_buffer = &cmd;
  gpio_set_level(PIN_NUM_DC, 0); // Komut modu
  spi_device_polling_transmit(spi, &t);
}

void lcd_data(spi_device_handle_t spi, const uint8_t *data, int len) {
  if (len == 0)
    return;
  spi_transaction_t t;
  memset(&t, 0, sizeof(t));
  t.length = len * 8;
  t.tx_buffer = data;
  gpio_set_level(PIN_NUM_DC, 1); // Veri modu
  spi_device_polling_transmit(spi, &t);
}

void lcd_data_byte(spi_device_handle_t spi, const uint8_t data) {
  lcd_data(spi, &data, 1);
}

void lcd_reset() {
  gpio_set_level(PIN_NUM_RST, 0);
  vTaskDelay(pdMS_TO_TICKS(100));
  gpio_set_level(PIN_NUM_RST, 1);
  vTaskDelay(pdMS_TO_TICKS(100));
}

void lcd_init(spi_device_handle_t spi) {
  lcd_reset();
  lcd_cmd(spi, 0x01); // Software Reset
  vTaskDelay(pdMS_TO_TICKS(150));
  lcd_cmd(spi, 0x28); // Display OFF

  // Güç ve ekran ayarları
  lcd_cmd(spi, 0xC0); // Power Control 1
  uint8_t pwr1[] = {0x23};
  lcd_data(spi, pwr1, 1);

  lcd_cmd(spi, 0xC1); // Power Control 2
  uint8_t pwr2[] = {0x10};
  lcd_data(spi, pwr2, 1);

  lcd_cmd(spi, 0xC5); // VCOM Control 1
  uint8_t vcom1[] = {0x3E, 0x28};
  lcd_data(spi, vcom1, 2);

  lcd_cmd(spi, 0xC7); // VCOM Control 2
  uint8_t vcom2[] = {0x86};
  lcd_data(spi, vcom2, 1);

  lcd_cmd(spi, 0x36);               // Memory Access Control (Yatay/Dikey modu)
  uint8_t mac[] = {LCD_MADCTR_VAL}; // Tanımlanan yönlendirme değerini gönder
  lcd_data(spi, mac, 1);

  lcd_cmd(spi, 0x3A); // Pixel Format (16-bit RGB565)
  lcd_data_byte(spi, 0x55);

  lcd_cmd(spi, 0xB1); // Frame Rate Control
  uint8_t frc[] = {0x00, 0x18};
  lcd_data(spi, frc, 2);

  lcd_cmd(spi, 0xB6); // Display Function Control
  uint8_t dfc[] = {0x08, 0x82, 0x27};
  lcd_data(spi, dfc, 3);

  lcd_cmd(spi, 0x11); // Sleep Out (Uykudan çık)
  vTaskDelay(pdMS_TO_TICKS(150));

  lcd_cmd(spi, 0x29); // Display ON (Ekranı aç)
  vTaskDelay(pdMS_TO_TICKS(50));
}

// Ekranın Belirli Bir Bölgesine Renkli Kutu Çizme
void lcd_draw_rect(spi_device_handle_t spi, uint16_t x, uint16_t y, uint16_t w,
                   uint16_t h, uint16_t color) {
  lcd_cmd(spi, 0x2A);
  uint8_t col_data[] = {x >> 8, x & 0xFF, (x + w - 1) >> 8, (x + w - 1) & 0xFF};
  lcd_data(spi, col_data, 4);

  lcd_cmd(spi, 0x2B);
  uint8_t page_data[] = {y >> 8, y & 0xFF, (y + h - 1) >> 8,
                         (y + h - 1) & 0xFF};
  lcd_data(spi, page_data, 4);

  lcd_cmd(spi, 0x2C);

  uint8_t high = color >> 8;
  uint8_t low = color & 0xFF;

  uint16_t *buf = malloc(w * sizeof(uint16_t));
  if (buf == NULL)
    return;

  uint8_t *byte_buf = (uint8_t *)buf;
  for (int i = 0; i < w; i++) {
    byte_buf[i * 2] = high;
    byte_buf[i * 2 + 1] = low;
  }

  for (int i = 0; i < h; i++) {
    lcd_data(spi, byte_buf, w * 2);
  }

  free(buf);
}

// Ekranın Tamamını Tek Renk Yapma
void lcd_clear(spi_device_handle_t spi, uint16_t color) {
  lcd_draw_rect(spi, 0, 0, 240, 320, color);
}

// Donanımsal 8x8 Harf Çizme Fonksiyonu
void lcd_draw_char(spi_device_handle_t spi, uint16_t x, uint16_t y, char c,
                   uint16_t color, uint16_t bg_color, int scale) {
  if (c < ' ' || c > 'Z')
    c = ' ';
  int idx = c - ' ';

  for (int r = 0; r < 8; r++) {
    uint8_t row_data = font8x8_basic[idx][r];
    for (int col = 0; col < 8; col++) {
      int bit = (row_data >> col) & 1;
      uint16_t pixel_color = bit ? color : bg_color;
      if (scale == 1) {
        lcd_draw_rect(spi, x + col, y + r, 1, 1, pixel_color);
      } else {
        lcd_draw_rect(spi, x + col * scale, y + r * scale, scale, scale,
                      pixel_color);
      }
    }
  }
}

// Ekran Üzerine Metin Çizme Fonksiyonu
void lcd_draw_string(spi_device_handle_t spi, uint16_t x, uint16_t y,
                     const char *str, uint16_t color, uint16_t bg_color,
                     int scale) {
  while (*str) {
    lcd_draw_char(spi, x, y, *str, color, bg_color, scale);
    x += 8 * scale;
    str++;
  }
}

// Sensör Değer Kutusunu ve Metinlerini Tek Seferde Çizme
void draw_sensor_box(spi_device_handle_t spi, uint16_t x, uint16_t y,
                     uint16_t w, uint16_t h, uint16_t color, const char *label,
                     const char *val_str, const char *status_str) {
  lcd_draw_rect(spi, x, y, w, h, color);
  uint16_t text_color = 0xFFFF; // Beyaz
  lcd_draw_string(spi, x + 8, y + 20, label, text_color, color, 1);
  lcd_draw_string(spi, x + 8, y + 50, val_str, text_color, color, 1);
  lcd_draw_string(spi, x + 8, y + 80, status_str, text_color, color, 1);
}

// ==========================================
// 6. FREERTOS PARALEL GÖREVLERİ (TASKS)
// ==========================================

// Alarm ses görevi: alarm_active olduğu sürece ton çalar.
// LLM son ses parçasından 800ms geçmeden susar (LLM'e öncelik tanır).
void alarm_sound_task(void *pvParameters) {
  printf("[TASK] Alarm ses gorevi baslatildi.\n");
  while (1) {
    if (alarm_active) {
      bool llm_speaking = (xTaskGetTickCount() - last_llm_audio_tick) < pdMS_TO_TICKS(800);
      if (!llm_speaking) {
        play_alarm_tone();
      } else {
        vTaskDelay(pdMS_TO_TICKS(100));
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(200));
    }
  }
}

// Sensör seviye sınıflandırması: 0=güvenli, 1=sarı(uyarı), 2=kırmızı(kritik)
static int level_temp(float t) {
  if (t > 31.5f) return 2;
  if (t >= 29.0f) return 1;
  return 0;
}
static int level_gas(int g) {
  if (g > 1200) return 2;
  if (g >= 800) return 1;
  return 0;
}
static int level_audio(int a) {
  if (a > 2500) return 2;
  if (a >= 1000) return 1;
  return 0;
}
static int level_motion(int m) { return m == 1 ? 1 : 0; }

// Alarm koşulları:
//   3+ sarı  |  2 kırmızı+1 sarı  |  1 kırmızı+3 sarı  |  3+ kırmızı
static bool check_alarm(int yellow, int red) {
  return (yellow >= 3) ||
         (red >= 2 && yellow >= 1) ||
         (red >= 1 && yellow >= 3) ||
         (red >= 3);
}

// GÖREV A: HIZLI SENSÖR OKUMA VE ALARM TETİKLEYİCİ GÖREVİ (Mikrofon + Gaz +
// PIR) Sıcaklık delay engeline takılmadan mikrofondan sürekli veri alır ve el
// şıklatmaları kaçırmaz!
void sensor_reading_task(void *pvParameters) {
  printf("[TASK] Hızlı sensör okuma görevi başlatıldı.\n");

  // PIR Giriş Yapılandırması
  gpio_reset_pin(PIR_PIN);
  gpio_set_direction(PIR_PIN, GPIO_MODE_INPUT);

  // MQ-135 Gaz Sensörü ADC Yapılandırması
  adc_oneshot_unit_handle_t adc1_handle;
  adc_oneshot_unit_init_cfg_t init_config1 = {.unit_id = ADC_UNIT_1};
  adc_oneshot_new_unit(&init_config1, &adc1_handle);
  adc_oneshot_chan_cfg_t config = {.bitwidth = ADC_BITWIDTH_DEFAULT,
                                   .atten = ADC_ATTEN_DB_12};
  adc_oneshot_config_channel(adc1_handle, GAS_CHANNEL, &config);

  // Mikrofon İlklendirmesi (Amfi app_main içinde başlatıldı)
  init_microphone();

  int ses_hafizasi = 0;

  while (1) {
    // Sensörleri Oku (Hızlı okuma, delay yok!)
    int pir_val = gpio_get_level(PIR_PIN);
    int gas_raw = 0;
    adc_oneshot_read(adc1_handle, GAS_CHANNEL, &gas_raw);
    // Ses kaydı sırasında mikrofon erişimini atla (kayıt görevi rx_chan
    // kullanıyor)
    int audio_lvl = s_recording ? current_data.audio_level : get_audio_level();

    // Okunan verileri güvenli şekilde güncelle
    taskENTER_CRITICAL(&data_mux);
    current_data.gas_raw = gas_raw;
    current_data.audio_level = audio_lvl;
    current_data.motion_detected = pir_val;
    taskEXIT_CRITICAL(&data_mux);

    // Akustik gürültü hafıza (latch) mekanizması
    if (audio_lvl > 1000) {
      ses_hafizasi = 20;
    }
    if (ses_hafizasi > 0) {
      ses_hafizasi--;
    }

    float temp = current_data.temperature;

    if (temp != -99.0) {
      // Her sensör seviyesini hesapla
      int lv_temp   = level_temp(temp);
      int lv_gas    = level_gas(gas_raw);
      int lv_audio  = level_audio(audio_lvl);
      int lv_motion = level_motion(pir_val);

      int yellow = (lv_temp == 1) + (lv_gas == 1) + (lv_audio == 1) + (lv_motion == 1);
      int red    = (lv_temp == 2) + (lv_gas == 2) + (lv_audio == 2) + (lv_motion == 2);

      bool triggered = check_alarm(yellow, red);
      if (triggered && !alarm_active) {
        printf("[ALARM] BASLADI! Sari=%d Kirmizi=%d  (ISI:%.1fC GAZ:%d SES:%d PIR:%d)\n",
               yellow, red, temp, gas_raw, audio_lvl, pir_val);
      } else if (!triggered && alarm_active) {
        printf("[ALARM] SONA ERDI. Sari=%d Kirmizi=%d\n", yellow, red);
      }
      alarm_active = triggered;
    }

    // Diğer görevlere süre vermek için bekleme
    vTaskDelay(pdMS_TO_TICKS(150));
  }
}

// GÖREV B: BAĞIMSIZ DS18B20 SICAKLIK OKUMA GÖREVİ
// 750ms'lik ağır dönüştürme gecikmesini ayrı bir görevde yöneterek mikrofonu
// engellemesini önleriz!
void temp_reading_task(void *pvParameters) {
  printf("[TASK] Bağımsız DS18B20 Sıcaklık okuma görevi başlatıldı.\n");
  while (1) {
    float temp = read_temperature(); // 750ms gecikmeyi burada güvenle yaşarız

    taskENTER_CRITICAL(&data_mux);
    current_data.temperature = temp;
    taskEXIT_CRITICAL(&data_mux);

    // Sıcaklık yavaş değiştiği için her 1 saniyede bir okumak fazlasıyla
    // yeterlidir
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

// GÖREV C: GÖRSEL LCD DASHBOARD GÜNCELLEME GÖREVİ (Metin Destekli)
void lcd_display_task(void *pvParameters) {
  spi_device_handle_t spi = (spi_device_handle_t)pvParameters;
  printf("[TASK] LCD Dashboard ekran görevi başlatıldı.\n");

  // Şık Arayüz Renkleri (RGB565)
  uint16_t COLOR_BG = 0x10A2;       // Koyu Grafit
  uint16_t COLOR_HEADER = 0x01EF;   // Şık Neon Mavi
  uint16_t COLOR_SAFE = 0x1382;     // Güvenli Yeşil
  uint16_t COLOR_WARNING = 0xEAA0;  // Uyarı Turuncu
  uint16_t COLOR_CRITICAL = 0xF800; // Canlı Kırmızı
  uint16_t COLOR_TXT_BG = 0x01EF;

  // Ekranı temizle
  lcd_clear(spi, COLOR_BG);
  vTaskDelay(pdMS_TO_TICKS(50));

  // Üst Bilgi Başlık Çubuğunu Çiz (Header Bar) ve Başlığı Yaz
  lcd_draw_rect(spi, 0, 0, 240, 40, COLOR_HEADER);
  lcd_draw_string(spi, 10, 12, "NEURO-SENTINEL", 0xFFFF, COLOR_TXT_BG, 2);

  char temp_val[16];
  char temp_status[16];
  uint16_t temp_color;

  char gas_val[16];
  char gas_status[16];
  uint16_t gas_color;

  char sound_val[16];
  char sound_status[16];
  uint16_t sound_color;

  char motion_val[16];
  char motion_status[16];
  uint16_t motion_color;

  while (1) {
    // Güvenli şekilde yerel veri çek
    taskENTER_CRITICAL(&data_mux);
    sensor_data_t local_data = current_data;
    taskEXIT_CRITICAL(&data_mux);

    if (local_data.temperature != -99.0) {

      // ==========================================
      // 1. ISIL DURUM KUTUSU (TEMPERATURE) - Üst Sol
      // ==========================================
      if (local_data.temperature > 31.5) {
        temp_color = COLOR_CRITICAL;
        strcpy(temp_status, "ISI: ALARM");
      } else if (local_data.temperature >= 29.0) {
        temp_color = COLOR_WARNING;
        strcpy(temp_status, "ISI: ILIK");
      } else {
        temp_color = COLOR_SAFE;
        strcpy(temp_status, "ISI: NORMAL");
      }

      snprintf(temp_val, sizeof(temp_val), "%.1f C", local_data.temperature);
      draw_sensor_box(spi, 10, 50, 105, 120, temp_color, "SICAKLIK", temp_val,
                      temp_status);

      // ==========================================
      // 2. GAZ DURUM KUTUSU (MQ-135) - Üst Sağ
      // ==========================================
      if (local_data.gas_raw > 1200) {
        gas_color = COLOR_CRITICAL;
        strcpy(gas_status, "GAZ: ALARM");
      } else if (local_data.gas_raw >= 800) {
        gas_color = COLOR_WARNING;
        strcpy(gas_status, "GAZ: YUKSEK");
      } else {
        gas_color = COLOR_SAFE;
        strcpy(gas_status, "GAZ: TEMIZ");
      }

      snprintf(gas_val, sizeof(gas_val), "%d PPM", local_data.gas_raw);
      draw_sensor_box(spi, 125, 50, 105, 120, gas_color, "GAZ SEVIYE", gas_val,
                      gas_status);

      // ==========================================
      // 3. ARK SESİ DURUM KUTUSU (INMP441) - Alt Sol
      // ==========================================
      if (local_data.audio_level > 2500) {
        sound_color = COLOR_CRITICAL;
        strcpy(sound_status, "SES: ALARM");
      } else if (local_data.audio_level >= 1000) {
        sound_color = COLOR_WARNING;
        strcpy(sound_status, "SES: UYARI");
      } else {
        sound_color = COLOR_SAFE;
        strcpy(sound_status, "SES: SAKIN");
      }

      snprintf(sound_val, sizeof(sound_val), "ARK: %d", local_data.audio_level);
      draw_sensor_box(spi, 10, 185, 105, 125, sound_color, "AKUSTIK", sound_val,
                      sound_status);

      // ==========================================
      // 4. HAREKET DURUM KUTUSU (PIR) - Alt Sağ
      // ==========================================
      if (local_data.motion_detected == 1) {
        motion_color = COLOR_WARNING;
        strcpy(motion_val, "TESPIT: VAR");
        strcpy(motion_status, "HRKT UYARI");
      } else {
        motion_color = COLOR_SAFE;
        strcpy(motion_val, "TESPIT: YOK");
        strcpy(motion_status, "HRKT YOK");
      }

      draw_sensor_box(spi, 125, 185, 105, 125, motion_color, "HAREKET",
                      motion_val, motion_status);
    }

    // Ekran güncelleme sıklığı (1 saniye)
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

// ==========================================
// 7. ESP32 SESLİ KOMUT GÖREVİ
// BOOT butonu (GPIO0) basılı tutularak konuşulur,
// bırakıldığında sunucuya gönderilir ve yanıt hoparlörde çalar.
// ==========================================

#define BOOT_BTN_PIN GPIO_NUM_0
#define VOICE_SAMPLE_RATE 16000
#define VOICE_MAX_SAMPLES (VOICE_SAMPLE_RATE * 4) // En fazla 4 saniye kayıt
#define VOICE_MAX_BYTES (VOICE_MAX_SAMPLES * 2)   // 16-bit PCM → 2 byte/örnek

void esp32_voice_task(void *pvParameters) {
  gpio_reset_pin(BOOT_BTN_PIN);
  gpio_set_direction(BOOT_BTN_PIN, GPIO_MODE_INPUT);
  // GPIO0 dahili pull-up direnci var, aktif LOW

  printf("[VOICE] Hazır. BOOT butonuna basılı tutarak konuşun.\n");

  while (1) {
    // BOOT butonu basılı (aktif LOW) ve WiFi bağlı mı?
    if (gpio_get_level(BOOT_BTN_PIN) != 0 || !s_wifi_connected) {
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }

    // Debounce
    vTaskDelay(pdMS_TO_TICKS(50));
    if (gpio_get_level(BOOT_BTN_PIN) != 0)
      continue;

    // WebSocket baglanti kontrolu
    if (!ws_client_is_connected()) {
      printf("[VOICE] WebSocket hazir degil, bekleniyor...\n");
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }

    if (mic_sem && xSemaphoreTake(mic_sem, pdMS_TO_TICKS(1000)) == pdTRUE) {
      // Buton basildiginda pcm_buf'i dinamik olarak tahsis et (SRAM korumasi!)
      int16_t *pcm_buf = malloc(VOICE_MAX_BYTES);
      if (!pcm_buf) {
        printf("[VOICE] Kayit icin bellek tahsis hatasi!\n");
        xSemaphoreGive(mic_sem);
        vTaskDelay(pdMS_TO_TICKS(500));
        continue;
      }

      printf("[VOICE] Kayıt başladı (bırakınca gönderilir)...\n");
      s_recording = true;

      int32_t raw[64];
      size_t bytes_read = 0;
      int n_samples = 0;

      while (gpio_get_level(BOOT_BTN_PIN) == 0 && n_samples < VOICE_MAX_SAMPLES) {
        i2s_channel_read(rx_chan, raw, sizeof(raw), &bytes_read,
                         pdMS_TO_TICKS(100));
        int n = (int)(bytes_read / sizeof(int32_t));
        for (int i = 0; i < n && n_samples < VOICE_MAX_SAMPLES; i++) {
          // INMP441: 32-bit Philips format, gerçek veri üst 24 bitte
          pcm_buf[n_samples++] = (int16_t)(raw[i] >> 15);
        }
      }

      s_recording = false;
      float duration = (float)n_samples / VOICE_SAMPLE_RATE;
      printf("[VOICE] Kayıt bitti: %.1f sn (%d örnek)\n", duration, n_samples);

      if (n_samples < 1600) { // 0.1 saniyeden kısa → atla
        printf("[VOICE] Çok kısa, gönderilmiyor.\n");
        free(pcm_buf);
        xSemaphoreGive(mic_sem);
        vTaskDelay(pdMS_TO_TICKS(200));
        continue;
      }

      // ── Sunucuya Gönder (Şifresiz Ham PCM) ──────────────────
      printf("[VOICE] Ham ses sunucuya gonderiliyor (%d byte)...\n", n_samples * 2);
      esp_err_t send_err = ws_client_send_audio((const uint8_t *)pcm_buf, n_samples * 2);
      if (send_err != ESP_OK) {
        printf("[VOICE] Ses gonderimi basarisiz.\n");
      }

      free(pcm_buf); // Tahsis edilen pcm_buf'i serbest birakarak SRAM'i tamamen bosalt!
      xSemaphoreGive(mic_sem);
    } else {
      printf("[VOICE] Mikrofon kilidi alinamadi!\n");
    }
    vTaskDelay(pdMS_TO_TICKS(200));
  }
}

// ==========================================
// 8. WiFi OLAY İŞLEYİCİ VE İLKLENDİRME
// ==========================================

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id,
                               void *data) {
  if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
    esp_wifi_connect();
  } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
    s_wifi_connected = false;
    printf("[WiFi] Bağlantı kesildi, yeniden deneniyor...\n");
    esp_wifi_connect(); // Event handler içinde vTaskDelay çağrılmamalı
  } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
    s_wifi_connected = true;
    ip_event_got_ip_t *event = (ip_event_got_ip_t *)data;
    printf("[WiFi] Bağlandı! IP: " IPSTR "\n", IP2STR(&event->ip_info.ip));

    // WebSocket istemcisini baslat
    static bool ws_started = false;
    if (!ws_started) {
      ws_client_init(on_audio_received, on_text_received);
      ws_started = true;
    }
  }
}

static void init_wifi(void) {
  esp_err_t ret = nvs_flash_init();
  if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
      ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    nvs_flash_erase();
    nvs_flash_init();
  }
  esp_netif_init();
  esp_event_loop_create_default();
  esp_netif_create_default_wifi_sta();

  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  esp_wifi_init(&cfg);

  esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler,
                             NULL);
  esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler,
                             NULL);

  wifi_config_t wifi_cfg = {
      .sta =
          {
              .ssid = WIFI_SSID,
              .password = WIFI_PASS,
          },
  };
  esp_wifi_set_mode(WIFI_MODE_STA);
  esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
  esp_wifi_start();
  printf("[WiFi] Ağa bağlanılıyor: %s\n", WIFI_SSID);
}

// ==========================================
// 9. HTTP SENSOR VERİSİ GÖNDERIM GÖREVİ
// ==========================================

void wifi_data_task(void *pvParameters) {
  char json_buf[160];
  char url[96];
  snprintf(url, sizeof(url), "http://%s:%d/api/sensor-data", SERVER_HOST,
           SERVER_PORT);

  while (1) {
    if (s_wifi_connected) {
      taskENTER_CRITICAL(&data_mux);
      sensor_data_t snap = current_data;
      taskEXIT_CRITICAL(&data_mux);

      snprintf(json_buf, sizeof(json_buf),
               "{\"temperature\":%.2f,\"gas_raw\":%d,\"audio_level\":%d,"
               "\"motion_detected\":%d}",
               snap.temperature, snap.gas_raw, snap.audio_level,
               snap.motion_detected);

      esp_http_client_config_t config = {
          .url = url,
          .method = HTTP_METHOD_POST,
      };
      esp_http_client_handle_t client = esp_http_client_init(&config);
      esp_http_client_set_header(client, "Content-Type", "application/json");
      esp_http_client_set_post_field(client, json_buf, strlen(json_buf));

      esp_err_t err = esp_http_client_perform(client);
      if (err == ESP_OK) {
        printf("[HTTP] Sensör verisi gönderildi: %s\n", json_buf);
      } else {
        printf("[HTTP] Gönderim hatası: %s\n", esp_err_to_name(err));
      }
      esp_http_client_cleanup(client);
    }
    vTaskDelay(pdMS_TO_TICKS(5000)); // Her 5 saniyede bir gönder
  }
}

// ==========================================
// 10. ANA BAŞLANGIÇ NOKTASI (APP_MAIN)
// ==========================================
void app_main(void) {
  printf("\n=========================================\n");
  printf("NEURO-SENTINEL MULTITASKING HASSAS SISTEM BASLATILIYOR...\n");
  printf("=========================================\n");

  // Ekran GPIO Yapılandırması
  gpio_reset_pin(PIN_NUM_DC);
  gpio_set_direction(PIN_NUM_DC, GPIO_MODE_OUTPUT);
  gpio_reset_pin(PIN_NUM_RST);
  gpio_set_direction(PIN_NUM_RST, GPIO_MODE_OUTPUT);

  // SPI Veriyolu İlklendir
  spi_bus_config_t buscfg = {.miso_io_num = PIN_NUM_MISO,
                             .mosi_io_num = PIN_NUM_MOSI,
                             .sclk_io_num = PIN_NUM_CLK,
                             .quadwp_io_num = -1,
                             .quadhd_io_num = -1,
                             .max_transfer_sz = 320 * 240 * 2};

  esp_err_t ret = spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO);
  if (ret != ESP_OK) {
    printf("[HATA] SPI Veriyolu başlatılamadı: %d\n", ret);
    return;
  }

  // LCD SPI Arayüzü
  spi_device_interface_config_t devcfg = {
      .clock_speed_hz = 2 * 1000 * 1000,
      .mode = 0,
      .spics_io_num = PIN_NUM_CS,
      .queue_size = 7,
  };

  ret = spi_bus_add_device(SPI2_HOST, &devcfg, &spi_lcd);
  if (ret != ESP_OK) {
    printf("[HATA] Ekran SPI cihazı eklenemedi: %d\n", ret);
    return;
  }

  // Ekranı başlat
  lcd_init(spi_lcd);
  printf("[OK] Ekran başarıyla kuruldu.\n");

  // Amplifikatörü başlat ve açılış bip sesi çal
  init_amplifier();
  play_alarm_tone();

  // --- FREERTOS PARALEL GÖREVLERİN OLUŞTURULMASI ---

  // 1. Hızlı Sensör Okuma Görevi (Öncelik: 5, Core 1)
  xTaskCreatePinnedToCore(sensor_reading_task, "sensor_reading_task", 4096,
                          NULL, 5, NULL, 1);

  // 2. Bağımsız Sıcaklık Okuma Görevi (Öncelik: 4, Core 1)
  xTaskCreatePinnedToCore(temp_reading_task, "temp_reading_task", 4096, NULL, 4,
                          NULL, 1);

  // 3. Ekran Güncelleme Görevi (Öncelik: 4, Core 0)
  xTaskCreatePinnedToCore(lcd_display_task, "lcd_display_task", 4096,
                          (void *)spi_lcd, 4, NULL, 0);

  // --- WiFi, SESLI KOMUT VE HTTP VERİ GÖNDERIM GÖREVLERİ ---
  init_wifi();

  // 4. ESP32 Sesli Komut Görevi (Öncelik: 5, Core 0) — BOOT butonu ile
  // tetiklenir
  xTaskCreatePinnedToCore(esp32_voice_task, "esp32_voice_task",
                          8192, // WAV streaming için büyük stack
                          NULL, 5, NULL, 0);

  // 5. WiFi Sensör Veri Gönderim Görevi (Öncelik: 3, Core 0)
  xTaskCreatePinnedToCore(wifi_data_task, "wifi_data_task", 4096, NULL, 3, NULL,
                          0);

  // 6. Alarm Ses Görevi (Öncelik: 3, Core 0) — alarm_active flag'ini izler
  xTaskCreatePinnedToCore(alarm_sound_task, "alarm_sound_task", 2048, NULL, 3,
                          NULL, 0);

  printf("[SİSTEM] Tüm paralel alt görevler kararlı şekilde başlatıldı.\n");
  printf("[SİSTEM] BOOT butonuna (GPIO0) basılı tutarak ESP32 mikrofonuyla "
         "konuşabilirsiniz.\n");
}