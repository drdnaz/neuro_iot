idf_component_register(SRCS "neuro_sentinel_main.c" "crypto.c" "ws_client.c"
                    INCLUDE_DIRS "."
                    REQUIRES driver esp_adc esp_lcd
                              nvs_flash esp_netif esp_event esp_wifi
                              esp_http_client esp_websocket_client mbedtls)