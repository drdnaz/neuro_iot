#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"

// Takım arkadaşının tablosundaki pin eşleşmeleri 
#define PIN_NUM_MOSI 11
#define PIN_NUM_CLK  12
#define PIN_NUM_CS   10
#define PIN_NUM_DC   9
#define PIN_NUM_RST  8
#define PIN_NUM_BCKL -1 // Tabloda 3.3V'a bağlı, kodla yönetilmiyor 

void app_main(void)
{
    printf("ILI9341 EKAN TESTI: Baslatiliyor...\n");

    // 1. SPI Veriyolu Konfigürasyonu
    spi_bus_config_t buscfg = {
        .miso_io_num = -1, // Veri sadece ESP'den ekrana akıyor 
        .mosi_io_num = PIN_NUM_MOSI,
        .sclk_io_num = PIN_NUM_CLK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 320 * 240 * 2
    };

    // SPI'ı başlat
    esp_err_t ret = spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if (ret == ESP_OK) {
        printf("SPI Veriyolu Basariyla Kuruldu. [cite: 65]\n");
    }

    // 2. DC ve Reset Pinlerini Hazırla
    gpio_reset_pin(PIN_NUM_DC);
    gpio_set_direction(PIN_NUM_DC, GPIO_MODE_OUTPUT);
    gpio_reset_pin(PIN_NUM_RST);
    gpio_set_direction(PIN_NUM_RST, GPIO_MODE_OUTPUT);

    // 3. Donanımsal Reset (Ekranı uyandır)
    gpio_set_level(PIN_NUM_RST, 0);
    vTaskDelay(pdMS_TO_TICKS(100));
    gpio_set_level(PIN_NUM_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(100));

    printf("Ekran Resetlendi. SPI Sinyalleri Aktif. [cite: 57]\n");

    while(1) {
        printf("EKRAN DURUMU: Enerji Var, SPI Beklemede...\n");
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}