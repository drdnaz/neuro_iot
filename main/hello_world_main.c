#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"

// PIR Sensörü pini tablonuza göre GPIO 6
#define PIR_PIN GPIO_NUM_6 

void app_main(void)
{
    // Pini giriş olarak ayarlıyoruz
    gpio_reset_pin(PIR_PIN);
    gpio_set_direction(PIR_PIN, GPIO_MODE_INPUT);

    printf("Sistem Baslatiliyor... ESP-IDF PIR Testi Aktif!\n");

    // Arduino'daki loop() mantığını FreeRTOS döngüsü ile kuruyoruz
    while (1) {
        int pir_state = gpio_get_level(PIR_PIN);
        
        if (pir_state == 1) {
            printf("ALARM: Hareket Algilandi!\n");
        } else {
            printf("Stabil: Hareket Yok.\n");
        }
        
        // İşlemcinin çökmemesi için bekleme (Delay) komutu - 500 ms
        vTaskDelay(500 / portTICK_PERIOD_MS);
    }
}