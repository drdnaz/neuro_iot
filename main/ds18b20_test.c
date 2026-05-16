#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "rom/ets_sys.h"

#define DS_PIN GPIO_NUM_4

// --- 1-Wire Protokol Fonksiyonları ---
int ds_reset() {
    gpio_set_direction(DS_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(DS_PIN, 0);
    ets_delay_us(480);
    gpio_set_direction(DS_PIN, GPIO_MODE_INPUT);
    ets_delay_us(70);
    int presence = gpio_get_level(DS_PIN);
    ets_delay_us(410);
    return presence;
}

void ds_write_bit(int b) {
    gpio_set_direction(DS_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(DS_PIN, 0);
    ets_delay_us(b ? 5 : 60);
    gpio_set_direction(DS_PIN, GPIO_MODE_INPUT);
    ets_delay_us(b ? 60 : 5);
}

int ds_read_bit() {
    gpio_set_direction(DS_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(DS_PIN, 0);
    ets_delay_us(2);
    gpio_set_direction(DS_PIN, GPIO_MODE_INPUT);
    ets_delay_us(10);
    int b = gpio_get_level(DS_PIN);
    ets_delay_us(50);
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
        if (ds_read_bit()) val |= (1 << i);
    }
    return val;
}

// --- Ana Program ---
void app_main() {
    printf("NEURO-SENTINEL: Termal Gozetmen Aktif Ediliyor...\n");

    while(1) {
        if (ds_reset() == 0) { // Sensör buradaysa
            // 1. Ölçüm Komutu Gönder
            ds_write_byte(0xCC); // Rom kodunu atla (Tek sensör var)
            ds_write_byte(0x44); // Sıcaklığı dönüştür
            
            // Sensörün ölçümü yapması için 750ms beklemesi gerekir
            vTaskDelay(pdMS_TO_TICKS(750)); 

            // 2. Veriyi Oku
            ds_reset();
            ds_write_byte(0xCC);
            ds_write_byte(0xBE); // Hafızayı oku (Scratchpad)

            int lsb = ds_read_byte(); // Alt bayt
            int msb = ds_read_byte(); // Üst bayt

            // 3. Veriyi Santigrata Çevir (DS18B20 Formülü)
            float temp = ((msb << 8) | lsb) / 16.0;
            
            printf("TERMAL ANALİZ -> Guncel Sicaklik: %.2f C\n", temp);
        } else {
            printf("DURUM: [HATA] Sensor okunmuyor. Direnc veya kablo koptu!\n");
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }
}