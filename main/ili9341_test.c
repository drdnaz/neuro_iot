#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"

#define PIN_NUM_MISO -1
#define PIN_NUM_MOSI 11
#define PIN_NUM_CLK  12  // Fiziksel bağlantıya göre GPIO 12 olarak düzeltildi
#define PIN_NUM_CS   10
#define PIN_NUM_DC   9
#define PIN_NUM_RST  8

spi_device_handle_t spi;

// Komut Gönderme Fonksiyonu (D/C = 0)
void lcd_cmd(spi_device_handle_t spi, const uint8_t cmd) {
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.length = 8;
    t.tx_buffer = &cmd;
    gpio_set_level(PIN_NUM_DC, 0); // Komut modu (D/C = Low)
    spi_device_polling_transmit(spi, &t);
}

// Veri Gönderme Fonksiyonu (D/C = 1)
void lcd_data(spi_device_handle_t spi, const uint8_t *data, int len) {
    if (len == 0) return;
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.length = len * 8;
    t.tx_buffer = data;
    gpio_set_level(PIN_NUM_DC, 1); // Veri modu (D/C = High)
    spi_device_polling_transmit(spi, &t);
}

// Tek Bayt Veri Gönderme Yardımcı Fonksiyonu
void lcd_data_byte(spi_device_handle_t spi, const uint8_t data) {
    lcd_data(spi, &data, 1);
}

// Donanımsal Reset (Sıfırlama)
void lcd_reset() {
    gpio_set_level(PIN_NUM_RST, 0);
    vTaskDelay(pdMS_TO_TICKS(100));
    gpio_set_level(PIN_NUM_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(100));
}

// ILI9341 Sürücüsü Temel Başlatma Komutları
void lcd_init(spi_device_handle_t spi) {
    lcd_reset();

    // Sürücü çipini uyandır ve temel ayarları yap
    lcd_cmd(spi, 0x01); // Software Reset
    vTaskDelay(pdMS_TO_TICKS(150));

    lcd_cmd(spi, 0x28); // Display OFF
    
    // Temel güç ve arayüz kontrolleri
    lcd_cmd(spi, 0xC0); // Power Control 1
    uint8_t pwr1[] = {0x23}; // 4.6V
    lcd_data(spi, pwr1, 1);

    lcd_cmd(spi, 0xC1); // Power Control 2
    uint8_t pwr2[] = {0x10}; // SAP[2:0];BT[3:0]
    lcd_data(spi, pwr2, 1);

    lcd_cmd(spi, 0xC5); // VCOM Control 1
    uint8_t vcom1[] = {0x3E, 0x28};
    lcd_data(spi, vcom1, 2);

    lcd_cmd(spi, 0xC7); // VCOM Control 2
    uint8_t vcom2[] = {0x86};
    lcd_data(spi, vcom2, 1);

    // Hafıza ve Görünüm Kontrolleri
    lcd_cmd(spi, 0x36); // Memory Access Control
    uint8_t mac[] = {0x08}; // BGR formatı
    lcd_data(spi, mac, 1);

    // Renk Modu Seçimi: 16-bit/pixel (RGB565)
    lcd_cmd(spi, 0x3A); 
    lcd_data_byte(spi, 0x55); 

    // Ekran Tarama Yönü
    lcd_cmd(spi, 0xB1); // Frame Rate Control
    uint8_t frc[] = {0x00, 0x18}; // 79Hz
    lcd_data(spi, frc, 2);

    lcd_cmd(spi, 0xB6); // Display Function Control
    uint8_t dfc[] = {0x08, 0x82, 0x27};
    lcd_data(spi, dfc, 3);

    // Uykudan Çıkış ve Ekranı Açış
    lcd_cmd(spi, 0x11); // Sleep Out
    vTaskDelay(pdMS_TO_TICKS(150));

    lcd_cmd(spi, 0x29); // Display ON
    vTaskDelay(pdMS_TO_TICKS(50));
}

// Ekranı Belirli Bir Renkle Boyama
void lcd_clear(spi_device_handle_t spi, uint16_t color) {
    // Çizim sınırlarını belirle (320x240)
    // Sütun aralığı: 0-239
    lcd_cmd(spi, 0x2A);
    uint8_t col_data[] = {0, 0, 0, 239};
    lcd_data(spi, col_data, 4);

    // Satır aralığı: 0-319
    lcd_cmd(spi, 0x2B);
    uint8_t page_data[] = {0, 0, 1, 63}; // 319 = 0x013F
    lcd_data(spi, page_data, 4);

    // Belleğe Yazma Komutu (Memory Write)
    lcd_cmd(spi, 0x2C);

    // Renk verilerini satır satır gönderelim
    // Bellek tasarrufu için tek seferde bir satır (240 piksel) tamponluyoruz
    uint16_t line_buffer[240];
    uint8_t *byte_ptr = (uint8_t *)line_buffer;
    
    // SPI big-endian gönderdiği için renk baytlarını yüksek-düşük olarak yerleştirelim
    uint8_t high_byte = color >> 8;
    uint8_t low_byte = color & 0xFF;
    for (int i = 0; i < 240; i++) {
        byte_ptr[i * 2] = high_byte;
        byte_ptr[i * 2 + 1] = low_byte;
    }

    for (int y = 0; y < 320; y++) {
        lcd_data(spi, byte_ptr, 240 * 2);
    }
}

void app_main(void)
{
    printf("=========================================\n");
    printf("ILI9341 EKAN TESTI: Donanim Baslatiliyor\n");
    printf("Fiziksel Pin Haritasi (PROJECT_STATUS tablosuna gore):\n");
    printf(" - CLK  (SCK): GPIO %d\n", PIN_NUM_CLK);
    printf(" - MOSI (SDI): GPIO %d\n", PIN_NUM_MOSI);
    printf(" - CS   (Chip Select): GPIO %d\n", PIN_NUM_CS);
    printf(" - DC   (Data/Command): GPIO %d\n", PIN_NUM_DC);
    printf(" - RST  (Reset): GPIO %d\n", PIN_NUM_RST);
    printf("=========================================\n");

    // 1. DC ve RST pinlerini GPIO çıkış olarak ayarla
    gpio_reset_pin(PIN_NUM_DC);
    gpio_set_direction(PIN_NUM_DC, GPIO_MODE_OUTPUT);
    gpio_reset_pin(PIN_NUM_RST);
    gpio_set_direction(PIN_NUM_RST, GPIO_MODE_OUTPUT);

    // 2. SPI Veriyolu Yapılandırması
    spi_bus_config_t buscfg = {
        .miso_io_num = PIN_NUM_MISO,
        .mosi_io_num = PIN_NUM_MOSI,
        .sclk_io_num = PIN_NUM_CLK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 320 * 240 * 2
    };

    esp_err_t ret = spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if (ret == ESP_OK) {
        printf("[OK] SPI Veriyolu basariyla kuruldu.\n");
    } else {
        printf("[HATA] SPI Veriyolu baslatilamadi: %d\n", ret);
        return;
    }

    // 3. Ekran Cihazını SPI Veriyoluna Ekle
    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = 10 * 1000 * 1000, // 10 MHz (Genellikle breadboard uzerinde en kararli hiz)
        .mode = 0,                         // SPI modu 0
        .spics_io_num = PIN_NUM_CS,        // CS Pin
        .queue_size = 7,                   // Kuyruk derinligi
    };

    ret = spi_bus_add_device(SPI2_HOST, &devcfg, &spi);
    if (ret == ESP_OK) {
        printf("[OK] ILI9341 SPI cihazi veriyoluna eklendi.\n");
    } else {
        printf("[HATA] SPI Cihazi eklenemedi: %d\n", ret);
        return;
    }

    // 4. Ekranı Başlat
    printf("Ekran ulandiriliyor ve baslatma komutlari gonderiliyor...\n");
    lcd_init(spi);
    printf("[OK] Ekran basariyla kuruldu. Renk dongusu basliyor!\n");

    // Renk tanımlamaları (RGB565 formatında)
    uint16_t RED   = 0xF800;
    uint16_t GREEN = 0x07E0;
    uint16_t BLUE  = 0x001F;

    while (1) {
        printf(">> Ekran KIRMIZI yapiliyor...\n");
        lcd_clear(spi, RED);
        vTaskDelay(pdMS_TO_TICKS(2000));

        printf(">> Ekran YESIL yapiliyor...\n");
        lcd_clear(spi, GREEN);
        vTaskDelay(pdMS_TO_TICKS(2000));

        printf(">> Ekran MAVI yapiliyor...\n");
        lcd_clear(spi, BLUE);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}