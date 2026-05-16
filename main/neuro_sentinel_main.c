#include <stdio.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "rom/ets_sys.h"
#include "driver/i2s_std.h"
#include "driver/spi_master.h"

// --- SENSÖR PİN TANIMLAMALARI ---
#define PIR_PIN GPIO_NUM_1
#define GAS_CHANNEL ADC_CHANNEL_1 // GPIO 2
#define DS_PIN GPIO_NUM_4

#define I2S_SD  GPIO_NUM_5
#define I2S_SCK GPIO_NUM_6
#define I2S_WS  GPIO_NUM_7

// --- YENİ EKRAN PİNLERİ (GÜNCELLENDİ) ---
#define PIN_NUM_CS   10
#define PIN_NUM_RST  8
#define PIN_NUM_DC   9
#define PIN_NUM_MOSI 11 // SDI pini buraya bağlandı
#define PIN_NUM_CLK  13 // SDK/SCK pini buraya bağlandı

i2s_chan_handle_t rx_chan;
spi_device_handle_t spi;

// --- 1. EKRAN (ILI9341) FONKSİYONLARI ---
void lcd_cmd(const uint8_t cmd) {
    gpio_set_level(PIN_NUM_DC, 0);
    spi_transaction_t t = { .length = 8, .tx_buffer = &cmd };
    spi_device_polling_transmit(spi, &t);
}

void lcd_data(const uint8_t data) {
    gpio_set_level(PIN_NUM_DC, 1);
    spi_transaction_t t = { .length = 8, .tx_buffer = &data };
    spi_device_polling_transmit(spi, &t);
}
void init_lcd() {
    spi_bus_config_t buscfg = {
        .miso_io_num = -1, 
        .mosi_io_num = PIN_NUM_MOSI, 
        .sclk_io_num = PIN_NUM_CLK,
        .quadwp_io_num = -1, 
        .quadhd_io_num = -1, 
        .max_transfer_sz = 320*240*2+8
    };
    // Çakışmayı önlemek için SPI3_HOST (SPI3) kanalına geçiş yapıyoruz
    spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO);
    
    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = 10*1000*1000, // 10 MHz
        .mode = 0, 
        .spics_io_num = PIN_NUM_CS, 
        .queue_size = 7
    };
    spi_bus_add_device(SPI3_HOST, &devcfg, &spi);
    
    gpio_set_direction(PIN_NUM_DC, GPIO_MODE_OUTPUT);
    gpio_set_direction(PIN_NUM_RST, GPIO_MODE_OUTPUT);
    
    // Güvenli Donanımsal Reset Akışı
    gpio_set_level(PIN_NUM_RST, 0); 
    vTaskDelay(pdMS_TO_TICKS(150));
    gpio_set_level(PIN_NUM_RST, 1); 
    vTaskDelay(pdMS_TO_TICKS(200));
    
    // Geliştirilmiş Uyanış Reçetesi
    lcd_cmd(0x01); vTaskDelay(pdMS_TO_TICKS(200)); // Yazılımsal Reset
    lcd_cmd(0x11); vTaskDelay(pdMS_TO_TICKS(200)); // Sleep Out
    
    lcd_cmd(0x3A); lcd_data(0x55);                 // 16-bit RGB565 renk formatı
    lcd_cmd(0x36); lcd_data(0x28);                 // Ekranı Yatay Kullan
    vTaskDelay(pdMS_TO_TICKS(50));                 // Yön ayarının oturması için küçük bekleme
    
    lcd_cmd(0xC0); lcd_data(0x23);                 // Power Control 1
    lcd_cmd(0xC1); lcd_data(0x10);                 // Power Control 2
    lcd_cmd(0xC5); lcd_data(0x3E); lcd_data(0x28); // VCOM Control 1
    
    lcd_cmd(0x29); vTaskDelay(pdMS_TO_TICKS(200)); // Display ON
}

void fill_screen(uint16_t color) {
    lcd_cmd(0x2A); lcd_data(0); lcd_data(0); lcd_data(0x01); lcd_data(0x3F); // X Ekseni
    lcd_cmd(0x2B); lcd_data(0); lcd_data(0); lcd_data(0x00); lcd_data(0xEF); // Y Ekseni
    lcd_cmd(0x2C); // RAM'e Yazmaya Başla
    
    gpio_set_level(PIN_NUM_DC, 1);
    
    // DMA için devasa bir alan yerine, sadece 1 satırlık (320 piksel) küçük bir alan ayırıyoruz.
    uint16_t *buf = heap_caps_malloc(320 * 2, MALLOC_CAP_DMA); 
    if(!buf) {
        printf("[HATA] Ekran icin DMA bellegi ayrilamadi!\n");
        return;
    }
    
    uint16_t sw_color = (color >> 8) | (color << 8); 
    for(int i=0; i<320; i++) buf[i] = sw_color;
    
    // Ekranı 240 satır boyunca tek tek tara ve boya
    for(int y=0; y<240; y++) { 
        spi_transaction_t t = { .length = 320 * 16, .tx_buffer = buf };
        spi_device_polling_transmit(spi, &t);
    }
    free(buf);
    
    printf("[BİLGİ] Ekrana renk basma komutu gonderildi.\n");
}

// --- 2. SENSÖR FONKSİYONLARI ---
void init_microphone() {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    i2s_new_channel(&chan_cfg, NULL, &rx_chan);
    i2s_std_config_t std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = { .mclk = I2S_GPIO_UNUSED, .bclk = I2S_SCK, .ws = I2S_WS, .dout = I2S_GPIO_UNUSED, .din = I2S_SD,
                      .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false } },
    };
    i2s_channel_init_std_mode(rx_chan, &std_cfg); i2s_channel_enable(rx_chan);
}

int get_audio_level() {
    int32_t raw_samples[64]; size_t bytes_read = 0;
    i2s_channel_read(rx_chan, raw_samples, sizeof(raw_samples), &bytes_read, portMAX_DELAY);
    int samples_read = bytes_read / sizeof(int32_t); int64_t total_energy = 0;
    for(int i = 0; i < samples_read; i++) total_energy += abs(raw_samples[i] >> 12);
    return (int)(total_energy / samples_read);
}

int ds_reset() {
    gpio_set_direction(DS_PIN, GPIO_MODE_OUTPUT); gpio_set_level(DS_PIN, 0); ets_delay_us(480);
    gpio_set_direction(DS_PIN, GPIO_MODE_INPUT); ets_delay_us(70);
    int presence = gpio_get_level(DS_PIN); ets_delay_us(410); return presence;
}
void ds_write_bit(int b) {
    gpio_set_direction(DS_PIN, GPIO_MODE_OUTPUT); gpio_set_level(DS_PIN, 0); ets_delay_us(b ? 5 : 60);
    gpio_set_direction(DS_PIN, GPIO_MODE_INPUT); ets_delay_us(b ? 60 : 5);
}
int ds_read_bit() {
    gpio_set_direction(DS_PIN, GPIO_MODE_OUTPUT); gpio_set_level(DS_PIN, 0); ets_delay_us(2);
    gpio_set_direction(DS_PIN, GPIO_MODE_INPUT); ets_delay_us(10);
    int b = gpio_get_level(DS_PIN); ets_delay_us(50); return b;
}
void ds_write_byte(int val) {
    for (int i = 0; i < 8; i++) { ds_write_bit(val & 0x01); val >>= 1; }
}
int ds_read_byte() {
    int val = 0; for (int i = 0; i < 8; i++) { if (ds_read_bit()) val |= (1 << i); } return val;
}
float read_temperature() {
    if (ds_reset() == 0) {
        ds_write_byte(0xCC); ds_write_byte(0x44); vTaskDelay(pdMS_TO_TICKS(750)); 
        ds_reset(); ds_write_byte(0xCC); ds_write_byte(0xBE);
        int lsb = ds_read_byte(); int msb = ds_read_byte();
        return ((msb << 8) | lsb) / 16.0;
    }
    return -99.0; 
}

// --- ANA PROGRAM (OMURİLİK) ---
void app_main(void)
{
    printf("NEURO-SENTINEL: Tum Donanimlar ve Ekran Baslatiliyor...\n");

    // Ekranı Başlat ve Yeşil (Güvenli) Yap
    init_lcd();
    fill_screen(0x07E0); // Yeşil

    // Sensörleri Başlat
    gpio_reset_pin(PIR_PIN); gpio_set_direction(PIR_PIN, GPIO_MODE_INPUT);
    adc_oneshot_unit_handle_t adc1_handle;
    adc_oneshot_unit_init_cfg_t init_config1 = { .unit_id = ADC_UNIT_1 };
    adc_oneshot_new_unit(&init_config1, &adc1_handle);
    adc_oneshot_chan_cfg_t config = { .bitwidth = ADC_BITWIDTH_DEFAULT, .atten = ADC_ATTEN_DB_12 };
    adc_oneshot_config_channel(adc1_handle, GAS_CHANNEL, &config);
    init_microphone();

    int last_crisis_state = 0;

    while (1) {
        int pir_val = gpio_get_level(PIR_PIN);
        int gas_raw = 0; adc_oneshot_read(adc1_handle, GAS_CHANNEL, &gas_raw);
        int audio_lvl = get_audio_level();
        float temp = read_temperature(); 

        if(temp != -99.0) {
            printf("[VERİ] ISI: %.2f C | GAZ: %4d | SES (ARK): %4d | HAREKET: %d \n", temp, gas_raw, audio_lvl, pir_val);
            
            int current_crisis_state = 0;
            if (gas_raw > 1500 || temp > 35 || audio_lvl > 3000) {
                current_crisis_state = 1;
            }

            if (current_crisis_state != last_crisis_state) {
                if (current_crisis_state == 1) {
                    fill_screen(0xF800); // Kırmızı (Tehlike)
                } else {
                    fill_screen(0x07E0); // Yeşil (Stabil)
                }
                last_crisis_state = current_crisis_state;
            }
        } else {
            printf("ISI SENSOR HATASI! (4.7k Direnci ve baglantilari kontrol edin)\n");
        }
    }
}